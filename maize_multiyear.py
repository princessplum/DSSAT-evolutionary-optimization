"""Optimize ONE maize fertilizer schedule across a selectable set of weather years.

Place this file in your project beside templates/ and weather/. Requires numpy and matplotlib
and an already working DSSAT 4.8 installation with your soil and cultivar files.
This is a separate driver based on your supplied Differential Evolution code.

Start with:
    python maize_multiyear.py --mode prepare --years 2021 2022 2023
    python maize_multiyear.py --mode test --years 2021 2022 2023
    python maize_multiyear.py --mode optimize --years 2021 2022 2023 --population 20 --generations 3

Windows defaults: --dssat-root C:/DSSAT48 --exe C:/DSSAT48/DSCSM048.EXE
Linux example: add --dssat-root /your/dssat-runtime --exe /your/dscsm048
Executable arguments default to MZCER048 B <batch>; --omit-model supports a
build whose existing working command uses just B <batch>.

Five years: --years 2019:2023. Thirty-year EXAMPLE: --years 1995:2024.
The colon range is inclusive. Use the same fixed year set for all candidates.
--schedule 150:50,170:100 means 50 kg N/ha on DOY 150 and 100 on DOY 170.
Schedules and template management dates retain the SAME DOY in leap years,
so the calendar date after February can differ by a day. This is not a rotation:
each year starts independently with the template's initial soil conditions.

Objective = mean(0.5 * HARWT / 13000 - 0.5 * planned_N / 300).
All years have equal weight; variability is reported but not penalized.
HARWT is read from Summary.OUT's HWAH (harvest yield), the same quantity printed
as HARWT in the supplied code's console parser. HWAM is yield at maturity.
The original template's physical/model settings, including CO2 mode, are kept.
Daily outputs are disabled by default for speed; --daily-outputs retains them.
There is no soil-N balance calculation or inferred total-loss metric here.

Results: output/multiyear/<timestamp>/, including candidate_results.csv,
yearly_results.csv, convergence.csv, best.json, best_by_year.csv, and metadata.
After test/optimize finishes, PNG/PDF figures and a compact summary table are
saved automatically in that run's plots/ directory. No separate reporting
script is required. Use --no-plots to skip figure generation.
Each new best is saved immediately; this is a results checkpoint, not a
resumable optimizer. The final best is rerun to save matching raw DSSAT outputs.
Parent scores and repeated schedules are reused within one invocation only.

The script uses the selected DSSAT root as its working directory, as your
original code does. Run only one optimizer in that runtime at a time. Parallel
cluster jobs each need their own DSSAT runtime and output directory.

Validation performed when delivered: real supplied template/weather files,
synthetic Summary.OUT fixtures, and a simulated executable integration test.
A real DSSAT executable was unavailable in the preparation environment; first
compare --years 2021 --mode test with your existing single-year code using the
same schedule, weather file, and DSSAT installation.
"""

import argparse
import calendar
import csv
import hashlib
import io
import json
import math
import random
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np


def write_csv(path, rows, append=False):
    rows = list(rows)
    if not rows:
        return
    path = Path(path)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a" if append else "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_years(tokens):
    years = []
    for token in tokens:
        if ":" in token:
            first, last = map(int, token.split(":"))
            if first > last:
                raise ValueError("Year ranges must be ascending.")
            years.extend(range(first, last + 1))
        else:
            years.append(int(token))
    if not years or len(set(years)) != len(years):
        raise ValueError("Provide distinct weather years.")
    if any(year < 1990 or year > 2025 for year in years):
        raise ValueError(
            "This driver is configured for your 1990-2025 weather dataset."
        )
    return tuple(sorted(years))


def parse_schedule(text):
    return sorted(tuple(map(int, pair.split(":"))) for pair in text.split(","))


def check_weather(path, year):
    """Require complete, nonmissing daily weather for the stated year."""
    seen = set()
    expected_days = 366 if calendar.isleap(year) else 365
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        fields = line.split()
        if len(fields) < 5 or not fields[0].isdigit():
            continue
        code = fields[0]
        date = int(code)
        observed_year = date // 1000
        if len(code) <= 5:
            observed_year += 1900 if observed_year >= 90 else 2000
        doy = date % 1000
        if observed_year != year or not 1 <= doy <= expected_days or doy in seen:
            raise ValueError(
                f"Wrong year, duplicate, or invalid weather date in {path}: {code}"
            )
        values = [float(x) for x in fields[1:5]]
        if any(not math.isfinite(x) or x <= -90 for x in values):
            raise ValueError(
                f"Missing/invalid SRAD, TMAX, TMIN, or RAIN in {path}: {code}"
            )
        if values[0] < 0 or values[3] < 0 or values[1] < values[2]:
            raise ValueError(
                f"Invalid radiation, rainfall, or temperature in {path}: {code}"
            )
        seen.add(doy)
    if seen != set(range(1, expected_days + 1)):
        raise ValueError(f"Incomplete daily weather in {path}: {len(seen)} records.")


DATE_FIELDS = {
    "SADAT",
    "ICDAT",
    "PDATE",
    "EDATE",
    "RDATE",
    "TDATE",
    "HDATE",
    "SDATE",
    "PFRST",
    "PLAST",
    "FDATE",
    "IDATE",
    "CDATE",
}


def replace_cell(line, start, width, value, left=False):
    text = str(value)
    if len(text) > width:
        raise ValueError(f"Value {text} exceeds a {width}-column DSSAT field.")
    line = line.ljust(start + width)
    text = text.ljust(width) if left else text.rjust(width)
    return line[:start] + text + line[start + width :]


def render_experiment(template, year, schedule, daily_outputs=False):
    """Target known fields; preserve fixed widths and unrelated numbers."""
    lines = template.replace("\x1a", "").splitlines()
    result = []
    header = ""
    in_fertilizer = False
    fertilizer_headers = 0
    seen_dates = set()
    seen_weather = 0
    for line in lines:
        if line.startswith("*EXP.DETAILS"):
            line = f"*EXP.DETAILS: UKLE{year % 100:02d}02MZ SHARED N SCHEDULE; WEATHER {year}"
        if line.startswith("*"):
            in_fertilizer = line.startswith("*FERTILIZERS")
            header = ""
        if in_fertilizer:
            if line.startswith("*"):
                result.append(line)
            elif line.startswith("@F"):
                fertilizer_headers += 1
                result.append(line)
                for doy, amount in schedule:
                    result.append(
                        f" 1 {year % 100:02d}{doy:03d} FE005 AP001"
                        f" {0:5d} {amount:5d} {0:5d} {0:5d} {0:5d} {0:5d} {-99:5d} UREA"
                    )
                result.append("")
            continue
        if line.startswith("@"):
            header = line
        elif header and line.strip() and not line.lstrip().startswith(("!", "*")):
            for field in re.finditer(r"\S+", header):
                key = field.group().rstrip(".")
                start = field.start()
                if key in DATE_FIELDS:
                    value = line[start : start + 5].strip()
                    if value and value != "-99":
                        if len(value) != 5 or not value.isdigit() or value[:2] != "21":
                            raise ValueError(
                                f"Expected a 2021 YYDDD template date at {key}: {value!r}"
                            )
                        doy = int(value[2:])
                        if not 1 <= doy <= 365:
                            raise ValueError(f"Invalid template date: {value}")
                        line = replace_cell(
                            line, start, 5, f"{year % 100:02d}{doy:03d}"
                        )
                        seen_dates.add(key)
                elif key == "WSTA":
                    line = replace_cell(
                        line, start, 8, f"UKLE{year % 100:02d}01", left=True
                    )
                    seen_weather += 1
                elif key in {"NYERS", "NREPS"}:
                    if line[start : start + 5].strip() != "1":
                        raise ValueError(
                            "Use a single-season, single-replicate template (NYERS=NREPS=1)."
                        )
                elif key in {"FNAME", "SUMRY", "FMOPT", "VBOSE"}:
                    value = {"FNAME": "N", "SUMRY": "Y", "FMOPT": "A", "VBOSE": "Y"}[
                        key
                    ]
                    line = replace_cell(line, start, 5, value)
                elif not daily_outputs and key in {
                    "OVVEW",
                    "GROUT",
                    "CAOUT",
                    "WAOUT",
                    "NIOUT",
                    "MIOUT",
                    "DIOUT",
                    "CHOUT",
                    "OPOUT",
                }:
                    line = replace_cell(line, start, 5, "N")
        result.append(line)
    if (
        fertilizer_headers != 1
        or seen_weather != 1
        or not {"SDATE", "PDATE", "ICDAT"} <= seen_dates
    ):
        raise ValueError(
            "Template must have one fertilizer level, one field, and SDATE/PDATE/ICDAT."
        )
    return "\n".join(result).rstrip() + "\n"


def read_summary(path, expected_years):
    """Read all batch runs using fixed column edges, including names with spaces."""
    fields = None
    records = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("@") and "RUNNO" in line and "HWAH" in line:
            fields = [
                (m.group().lstrip("@").rstrip("."), m.end())
                for m in re.finditer(r"\S+", line)
                if m.group() != "@"
            ]
            continue
        if not fields or not line.strip() or not line.lstrip()[0].isdigit():
            continue
        row = {}
        previous = 0
        for key, end in fields:
            row[key] = line[previous:end].strip()
            previous = end
        run = int(row["RUNNO"])
        if run in records or run not in range(1, len(expected_years) + 1):
            raise ValueError(f"Unexpected or repeated DSSAT run number: {run}")
        year = expected_years[run - 1]
        if row.get("EXNAME") != f"UKLE{year % 100:02d}02" or row.get("CR") != "MZ":
            raise ValueError(f"Run {run} has an unexpected experiment/crop: {row}")
        if int(row["TRNO"]) != 1 or int(row["PDAT"]) // 1000 != year:
            raise ValueError(f"Run {run} has an unexpected treatment or planting year.")
        if int(row["SDAT"]) // 1000 != year:
            raise ValueError(f"Run {run} has an unexpected simulation start year.")
        if "WYEAR" in row and int(row["WYEAR"]) != year:
            raise ValueError(
                f"Run {run} used weather year {row['WYEAR']}, expected {year}."
            )
        metrics = {
            "HARWT": "HWAH",
            "TNUP": "NUCM",
            "TNLF": "NLCM",
            "RAIN": "PRCM",
            "CET": "ETCM",
            "actual_n_applied": "NICM",
        }
        record = {"run": run, "weather_year": year, "experiment": row["EXNAME"]}
        for label, column in metrics.items():
            value = float(row[column])
            if not math.isfinite(value):
                raise ValueError(f"Invalid {column} for weather year {year}.")
                # Preserve failed-yield validation; leave missing other metrics blank.
            record[label] = None if value == -99 and label != "HARWT" else value
        if record["HARWT"] < 0:
            raise ValueError(
                f"Missing/failed yield for weather year {year}; not assigned zero."
            )
        for column in ("SDAT", "PDAT", "MDAT", "HDAT"):
            record[column] = int(row[column])
        records[run] = record
    if len(records) != len(expected_years):
        raise ValueError(
            f"Expected {len(expected_years)} completed runs, found {len(records)} in {path}."
        )
    return [records[run] for run in range(1, len(expected_years) + 1)]


class MultiYearMaizeSimulator:
    def __init__(self, args):
        self.args = args
        self.years = parse_years(args.years)
        self.root = Path(args.dssat_root).expanduser().resolve()
        self.exe = (
            Path(args.exe).expanduser().resolve()
            if args.exe
            else self.root / "DSCSM048.EXE"
        )
        self.template_path = Path(args.template).resolve()
        self.weather_dir = Path(args.weather_dir).resolve()
        self.template = self.template_path.read_text(encoding="utf-8-sig")
        self.max_total_n = args.budget
        self.max_applications = 15
        self.max_n_per_application = 300
        self.min_n_per_application = 20
        self.n_step = 1
        self.reference_yield = 13000.0
        self.objective_n_reference = 300.0
        self.dates = list(range(args.min_doy, args.max_doy + 1))
        self.rng = random.Random(args.seed)
        self.cache = {}
        self.batch_calls = 0
        self.cache_hits = 0
        self.batch_seconds = 0.0
        self.best_score = -math.inf
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        if len(self.years) == 1:
            years_label = str(self.years[0])
        else:
            years_label = f"{self.years[0]}-{self.years[-1]}_{len(self.years)}yrs"

        if args.mode == "optimize":
            run_name = (
                f"optimize_seed{args.seed}_{years_label}"
                f"_pop{args.population}_gen{args.generations}"
                f"_budget{args.budget}_{timestamp}"
            )
        else:
            run_name = f"{args.mode}_{years_label}_{timestamp}"

        self.output_dir = Path(args.output_dir).resolve() / run_name
        if not self.root.is_dir():
            raise FileNotFoundError(
                f"DSSAT runtime not found: {self.root}. Set --dssat-root."
            )
        if (
            not 20 <= self.max_total_n <= 300
            or not 1 <= args.min_doy <= args.max_doy <= 365
        ):
            raise ValueError(
                "Use budget 20-300 kg/ha and a valid within-year DOY window."
            )
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive (seconds per complete batch).")
        if args.mode != "prepare" and not self.exe.is_file():
            raise FileNotFoundError(
                f"DSSAT executable not found: {self.exe}. Set --exe."
            )
        # Validate the template and selected data before starting any simulation.
        self.weather_sources = {}
        for year in self.years:
            render_experiment(
                self.template, year, [(args.min_doy, 20)], args.daily_outputs
            )
            source = self.weather_dir / f"UKLE{year % 100:02d}01.WTH"
            check_weather(source, year)
            # DSSAT searches cwd before the experiment directory. Detect shadowing.
            shadow = self.root / source.name
            if shadow.exists() and digest(shadow) != digest(source):
                raise ValueError(f"Conflicting weather file in DSSAT cwd: {shadow}")
            self.weather_sources[year] = source
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.inputs_dir = self.root / ("MY" + uuid.uuid4().hex[:6].upper())
        self.inputs_dir.mkdir(exist_ok=False)
        self.batch_path = self.root / (self.inputs_dir.name + ".v48")
        for source in self.weather_sources.values():
            shutil.copy2(source, self.inputs_dir / source.name)
        self.experiments = {
            year: self.inputs_dir / f"UKLE{year % 100:02d}02.MZX" for year in self.years
        }
        self.command = [str(self.exe)]
        if not args.omit_model:
            self.command.append("MZCER048")
        self.command.extend(["B", self.batch_path.name])
        self.write_batch_file()
        metadata = {
            "arguments": vars(args),
            "weather_years": self.years,
            "objective": "mean(0.5 * HARWT / 13000 - 0.5 * planned_N / 300)",
            "yield_field": "Summary.OUT HWAH (console HARWT)",
            "date_policy": "same DOY, including leap years",
            "initial_conditions": "independent reset from the template for every year",
            "dssat_command": self.command,
            "dssat_working_directory": str(self.root),
            "input_directory": str(self.inputs_dir),
            "template_sha256": digest(self.template_path),
            "weather_sha256": {
                str(y): digest(p) for y, p in self.weather_sources.items()
            },
            "script_sha256": digest(__file__),
            "executable_sha256": digest(self.exe) if self.exe.is_file() else None,
        }
        (self.output_dir / "run_config.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        write_csv(
            self.output_dir / "batch_manifest.csv",
            [
                {
                    "run": i,
                    "weather_year": y,
                    "experiment_file": str(p),
                    "weather_file": str(self.weather_sources[y]),
                }
                for i, (y, p) in enumerate(self.experiments.items(), 1)
            ],
        )

    def validate_schedule(self, schedule):
        if not 1 <= len(schedule) <= min(self.max_applications, len(self.dates)):
            raise ValueError("Schedule must have 1-15 applications within the window.")
        if len({d for d, _ in schedule}) != len(schedule):
            raise ValueError("Duplicate application dates.")
        if sum(n for _, n in schedule) > self.max_total_n:
            raise ValueError("Schedule exceeds the maximum N budget.")
        for doy, amount in schedule:
            if (
                doy not in self.dates
                or int(amount) != amount
                or not 20 <= amount <= 300
            ):
                raise ValueError(
                    "Use DOY (not YYDDD), and integer amounts from 20 to 300 kg N/ha."
                )

    def write_batch_file(self):
        # Official DSSAT 4.8 batch format: 92-column FILEX followed by five I7 values.
        rows = [
            "$BATCH(MAIZE)",
            "!",
            "@FILEX".ljust(94) + "TRTNO     RP     SQ     OP     CO",
        ]
        for path in self.experiments.values():
            relative = str(path.relative_to(self.root))
            if len(relative) > 92:
                raise ValueError(
                    "Batch experiment path exceeds DSSAT's 92-column limit."
                )
            rows.append(f"{relative:<92}{1:7d}{1:7d}{0:7d}{1:7d}{0:7d}")
        self.batch_path.write_text("\n".join(rows) + "\n", encoding="ascii")

    def write_experiments(self, schedule):
        self.validate_schedule(schedule)
        for year, path in self.experiments.items():
            path.write_text(
                render_experiment(
                    self.template, year, schedule, self.args.daily_outputs
                ),
                encoding="ascii",
            )

    def clear_outputs(self):
        # Only remove the files read to decide whether THIS batch succeeded.
        for path in self.root.iterdir():
            if path.is_file() and path.name.upper() in {
                "SUMMARY.OUT",
                "ERROR.OUT",
                "WARNING.OUT",
            }:
                path.unlink()

    def find_output(self, name):
        matches = [
            p
            for p in self.root.iterdir()
            if p.is_file() and p.name.upper() == name.upper()
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous case variants of {name} in {self.root}.")
        return matches[0] if matches else None

    def run_dssat(self):
        self.clear_outputs()
        started = time.perf_counter()
        self.batch_calls += 1
        log_path = self.output_dir / "last_dssat.log"
        with log_path.open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(
                    self.command,
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=self.args.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"DSSAT batch timed out. See {log_path}") from exc
        self.batch_seconds += time.perf_counter() - started
        errors = self.find_output("ERROR.OUT")
        if completed.returncode != 0 or (errors and errors.stat().st_size > 0):
            if errors:
                shutil.copy2(errors, self.output_dir / "ERROR.OUT")
            raise RuntimeError(
                f"DSSAT failed (return code {completed.returncode}). See {log_path}"
            )
        summary = self.find_output("Summary.OUT")
        if summary is None:
            raise RuntimeError(f"DSSAT produced no Summary.OUT. See {log_path}")
        return read_summary(summary, self.years)

    def evaluate_candidate(self, schedule, force=False):
        schedule = list(schedule)
        self.validate_schedule(schedule)
        schedule = sorted((int(d), int(n)) for d, n in schedule)
        key = tuple(schedule)
        if not force and key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        self.write_experiments(schedule)
        yearly = self.run_dssat()
        total_n = sum(n for _, n in schedule)
        for row in yearly:
            row["year_score"] = (
                0.5 * row["HARWT"] / self.reference_yield
                - 0.5 * total_n / self.objective_n_reference
            )
        yields = [row["HARWT"] for row in yearly]
        result = {
            "schedule_doy": schedule,
            "total_n": total_n,
            "num_applications": len(schedule),
            "mean_yield": float(np.mean(yields)),
            "std_yield_across_weather_years": (
                float(np.std(yields, ddof=1)) if len(yields) > 1 else None
            ),
            "min_yield": min(yields),
            "max_yield": max(yields),
            "score": float(np.mean([row["year_score"] for row in yearly])),
            "per_year": yearly,
        }
        evaluation_id = self.batch_calls
        write_csv(
            self.output_dir / "candidate_results.csv",
            [
                {
                    "evaluation_id": evaluation_id,
                    **{
                        k: json.dumps(v) if k == "schedule_doy" else v
                        for k, v in result.items()
                        if k != "per_year"
                    },
                }
            ],
            append=True,
        )
        write_csv(
            self.output_dir / "yearly_results.csv",
            [
                {"evaluation_id": evaluation_id, "planned_n": total_n, **row}
                for row in yearly
            ],
            append=True,
        )
        self.cache[key] = result
        if result["score"] > self.best_score:
            self.best_score = result["score"]
            self.save_best(result)
        print(
            f"Batch {self.batch_calls}: {len(self.years)} years, mean yield {result['mean_yield']:.1f}, "
            f"N {total_n}, score {result['score']:.6f}",
            flush=True,
        )
        return result

    def save_best(self, result):
        path = self.output_dir / "best.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        temporary.replace(path)
        write_csv(self.output_dir / "best_by_year.csv", result["per_year"])

    def archive_current_outputs(self):
        destination = self.output_dir / "best_dssat"
        destination.mkdir(exist_ok=True)
        for name in ["Summary.OUT", "WARNING.OUT"]:
            source = self.find_output(name)
            if source:
                shutil.copy2(source, destination / source.name)
        shutil.copy2(self.batch_path, destination / self.batch_path.name)
        shutil.copytree(
            self.inputs_dir, destination / self.inputs_dir.name, dirs_exist_ok=True
        )
        shutil.copy2(self.output_dir / "last_dssat.log", destination / "dssat.log")

    def generate_random_vector(self):
        vector = np.zeros(len(self.dates), dtype=float)
        num_apps = self.rng.randint(1, min(self.max_applications, len(self.dates)))
        remaining = self.max_total_n
        for index in self.rng.sample(range(len(vector)), num_apps):
            if remaining < self.min_n_per_application:
                break
            amount = self.rng.randint(
                self.min_n_per_application, min(remaining, self.max_n_per_application)
            )
            vector[index] = amount
            remaining -= amount
        return vector

    def repair_vector(self, vector):
        vector = np.round(
            np.clip(np.asarray(vector, dtype=float), 0, self.max_n_per_application)
        )
        vector[(vector > 0) & (vector < self.min_n_per_application)] = 0
        active = np.flatnonzero(vector)
        if len(active) > self.max_applications:
            keep = active[np.argsort(vector[active])[-self.max_applications :]]
            mask = np.zeros(len(vector), dtype=bool)
            mask[keep] = True
            vector[~mask] = 0
        if vector.sum() > self.max_total_n:
            vector = np.floor(vector * self.max_total_n / vector.sum())
            vector[(vector > 0) & (vector < self.min_n_per_application)] = 0
        if not np.any(vector):
            vector[self.rng.randrange(len(vector))] = self.min_n_per_application
        return vector

    def vector_to_schedule(self, vector):
        return [
            (d, int(n))
            for d, n in zip(self.dates, vector)
            if n >= self.min_n_per_application
        ]

    def evolutionary_search(self):
        args = self.args
        if (
            args.population < 4
            or args.generations < 1
            or not 0 <= args.CR <= 1
            or not 0 < args.F <= 2
        ):
            raise ValueError(
                "Use population >=4, generations >=1, CR in [0,1], F in (0,2]."
            )
        population = np.array(
            [self.generate_random_vector() for _ in range(args.population)]
        )
        # Evaluate parents ONCE; retained parents keep their known fitness.
        results = [
            self.evaluate_candidate(self.vector_to_schedule(v)) for v in population
        ]
        best = max(results, key=lambda r: r["score"])
        self.log_generation(0, best)
        for generation in range(1, args.generations + 1):
            order = sorted(
                range(len(results)), key=lambda i: results[i]["score"], reverse=True
            )
            population = population[order]
            results = [results[i] for i in order]
            next_population = population.copy()
            next_results = list(results)
            for i, parent in enumerate(population):
                others = [j for j in range(args.population) if j != i]
                a, b = self.rng.sample(others, 2)
                mutant = self.repair_vector(
                    parent
                    + args.F * (population[0] - parent + population[a] - population[b])
                )
                forced = self.rng.randrange(len(parent))
                trial = self.repair_vector(
                    np.array(
                        [
                            (
                                mutant[j]
                                if self.rng.random() < args.CR or j == forced
                                else parent[j]
                            )
                            for j in range(len(parent))
                        ]
                    )
                )
                trial_result = self.evaluate_candidate(self.vector_to_schedule(trial))
                if trial_result["score"] >= results[i]["score"]:
                    next_population[i] = trial
                    next_results[i] = trial_result
            population, results = next_population, next_results
            best = max(results, key=lambda r: r["score"])
            self.log_generation(generation, best)
            print(
                f"Generation {generation}/{args.generations}: best score {best['score']:.6f}; "
                f"cache hits {self.cache_hits}",
                flush=True,
            )
        # Bypass cache so raw DSSAT files actually belong to the selected schedule.
        verified = self.evaluate_candidate(best["schedule_doy"], force=True)
        if not math.isclose(verified["score"], best["score"], rel_tol=0, abs_tol=1e-12):
            raise RuntimeError(
                "Best schedule rerun changed its score. Check DSSAT inputs/settings."
            )
        self.save_best(verified)
        self.archive_current_outputs()
        return verified

    def log_generation(self, generation, best):
        write_csv(
            self.output_dir / "convergence.csv",
            [
                {
                    "generation": generation,
                    "best_score": best["score"],
                    "best_mean_yield": best["mean_yield"],
                    "best_total_n": best["total_n"],
                    "dssat_batches": self.batch_calls,
                    "single_year_simulations": self.batch_calls * len(self.years),
                    "cache_hits": self.cache_hits,
                }
            ],
            append=True,
        )


def plotting_backend():
    """Check plotting availability before starting an expensive simulation."""
    try:
        import matplotlib

        matplotlib.use(
            "Agg"
        )  # Save files without opening windows, including on clusters.
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit(
            "Automatic plots need matplotlib. Run: python -m pip install matplotlib\n"
            "Or add --no-plots to run without figures."
        ) from exc
    return plt


def save_visualizations(simulator, result, plt):
    """Write figures for this completed run only; never launch more DSSAT runs."""
    folder = simulator.output_dir / "plots"
    folder.mkdir(exist_ok=True)
    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.2,
            "axes.axisbelow": True,
            "pdf.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )

    def save(fig, name):
        try:
            for extension in ("png", "pdf"):
                buffer = io.BytesIO()
                fig.savefig(buffer, format=extension, dpi=300, bbox_inches="tight")
                data = buffer.getvalue()
                if not data:
                    raise RuntimeError(f"Empty figure: {name}.{extension}")
                (folder / f"{name}.{extension}").write_bytes(data)
        finally:
            plt.close(fig)

    def rows(name):
        with (simulator.output_dir / name).open(
            newline="", encoding="utf-8-sig"
        ) as handle:
            return list(csv.DictReader(handle))

    yearly = sorted(result["per_year"], key=lambda row: row["weather_year"])
    years = [row["weather_year"] for row in yearly]
    yields = [row["HARWT"] for row in yearly]
    mode_label = (
        "Selected schedule" if simulator.args.mode == "optimize" else "Tested schedule"
    )
    fig, ax = plt.subplots(
        figsize=(max(8, len(years) * 0.35), 4.8), layout="constrained"
    )
    ax.bar(range(len(years)), yields, color="#277da8")
    ax.set_xticks(range(len(years)), years, rotation=45 if len(years) > 10 else 0)
    ax.set(
        xlabel="Weather year",
        ylabel="Simulated grain yield (kg/ha)",
        ylim=(0, None),
        title=f"{mode_label}: yield across weather years\n"
        f"Planned N: {result['total_n']} kg/ha | Objective: {result['score']:.6f}",
    )
    save(fig, "yield_by_year")

    fig, ax = plt.subplots(figsize=(8, 4.5), layout="constrained")
    dates = [day for day, amount in result["schedule_doy"]]
    amounts = [amount for day, amount in result["schedule_doy"]]
    ax.vlines(dates, 0, amounts, color="#277da8", linewidth=2)
    ax.scatter(dates, amounts, color="#277da8", s=65, zorder=3)
    ax.set(
        xlabel="Day of year (same schedule in every year)",
        ylabel="Nitrogen per application (kg N/ha)",
        title=f"{mode_label}: fertilizer timing and amount",
        xlim=(simulator.args.min_doy - 3, simulator.args.max_doy + 3),
        ylim=(0, max(amounts, default=1) * 1.25),
    )
    save(fig, "fertilizer_schedule")

    std = result["std_yield_across_weather_years"]
    summary = {
        "mode": simulator.args.mode,
        "seed": simulator.args.seed if simulator.args.mode == "optimize" else "",
        "weather_years": ", ".join(map(str, years)),
        "max_n_budget_kg_ha": simulator.args.budget,
        "planned_n_kg_ha": result["total_n"],
        "applications": result["num_applications"],
        "mean_yield_kg_ha": result["mean_yield"],
        "yield_sd_across_weather_years_kg_ha": std if std is not None else "",
        "minimum_year_yield_kg_ha": min(yields),
        "maximum_year_yield_kg_ha": max(yields),
        "aggregate_objective": result["score"],
        "schedule_doy_kg_n_ha": ", ".join(
            f"{d}:{n}" for d, n in result["schedule_doy"]
        ),
    }
    write_csv(folder / "run_summary.csv", [summary])
    table_rows = [
        ["Weather years evaluated", str(len(years))],
        ["Maximum nitrogen budget (kg N/ha)", str(simulator.args.budget)],
        ["Total planned nitrogen (kg N/ha)", str(result["total_n"])],
        ["Number of applications", str(result["num_applications"])],
        ["Mean yield across years (kg/ha)", f"{result['mean_yield']:,.1f}"],
        [
            "Yield SD across weather years (kg/ha)",
            f"{std:,.1f}" if std is not None else "Not available: one year",
        ],
        ["Lowest yearly yield (kg/ha)", f"{min(yields):,.1f}"],
        ["Highest yearly yield (kg/ha)", f"{max(yields):,.1f}"],
        ["Aggregate objective (higher is better)", f"{result['score']:.6f}"],
    ]
    fig, ax = plt.subplots(figsize=(9, 4.9), layout="constrained")
    ax.axis("off")
    table = ax.table(
        cellText=table_rows,
        colLabels=["Measure", "Result"],
        colWidths=[0.68, 0.32],
        cellLoc="left",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.8)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#d5dce2")
        if row == 0:
            cell.set_facecolor("#193d56")
            cell.set_text_props(color="white", weight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#eef4f8")
    label = (
        f" | Optimizer seed {simulator.args.seed}"
        if simulator.args.mode == "optimize"
        else ""
    )
    ax.set_title(f"{mode_label}: results summary{label}", pad=15, fontweight="bold")
    save(fig, "results_table")

    if simulator.args.mode == "optimize":
        history = rows("convergence.csv")
        fig, ax = plt.subplots(figsize=(8, 4.6), layout="constrained")
        ax.plot(
            [int(row["generation"]) for row in history],
            [float(row["best_score"]) for row in history],
            color="#277da8",
            linewidth=2,
        )
        ax.set(
            xlabel="Generation (0 = initial population)",
            ylabel="Best objective so far",
            title=f"Optimization progress | Seed {simulator.args.seed}",
        )
        save(fig, "convergence")

        unique = {}
        for row in rows("candidate_results.csv"):
            key = json.dumps(json.loads(row["schedule_doy"]))
            unique.setdefault(key, row)
        candidates = list(unique.values())
        # Evaluation order is available even when cached trials do not launch DSSAT.
        # The final forced verification is already represented by an earlier row.
        candidates.sort(key=lambda row: int(row["evaluation_id"]))
        evaluation_ids = [int(row["evaluation_id"]) for row in candidates]
        counts = [int(row["num_applications"]) for row in candidates]
        scores = [float(row["score"]) for row in candidates]
        best_counts = []
        best_score = float("-inf")
        best_count = None
        for count, score in zip(counts, scores):
            if score > best_score:
                best_score, best_count = score, count
            best_counts.append(best_count)
        fig, (ax, ax2) = plt.subplots(
            2, 1, figsize=(9, 7), layout="constrained", height_ratios=[2, 1]
        )
        ax.scatter(
            evaluation_ids,
            counts,
            color="#9abed1",
            s=18,
            alpha=0.65,
            label="Evaluated candidate",
        )
        ax.step(
            evaluation_ids,
            best_counts,
            where="post",
            color="#d56836",
            linewidth=2.2,
            label="Application count of best-scoring schedule so far",
        )
        ax.set(
            xlabel="Unique candidate evaluation (in search order)",
            ylabel="Number of applications",
            title=f"Application counts during optimization | Seed {simulator.args.seed}",
        )
        ax.set_yticks(range(1, max(counts) + 1))
        ax.legend(loc="upper right")
        values = {count: counts.count(count) for count in sorted(set(counts))}
        ax2.bar(list(values), list(values.values()), color="#277da8")
        ax2.set(
            xlabel="Number of applications in candidate schedule",
            ylabel="Candidates per count",
        )
        ax2.set_xticks(list(values))
        save(fig, "application_counts_during_search")
        fig, ax = plt.subplots(figsize=(8, 4.8), layout="constrained")
        points = ax.scatter(
            [float(row["total_n"]) for row in candidates],
            [float(row["mean_yield"]) for row in candidates],
            c=[float(row["score"]) for row in candidates],
            cmap="viridis",
            s=25,
            alpha=0.7,
        )
        ax.scatter(
            result["total_n"],
            result["mean_yield"],
            marker="*",
            s=230,
            color="#ff8b38",
            edgecolors="black",
            label="Selected schedule",
            zorder=5,
        )
        fig.colorbar(points, ax=ax, label="Aggregate objective")
        ax.set(
            xlabel="Total planned nitrogen (kg N/ha)",
            ylabel="Mean yield across weather years (kg/ha)",
            title="Yield and nitrogen among evaluated schedules",
        )
        ax.legend()
        save(fig, "yield_vs_planned_n")

    (folder / "README.txt").write_text(
        "Figures describe this completed run only. Source data are in the parent results folder.\n"
        "PNG: presentation images. PDF: vector figures. run_summary.csv: table for Excel.\n"
        "Nitrogen is the planned schedule total, not the maximum budget or necessarily DSSAT-reported N applied.\n"
        "Yield SD is across weather years, not across optimizer seeds; it is unavailable for one weather year.\n"
        "An optimized schedule is the best found in this run, not a proven global optimum.\n"
        "Optimization years were used during the search; these figures do not establish held-out performance.\n"
        "The sampled-schedule scatter varies timing and amount; it is not a controlled nitrogen-response curve.\n"
        "Application-count plot shows unique DSSAT evaluations in search order, not every cached trial.\n"
        "A flat best-application-count line does not imply the objective has converged; compare the score plot.\n"
        "Application counts are sampled unevenly; compare fixed-count optimizations before drawing agronomic conclusions.\n"
        "Multiple seeds and budget settings are needed for seed/budget comparison figures.\n",
        encoding="utf-8",
    )
    return folder


def make_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--mode", choices=["prepare", "test", "optimize"], default="test"
    )
    parser.add_argument("--years", nargs="+", default=["2021", "2022", "2023"])
    parser.add_argument("--dssat-root", default="C:/DSSAT48")
    parser.add_argument("--exe", default=None)
    parser.add_argument("--omit-model", action="store_true")
    parser.add_argument("--template", default="templates/maize_template.MZX")
    parser.add_argument("--weather-dir", default="weather")
    parser.add_argument("--output-dir", default="output/multiyear")
    parser.add_argument("--schedule", default="150:50,170:100")
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--min-doy", type=int, default=134)
    parser.add_argument("--max-doy", type=int, default=227)
    parser.add_argument("--population", type=int, default=20)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--F", type=float, default=0.5)
    parser.add_argument("--CR", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--timeout", type=float, default=600, help="seconds per complete DSSAT batch"
    )
    parser.add_argument("--daily-outputs", action="store_true")
    parser.add_argument(
        "--no-plots", action="store_true", help="skip automatic PNG/PDF figures"
    )
    return parser


def main():
    args = make_parser().parse_args()
    plt = plotting_backend() if args.mode != "prepare" and not args.no_plots else None
    started = time.perf_counter()
    simulator = MultiYearMaizeSimulator(args)
    print(
        f"Years: {list(simulator.years)}\nResults: {simulator.output_dir}", flush=True
    )
    if args.mode == "prepare":
        simulator.write_experiments(parse_schedule(args.schedule))
        print(
            f"Prepared {len(simulator.years)} experiments; no DSSAT simulations run.\nBatch: {simulator.batch_path}"
        )
        return
    if args.mode == "test":
        result = simulator.evaluate_candidate(parse_schedule(args.schedule))
        simulator.archive_current_outputs()
    else:
        maximum_batches = args.population * (args.generations + 1) + 1
        print(
            f"At most {maximum_batches} batches / {maximum_batches * len(simulator.years)} "
            "yearly simulations, including the final verification rerun; caching can reduce this.",
            flush=True,
        )
        result = simulator.evolutionary_search()
    print("\nSelected schedule (DOY, kg N/ha):", result["schedule_doy"])
    for row in result["per_year"]:
        print(
            f"  {row['weather_year']}: yield {row['HARWT']:.1f}, score {row['year_score']:.6f}"
        )
    print(
        f"Mean yield: {result['mean_yield']:.1f} kg/ha | N: {result['total_n']} kg/ha"
    )
    print(f"Aggregate objective: {result['score']:.6f}")
    print(
        f"Elapsed: {time.perf_counter() - started:.1f}s; DSSAT batches: {simulator.batch_calls}; "
        f"cache hits: {simulator.cache_hits}"
    )
    if args.mode == "test":
        mean_batch = simulator.batch_seconds / simulator.batch_calls
        pilot_batches = args.population * (args.generations + 1) + 1
        print(
            f"This {len(simulator.years)}-year batch took {mean_batch:.2f}s. Rough "
            f"DSSAT-only estimate for population {args.population}, generations {args.generations}: "
            f"{mean_batch * pilot_batches / 60:.1f} min before cache savings. Actual runtime varies."
        )
    print(f"Saved: {simulator.output_dir}")
    if plt is not None:
        print("Saving plots and summary table...", flush=True)
        try:
            folder = save_visualizations(simulator, result, plt)
        except Exception:
            print(
                f"Plot generation failed; completed simulation results remain in {simulator.output_dir}",
                flush=True,
            )
            raise
        print(f"Plots and table saved: {folder}", flush=True)


if __name__ == "__main__":
    main()
