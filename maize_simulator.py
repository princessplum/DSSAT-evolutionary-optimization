import subprocess
from pathlib import Path
import csv
import pandas as pd
import matplotlib.pyplot as plt
import random
import ast
from datetime import datetime, timedelta
import re
from matplotlib.patches import Patch
from matplotlib.lines import Line2D


class MaizeSimulator:

    # ---------------------------
    # 1. DSSAT SETUP
    # ---------------------------

    # Clearing Old Outputs
    def clear_dssat_outputs(self):
        for filename in ["Summary.OUT", "SoilNi.OUT", "WARNING.OUT"]:
            path = self.dssat_root / filename

            if path.exists():
                path.unlink()

    def clear_optimizer_outputs(self):
        files_to_delete = [
            Path(f"output/optimizer_results/results_{self.year}.csv"),
            Path(f"output/convergence_history_{self.year}.csv"),
        ]

        for path in files_to_delete:
            if path.exists():
                path.unlink()

        viz_dir = Path("output") / "visualizations" / str(self.year)

        if viz_dir.exists():
            for path in viz_dir.glob("*.png"):
                path.unlink()

    def __init__(self):
        # DSSAT paths
        self.dssat_root = Path("C:/DSSAT48")
        self.maize_dir = self.dssat_root / "Maize"
        self.batch_file = self.dssat_root / "DSSBatch.v48"
        self.exe = self.dssat_root / "DSCSM048.EXE"

        # Experiment files
        self.template_path = Path("templates/maize_template.MZX")
        self.output_exp = self.maize_dir / "UKLE2102.MZX"

        # Optimization settings
        self.max_applications = 15
        self.max_total_n = 300
        self.max_n_per_application = 300
        self.n_step = 1

        # Default year / fertilizer window
        self.year = 2021
        self.min_fert_date = 21140
        self.max_fert_date = 21227

    def set_year(self, year):
        self.year = year
        yy = year % 100

        self.output_exp = self.maize_dir / f"UKLE{yy}02.MZX"
        self.min_fert_date = int(f"{yy}140")
        self.max_fert_date = int(f"{yy}227")

    # def convert_dssat_date_to_year(self, value):
    #     value = str(value)

    #     if len(value) == 5 and value.startswith("21"):
    #         return f"{self.year % 100:02d}{value[2:]}"

    #     return value

    def replace_fertilizer_block(self, fertilizer_schedule):
        """
        fertilizer_schedule = list of tuples:
        [(date, amount), ...]
        Example: [(21150, 50), (21160, 100)]
        """

        lines = self.template_path.read_text().splitlines()
        new_lines = []

        in_fert_block = False

        for line in lines:
            if line.startswith("*FERTILIZERS"):
                in_fert_block = True
                new_lines.append(line)
                continue

            if in_fert_block and line.startswith("@F"):
                new_lines.append(line)

                # Insert new fertilizer schedule
                for date, amount in fertilizer_schedule:
                    fert_line = f" 1 {date} FE005 AP001     0   {amount:<5}     0     0     0     0   -99 UREA"
                    new_lines.append(fert_line)

                continue

            # Exit fertilizer block
            if (
                in_fert_block
                and line.startswith("*")
                and not line.startswith("*FERTILIZERS")
            ):
                in_fert_block = False

            if not in_fert_block:
                new_lines.append(line)

        content = "\n".join(new_lines)

        yy = self.year % 100

        content = re.sub(r"\b21(\d{3})\b", lambda m: f"{yy:02d}{m.group(1)}", content)

        return content

    def write_experiment_file(self, fertilizer_schedule):
        content = self.replace_fertilizer_block(fertilizer_schedule)
        self.output_exp.write_text(content)

    def write_batch_file(self):
        batch_content = (
            "$BATCH(MAIZE)\n"
            "!\n"
            "@FILEX                                                                                        TRTNO     RP     SQ     OP     CO\n"
            f"{self.output_exp}                                                                   1      1      0      1      0\n"
        )

        self.batch_file.write_text(batch_content)

    def load_weather_data(self, year):
        weather_file = Path("weather") / f"UKLE{str(year)[2:]}01.WTH"

        if not weather_file.exists():
            raise FileNotFoundError(f"Weather file not found: {weather_file}")

        rows = []

        def parse_weather_value(value):
            """
            Convert DSSAT weather values to float.
            Missing-value codes such as -99 become NaN.
            """
            value = float(value)

            if value <= -90:
                return float("nan")

            return value

        with open(weather_file, "r") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("*") or line.startswith("@"):
                    continue

                parts = line.split()

                if len(parts) >= 5 and parts[0].isdigit():
                    date_code = int(parts[0])

                    rows.append(
                        {
                            "DATE": date_code,
                            "DOY": date_code % 1000,
                            "SRAD": parse_weather_value(parts[1]),
                            "TMAX": parse_weather_value(parts[2]),
                            "TMIN": parse_weather_value(parts[3]),
                            "RAIN": parse_weather_value(parts[4]),
                        }
                    )

        df = pd.DataFrame(rows)

        if df.empty:
            raise ValueError(f"No weather data found in {weather_file}")

        df["weather_year"] = year
        df["TMEAN"] = (df["TMAX"] + df["TMIN"]) / 2

        return df

    def run_dssat(self):
        result = subprocess.run(
            [str(self.exe), "MZCER048", "B", "DSSBatch.v48"],
            cwd=self.dssat_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            raise RuntimeError("DSSAT run failed")

        print(result.stdout)
        return result.stdout

    def extract_summary_outputs(self, dssat_output):
        for line in dssat_output.splitlines():
            parts = line.split()

            if len(parts) >= 15 and parts[0] == "1" and parts[1] == "MZ":
                return {
                    "TRT": float(parts[2]),
                    "FLO": float(parts[3]),
                    "MAT": float(parts[4]),
                    "TOPWT": float(parts[5]),
                    "HARWT": float(parts[6]),
                    "RAIN": float(parts[7]),
                    "TIRR": float(parts[8]),
                    "CET": float(parts[9]),
                    "PESW": float(parts[10]),
                    "TNUP": float(parts[11]),
                    "TNLF": float(parts[12]),
                    "TSON": float(parts[13]),
                    "TSOC": float(parts[14]),
                }

        raise ValueError("Could not find DSSAT result line")

    def extract_soil_niad(self):
        soilni_path = self.dssat_root / "SoilNi.OUT"

        if not soilni_path.exists():
            raise FileNotFoundError("SoilNi.OUT was not found.")

        data_rows = []

        with open(soilni_path, "r") as f:
            for line in f:
                parts = line.split()

                if len(parts) > 7 and parts[0].isdigit():
                    data_rows.append(parts)

        if not data_rows:
            raise ValueError("No data rows found in SoilNi.OUT.")

        first_row = data_rows[0]
        last_row = data_rows[-1]

        # SoilNi.OUT columns:
        # YEAR DOY DAS NAPC NI#M NIAD NITD NHTD ...
        initial_niad = float(first_row[5])
        final_niad = float(last_row[5])

        return initial_niad, final_niad

    def evaluate_candidate(self, fertilizer_schedule):
        self.clear_dssat_outputs()
        self.write_experiment_file(fertilizer_schedule)
        self.write_batch_file()

        dssat_output = self.run_dssat()
        result = self.extract_summary_outputs(dssat_output)

        initial_niad, final_niad = self.extract_soil_niad()
        result["initial_NIAD"] = initial_niad
        result["final_NIAD"] = final_niad

        total_n = self.get_total_n_applied(fertilizer_schedule)

        estimated_n_loss = initial_niad + total_n - final_niad - result["TNUP"]

        result["estimated_n_loss"] = max(0, estimated_n_loss) # Forces negative values to zero
        result["montse_n_loss_proxy"] = total_n - result["TNUP"]

        return result

    def save_best_dssat_output_files(self):
        output_dir = Path("output") / "dssat_outputs" / str(self.year)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_files = ["Summary.OUT", "SoilNi.OUT", "WARNING.OUT"]

        for filename in output_files:
            source = self.dssat_root / filename

            if source.exists():
                destination = output_dir / filename
                destination.write_text(source.read_text())

        # Also save the final experiment file for verification
        exp_destination = output_dir / self.output_exp.name
        exp_destination.write_text(self.output_exp.read_text())

    # ---------------------------
    # 2. Schedule checking and formatting
    # ---------------------------
    def get_total_n_applied(self, fertilizer_schedule):
        return sum(amount for date, amount in fertilizer_schedule)

    def is_valid_schedule(self, schedule):
        total_n = self.get_total_n_applied(schedule)

        # Single event application can be as large as the entire budget
        if total_n > self.max_total_n:
            return False

        if len(schedule) > self.max_applications:
            return False

        for date, amount in schedule:
            if amount > self.max_n_per_application:
                return False

            if date < self.min_fert_date or date > self.max_fert_date:
                return False

        return True

    def format_schedule_for_table(self, schedule):
        """
        Convert a DSSAT fertilizer schedule into a readable string.

        Example:
        [(21149, 178), (21170, 20)]

        becomes:
        May 29 (DOY 149): 178 kg/ha; June 19 (DOY 170): 20 kg/ha
        """

        if isinstance(schedule, str):
            schedule = ast.literal_eval(schedule)

        formatted_apps = []

        for date, amount in schedule:
            if amount <= 0:
                continue

            date = int(date)
            doy = int(str(date)[-3:])

            calendar_date = self.dssat_date_to_datetime(date)

            formatted_apps.append(
                f"{calendar_date.strftime('%b %d')} " f"(DOY {doy}): {amount:g} kg/ha"
            )

        return "; ".join(formatted_apps)

    def dssat_date_to_datetime(self, dssat_date):
        year = 2000 + int(str(dssat_date)[:2])
        day_of_year = int(str(dssat_date)[2:])
        return datetime(year, 1, 1) + timedelta(days=day_of_year - 1)

    # ---------------------------
    # 3. Schedule generation
    # ---------------------------
    def generate_random_schedule(self):
        num_apps = random.randint(1, self.max_applications)

        possible_dates = list(range(self.min_fert_date, self.max_fert_date + 1))
        selected_dates = sorted(random.sample(possible_dates, num_apps))

        schedule = []

        remaining_n = self.max_total_n

        for date in selected_dates:
            max_amount = min(self.max_n_per_application, remaining_n)

            if max_amount <= 0:
                amount = 0
            else:
                amount = random.choice(
                    range(self.n_step, int(max_amount) + 1, self.n_step)
                )

            schedule.append((date, amount))
            remaining_n -= amount

        return schedule

    # ---------------------------
    # 4. Scoring / optimization
    # ---------------------------

    def score_result(self, result, total_n):
        # score = result["HARWT"]

        score = result["HARWT"] - total_n

        # score = result["HARWT"] - (total_n - result["TNUP"])

        # score = result["HARWT"] - result["TNLF"]

        return score

    def mutate_schedule(self, schedule):
        possible_dates = list(range(self.min_fert_date, self.max_fert_date + 1))
        possible_amounts = list(
            range(self.n_step, self.max_n_per_application + 1, self.n_step)
        )

        new_schedule = schedule.copy()
        mutation_type = random.choice(["change_amount", "change_date", "add", "remove"])

        if mutation_type == "change_amount" and new_schedule:
            i = random.randrange(len(new_schedule))
            date, amount = new_schedule[i]
            new_schedule[i] = (date, random.choice(possible_amounts))

        elif mutation_type == "change_date" and new_schedule:
            i = random.randrange(len(new_schedule))
            date, amount = new_schedule[i]
            new_schedule[i] = (random.choice(possible_dates), amount)

        elif mutation_type == "add" and len(new_schedule) < self.max_applications:
            new_schedule.append(
                (random.choice(possible_dates), random.choice(possible_amounts))
            )

        elif mutation_type == "remove" and len(new_schedule) > 1:
            i = random.randrange(len(new_schedule))
            new_schedule.pop(i)

        schedule_dict = {}
        for date, amount in new_schedule:
            schedule_dict[date] = amount

        return sorted(schedule_dict.items())

    def crossover_schedules(self, parent1, parent2):
        combined = parent1 + parent2
        random.shuffle(combined)

        child = combined[: random.randint(1, min(self.max_applications, len(combined)))]

        # Remove duplicate dates
        schedule_dict = {}
        for date, amount in child:
            schedule_dict[date] = amount

        return sorted(schedule_dict.items())

    def evolutionary_search(self, population_size=100, generations=50):
        population = []

        while len(population) < population_size:
            schedule = self.generate_random_schedule()

            if self.is_valid_schedule(schedule):
                population.append(schedule)

        best_schedule = None
        best_result = None
        best_score = float("-inf")
        convergence_history = []

        for gen in range(generations):
            print(f"\nGENERATION {gen + 1}/{generations}")

            evaluated = []

            for i, schedule in enumerate(population):
                result = self.evaluate_candidate(schedule)

                total_n = self.get_total_n_applied(schedule)

                score = self.score_result(result, total_n)

                self.save_result_to_csv(schedule, result)

                evaluated.append((score, schedule, result))

                if score > best_score:
                    best_score = score
                    best_schedule = schedule
                    best_result = result
                    print("New best schedule found!")
                    print(best_schedule, best_result, best_score)

            evaluated.sort(reverse=True, key=lambda x: x[0])

            self.top_schedules = evaluated[:3]

            # Print best and worst schedules
            best_gen_score, best_gen_schedule, best_gen_result = evaluated[0]
            worst_gen_score, worst_gen_schedule, worst_gen_result = evaluated[-1]

            unique_schedules = {
                tuple(schedule) for score, schedule, result in evaluated
            }

            near_optimal_count = sum(
                1
                for score, schedule, result in evaluated
                if score >= 0.95 * best_gen_score
            )

            convergence_history.append(
                {
                    "generation": gen + 1,
                    "best_generation_score": best_gen_score,
                    "best_overall_score": best_score,
                    "best_generation_yield": best_gen_result["HARWT"],
                    "best_generation_total_n": self.get_total_n_applied(
                        best_gen_schedule
                    ),
                    "unique_schedules": len(unique_schedules),
                    "near_optimal_schedules": near_optimal_count,
                }
            )

            print("\nBEST OF GENERATION")
            print("Schedule:", best_gen_schedule)
            print("Result:", best_gen_result)
            print("Score:", best_gen_score)

            print("\nWORST OF GENERATION")
            print("Schedule:", worst_gen_schedule)
            print("Result:", worst_gen_result)
            print("Score:", worst_gen_score)

            # Keep top 25%
            elite_count = max(2, population_size // 4)
            elites = evaluated[:elite_count]

            new_population = [schedule for score, schedule, result in elites]

            while len(new_population) < population_size:
                parent1 = random.choice(elites)[1]
                parent2 = random.choice(elites)[1]

                child = self.crossover_schedules(parent1, parent2)
                child = self.mutate_schedule(child)

                while not self.is_valid_schedule(child):
                    child = self.mutate_schedule(child)

                new_population.append(child)

            population = new_population

        evaluated.sort(reverse=True, key=lambda x: x[0])

        print("\nFINAL GENERATION BEST")
        print(evaluated[0])

        print("\nFINAL GENERATION WORST")
        print(evaluated[-1])

        print("\nBEST OF GENERATION")
        print(best_gen_schedule)

        print("\nWORST OF GENERATION")
        print(worst_gen_schedule)

        pd.DataFrame(convergence_history).to_csv(
            f"output/convergence_history_{self.year}.csv", index=False
        )

        self.evaluate_candidate(best_schedule)
        self.save_best_dssat_output_files()

        return best_schedule, best_result, best_score

    def find_best_schedule(self, num_simulations=50):
        best_schedule = None
        best_result = None
        best_score = float("-inf")

        for i in range(num_simulations):
            schedule = self.generate_random_schedule()
            result = self.evaluate_candidate(schedule)

            # Higher yield is good, nitrogen loss is bad
            score = result["HARWT"] - 100 * result["TNLF"]

            self.save_result_to_csv(schedule, result)

            print(f"\nRun {i + 1}/{num_simulations}")
            print("Schedule:", schedule)
            print("Result:", result)
            print("Score:", score)

            if score > best_score:
                best_score = score
                best_schedule = schedule
                best_result = result

                print("New best schedule found!")

        return best_schedule, best_result, best_score

    # ---------------------------
    # 5. Results saving / processing
    # ---------------------------
    def save_result_to_csv(self, schedule, result, filename=None):
        if filename is None:
            filename = f"output/optimizer_results/results_{self.year}.csv"
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        file_exists = Path(filename).exists()

        total_n = self.get_total_n_applied(schedule)
        score = self.score_result(result, total_n)

        # Nitrogen Use Efficiency
        nue = result["HARWT"] / total_n if total_n > 0 else 0

        # Percent nitrogen loss
        percent_n_loss = (result["TNLF"] / total_n) * 100 if total_n > 0 else 0

        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)

            # Write header once
            if not file_exists:
                writer.writerow(
                    [
                        "weather_year",
                        "schedule",
                        "total_n_applied",
                        "HARWT",
                        "TNUP",
                        "TNLF",
                        "percent_n_loss",
                        "NUE",
                        "RAIN",
                        "CET",
                        "initial_NIAD",
                        "final_NIAD",
                        "estimated_n_loss",
                        "montse_n_loss_proxy",
                        "score",
                    ]
                )

            writer.writerow(
                [
                    self.year,
                    schedule,
                    total_n,
                    result["HARWT"],
                    result["TNUP"],
                    result["TNLF"],
                    percent_n_loss,
                    nue,
                    result["RAIN"],
                    result["CET"],
                    result["initial_NIAD"],
                    result["final_NIAD"],
                    result["estimated_n_loss"],
                    result["montse_n_loss_proxy"],
                    score,
                ]
            )

    def summarize_by_total_n(self, filename=None):

        if filename is None:
            filename = f"output/optimizer_results/results_{self.year}.csv"
        df = pd.read_csv(filename)

        summary = (
            df.groupby("total_n_applied")
            .agg(
                mean_yield=("HARWT", "mean"),
                std_yield=("HARWT", "std"),
                mean_tnlf=("TNLF", "mean"),
                std_tnlf=("TNLF", "std"),
                mean_nue=("NUE", "mean"),
                std_nue=("NUE", "std"),
                count=("HARWT", "count"),
            )
            .reset_index()
        )

        summary.to_csv(
            f"output/optimizer_results/summary_by_total_n_{self.year}.csv",
            index=False,
        )
        return summary

    def save_weather_summary_row(
        self,
        best_schedule,
        best_result,
        best_score,
        summary_file="output/weather_summary.csv",
    ):
        summary_file = Path(summary_file)
        summary_file.parent.mkdir(parents=True, exist_ok=True)

        file_exists = summary_file.exists()
        total_n = self.get_total_n_applied(best_schedule)

        with open(summary_file, "a", newline="") as f:
            writer = csv.writer(f)

            if not file_exists:
                writer.writerow(
                    [
                        "weather_year",
                        "best_schedule",
                        "fertilizer_n_applied",
                        "yield",
                        "TNUP",
                        "TNLF",
                        "estimated_n_loss",
                        "RAIN",
                        "CET",
                        "score",
                    ]
                )

            writer.writerow(
                [
                    self.year,
                    best_schedule,
                    total_n,
                    best_result["HARWT"],
                    best_result["TNUP"],
                    best_result["TNLF"],
                    best_result["estimated_n_loss"],
                    best_result["RAIN"],
                    best_result["CET"],
                    best_score,
                ]
            )

    # ---------------------------
    # 6. Visualizations
    # ---------------------------
    def prepare_results_df(self, filename=None):

        if filename is None:
            filename = f"output/optimizer_results/results_{self.year}.csv"
        df = pd.read_csv(filename)
        df["parsed_schedule"] = df["schedule"].apply(ast.literal_eval)
        df["num_applications"] = df["parsed_schedule"].apply(len)

        df["schedule_clean"] = df["parsed_schedule"].apply(
            lambda s: tuple((d, a) for d, a in s if a > 0)
        )

        return df

    def visualize_results(self, filename=None):
        if filename is None:
            filename = f"output/optimizer_results/results_{self.year}.csv"

        viz_dir = Path("output") / "visualizations" / str(self.year)
        viz_dir.mkdir(parents=True, exist_ok=True)

        df = self.prepare_results_df(filename)

        self.plot_yield_vs_total_n(df, viz_dir)
        self.plot_yield_summary_by_total_n(df, viz_dir)
        self.plot_nue_vs_total_n(df, viz_dir)
        self.plot_tnlf_vs_total_n(df, viz_dir)
        self.plot_num_applications_vs_yield(df, viz_dir)
        self.plot_top_schedule_heatmap(df, viz_dir)

        print(f"All visualizations saved to {viz_dir}.")

    def plot_yield_vs_total_n(self, df, viz_dir):
        plt.figure()

        scatter = plt.scatter(
            df["total_n_applied"], df["HARWT"], c=df["TNLF"], cmap="viridis", alpha=0.7
        )

        plt.colorbar(scatter, label="Nitrogen Loss (TNLF)")
        plt.xlabel("Total N Applied (kg/ha)")
        plt.ylabel("Yield (HARWT kg/ha)")
        plt.title("Yield vs Total Nitrogen Applied")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(viz_dir / "yield_vs_total_n.png")
        print("Saved yield_vs_total_n.png")
        plt.close()

    def plot_yield_summary_by_total_n(self, df, viz_dir):

        summary = (
            df.groupby("total_n_applied")
            .agg(
                mean_yield=("HARWT", "mean"),
                std_yield=("HARWT", "std"),
                max_yield=("HARWT", "max"),
                count=("HARWT", "count"),
            )
            .reset_index()
            .sort_values("total_n_applied")
        )

        plt.figure(figsize=(10, 6))

        # Mean ± Std Dev
        plt.errorbar(
            summary["total_n_applied"],
            summary["mean_yield"],
            yerr=summary["std_yield"].fillna(0),
            fmt="o",
            capsize=5,
            label="Mean Yield ± Std Dev",
        )

        # Best yield found
        plt.plot(
            summary["total_n_applied"],
            summary["max_yield"],
            marker="s",
            linewidth=2,
            label="Best Yield Found",
        )

        plt.xlabel("Total Nitrogen Applied (kg/ha)")
        plt.ylabel("Yield (HARWT kg/ha)")
        plt.title("Yield Summary by Total Nitrogen Applied")

        plt.grid(True)
        plt.legend()

        plt.tight_layout()
        plt.savefig(viz_dir / "yield_summary_by_total_n.png")
        print("Saved yield_summary_by_total_n.png")
        plt.close()

    def plot_nue_vs_total_n(self, df, viz_dir):
        plt.figure()
        plt.scatter(df["total_n_applied"], df["NUE"], alpha=0.5)
        plt.xlabel("Total N Applied (kg/ha)")
        plt.ylabel("Nitrogen Use Efficiency (NUE)")
        plt.title("NUE vs Total Nitrogen Applied")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(viz_dir / "nue_vs_total_n.png")
        print("Saved nue_vs_total_n.png")
        plt.close()

    def plot_tnlf_vs_total_n(self, df, viz_dir):
        plt.figure()
        plt.scatter(df["total_n_applied"], df["TNLF"], alpha=0.5)
        plt.xlabel("Total N Applied (kg/ha)")
        plt.ylabel("Nitrogen Loss (TNLF kg/ha)")
        plt.title("Nitrogen Loss vs Total Nitrogen Applied")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(viz_dir / "tnlf_vs_total_n.png")
        print("Saved tnlf_vs_total_n.png")
        plt.close()

    def plot_num_applications_vs_yield(self, df, viz_dir):
        avg_yield = df.groupby("num_applications")["HARWT"].mean().reset_index()

        plt.figure(figsize=(8, 5))
        plt.bar(avg_yield["num_applications"], avg_yield["HARWT"])
        plt.xlabel("Number of Fertilizer Applications")
        plt.ylabel("Average Yield (HARWT kg/ha)")
        plt.title("Number of Applications vs Average Yield")
        plt.tight_layout()
        plt.savefig(viz_dir / "num_applications_vs_yield.png")
        print("Saved num_applications_vs_yield.png")
        plt.close()

    def plot_top_schedule_heatmap(self, df, viz_dir):
        top_n = 10

        top_df = (
            df.drop_duplicates(subset=["schedule_clean"])
            .sort_values(by=["HARWT", "total_n_applied"], ascending=[False, True])
            .head(top_n)
        )

        heatmap_data = []

        for _, row in top_df.iterrows():
            row_data = {}

            for date, amount in row["parsed_schedule"]:
                if amount > 0:
                    row_data[date] = amount

            heatmap_data.append(row_data)

        all_dates = sorted(set(date for row in heatmap_data for date in row.keys()))

        matrix = []
        for row in heatmap_data:
            matrix.append([row.get(date, 0) for date in all_dates])

        plt.figure(figsize=(12, 6))
        plt.imshow(matrix, aspect="auto")

        for i in range(len(matrix)):
            for j in range(len(all_dates)):
                amount = matrix[i][j]

                if amount > 0:
                    plt.text(
                        j,
                        i,
                        f"{amount}",
                        ha="center",
                        va="center",
                        fontsize=9,
                        color="black",
                    )

        plt.colorbar(label="Nitrogen Applied (kg/ha)")

        plt.xticks(range(len(all_dates)), all_dates, rotation=45)

        yield_labels = [
            f"Y={int(row['HARWT'])}, N={int(row['total_n_applied'])}"
            for _, row in top_df.iterrows()
        ]

        plt.yticks(range(len(top_df)), yield_labels)

        plt.ylabel("Yield and Total N")
        plt.xlabel("Application Day (DSSAT Date)")
        plt.title(
            f"Top {len(top_df)} Fertilizer Schedules\n"
            "Cell value = nitrogen applied on that DSSAT day (kg/ha)"
        )

        plt.tight_layout()
        plt.savefig(viz_dir / "top_schedule_timing_heatmap.png")
        print("Saved top_schedule_timing_heatmap.png")
        plt.close()

    def plot_convergence(self, filename=None):
        if filename is None:
            filename = f"output/convergence_history_{self.year}.csv"

        viz_dir = Path("output") / "visualizations" / str(self.year)
        viz_dir.mkdir(parents=True, exist_ok=True)

        df = pd.read_csv(filename)

        plt.figure(figsize=(8, 5))
        plt.plot(
            df["generation"],
            df["best_overall_score"],
            marker="o",
            label="Best overall score",
        )
        plt.plot(
            df["generation"],
            df["best_generation_score"],
            marker="s",
            label="Best score in generation",
        )

        plt.xlabel("Generation")
        plt.ylabel("Objective Function Value")
        plt.title(f"Optimizer Convergence ({self.year})")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        plt.savefig(viz_dir / "optimizer_convergence.png")
        plt.close()

    def plot_convergence_yield(
        self,
        filename=None,
    ):

        if filename is None:
            filename = f"output/convergence_history_{self.year}.csv"
        df = pd.read_csv(filename)

        plt.figure(figsize=(8, 5))

        plt.plot(df["generation"], df["best_generation_yield"], marker="o")

        plt.xlabel("Generation")
        plt.ylabel("Best Yield (kg/ha)")
        plt.title("Best Yield Found by Generation")

        plt.grid(True)
        plt.tight_layout()
        viz_dir = Path("output") / "visualizations" / str(self.year)
        viz_dir.mkdir(parents=True, exist_ok=True)
        plt.savefig(viz_dir / "convergence_yield.png")
        plt.close()

    def plot_population_diversity(
        self,
        filename=None,
    ):

        if filename is None:
            filename = f"output/convergence_history_{self.year}.csv"
        viz_dir = Path("output") / "visualizations" / str(self.year)
        viz_dir.mkdir(parents=True, exist_ok=True)

        df = pd.read_csv(filename)
        print(df.columns)

        plt.figure(figsize=(8, 5))

        plt.plot(
            df["generation"],
            df["unique_schedules"],
            marker="o",
        )

        plt.xlabel("Generation")
        plt.ylabel("Number of Unique Schedules")
        plt.title("Population Diversity by Generation")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(viz_dir / "population_diversity.png")
        print("Saved population_diversity.png")
        plt.close()

    def plot_annual_rainfall_by_year(self, start_year, end_year):
        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        years = []
        annual_rainfall = []

        for year in range(start_year, end_year + 1):
            df = self.load_weather_data(year)

            years.append(year)
            annual_rainfall.append(df["RAIN"].sum())

        plt.figure(figsize=(8, 5))

        markerline, stemlines, baseline = plt.stem(years, annual_rainfall)

        baseline.set_visible(False)

        # Start at zero and leave room for value labels
        max_rain = max(annual_rainfall)
        plt.ylim(0, max_rain * 1.10)

        for year, rain in zip(years, annual_rainfall):
            plt.text(
                year,
                rain + max_rain * 0.02,
                f"{rain:.1f}",
                ha="center",
                va="bottom",
            )

        plt.xlabel("Year")
        plt.ylabel("Precipitation (mm)")
        plt.title("Annual Precipitation by Year")
        plt.xticks(years)

        plt.tight_layout()
        plt.savefig(
            comparison_dir / f"annual_rainfall_{start_year}_{end_year}.png",
            dpi=300,
        )
        plt.close()

        print("Saved annual rainfall comparison.")

    def plot_growing_season_rainfall(self, start_year, end_year):
        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        # From DSSAT maize template:
        # PDATE = DOY 134 (May 14)
        # HDATE = DOY 272 (September 29)
        planting_doy = 134
        harvest_doy = 272

        fig, ax = plt.subplots(figsize=(10, 6))

        for year in range(start_year, end_year + 1):
            df = self.load_weather_data(year)

            # Keep only rainfall during the maize growing season
            season_df = df[
                (df["DOY"] >= planting_doy) & (df["DOY"] <= harvest_doy)
            ].copy()

            # Create 7-day periods starting from planting
            season_df["week"] = (season_df["DOY"] - planting_doy) // 7

            weekly_rain = (
                season_df.groupby("week")
                .agg(
                    rainfall=("RAIN", "sum"),
                    start_doy=("DOY", "min"),
                )
                .reset_index()
            )

            ax.plot(
                weekly_rain["start_doy"],
                weekly_rain["rainfall"],
                marker="o",
                label=str(year),
            )

        ax.set_xlabel("Start of 7-Day Period")
        ax.set_ylabel("7-Day Precipitation (mm)")
        ax.set_title("7-Day Precipitation During the Maize Growing Season")

        # Calendar labels corresponding to the common DOY timeline
        tick_doys = [134, 152, 182, 213, 244, 272]
        tick_labels = [
            "May 14",
            "Jun 1",
            "Jul 1",
            "Aug 1",
            "Sep 1",
            "Sep 29",
        ]

        ax.set_xticks(tick_doys)
        ax.set_xticklabels(tick_labels)

        ax.set_xlim(planting_doy, harvest_doy)
        ax.set_ylim(bottom=0)

        ax.grid(True, alpha=0.3)
        ax.legend(title="Weather Year")

        fig.tight_layout()

        fig.savefig(
            comparison_dir / f"growing_season_rainfall_{start_year}_{end_year}.png",
            dpi=300,
        )

        plt.close(fig)

        print("Saved growing-season rainfall comparison.")

    def classify_weather_years(self, start_year, end_year):
        comparison_dir = Path("output") / "weather_comparison"

        summary_file = (
            comparison_dir / f"weather_year_summary_{start_year}_{end_year}.csv"
        )

        if not summary_file.exists():
            raise FileNotFoundError(f"Weather summary file not found: {summary_file}")

        df = pd.read_csv(summary_file)

        rainfall = df["growing_season_rainfall_mm"].dropna()

        q1 = rainfall.quantile(0.25)
        median = rainfall.quantile(0.50)
        q3 = rainfall.quantile(0.75)
        mean = rainfall.mean()

        def classify(rain):
            if pd.isna(rain):
                return "Missing"
            elif rain < q1:
                return "Dry"
            elif rain > q3:
                return "Wet"
            else:
                return "Normal"

        df["weather_class"] = df["growing_season_rainfall_mm"].apply(classify)

        classified_file = (
            comparison_dir / f"weather_classification_{start_year}_{end_year}.csv"
        )

        df.to_csv(classified_file, index=False)

        thresholds_df = pd.DataFrame(
            {
                "statistic": [
                    "25th Percentile (Q1)",
                    "Median",
                    "75th Percentile (Q3)",
                    "Mean",
                ],
                "growing_season_rainfall_mm": [
                    round(q1, 1),
                    round(median, 1),
                    round(q3, 1),
                    round(mean, 1),
                ],
            }
        )

        thresholds_file = (
            comparison_dir / f"weather_classification_thresholds_"
            f"{start_year}_{end_year}.csv"
        )

        thresholds_df.to_csv(
            thresholds_file,
            index=False,
        )

        print("\nWeather classification thresholds:")
        print(f"Q1: {q1:.1f} mm")
        print(f"Median: {median:.1f} mm")
        print(f"Q3: {q3:.1f} mm")
        print(f"Mean: {mean:.1f} mm")

        return df, q1, median, q3

    def plot_weather_classification(self, start_year, end_year):
        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        # This method also creates/updates the classification CSV
        df, q1, median, q3 = self.classify_weather_years(
            start_year,
            end_year,
        )

        fig, ax = plt.subplots(figsize=(14, 6))

        years = df["year"]
        rainfall = df["growing_season_rainfall_mm"]

        # Assign colors according to weather classification
        class_colors = {
            "Dry": "tab:orange",
            "Normal": "tab:blue",
            "Wet": "tab:green",
            "Missing": "gray",
        }

        bar_colors = df["weather_class"].map(class_colors)

        # --------------------------------------------------
        # Background classification regions
        # --------------------------------------------------

        max_rain = rainfall.max() * 1.08

        ax.axhspan(
            0,
            q1,
            color="tab:orange",
            alpha=0.08,
        )

        ax.axhspan(
            q1,
            q3,
            color="tab:blue",
            alpha=0.06,
        )

        ax.axhspan(
            q3,
            max_rain,
            color="tab:green",
            alpha=0.08,
        )

        # --------------------------------------------------
        # Bars
        # --------------------------------------------------

        ax.bar(
            years,
            rainfall,
            color=bar_colors,
            edgecolor="black",
            linewidth=0.5,
        )

        # --------------------------------------------------
        # Classification threshold lines
        # --------------------------------------------------

        ax.axhline(
            q1,
            color="black",
            linestyle="--",
            linewidth=1.5,
        )

        ax.axhline(
            q3,
            color="black",
            linestyle="--",
            linewidth=1.5,
        )

        legend_elements = [
            Patch(facecolor="tab:orange", label="Dry"),
            Patch(facecolor="tab:blue", label="Normal"),
            Patch(facecolor="tab:green", label="Wet"),
            Line2D(
                [0],
                [0],
                color="black",
                linestyle="--",
                label=f"Q1 = {q1:.1f} mm",
            ),
            Line2D(
                [0],
                [0],
                color="black",
                linestyle="--",
                label=f"Q3 = {q3:.1f} mm",
            ),
        ]

        ax.legend(
            handles=legend_elements,
            loc="upper left",
        )

        # --------------------------------------------------
        # Labels for classification regions
        # --------------------------------------------------

        ax.text(
            end_year + 0.4,
            q1 / 2,
            "DRY",
            ha="left",
            va="center",
            fontweight="bold",
        )

        ax.text(
            end_year + 0.4,
            (q1 + q3) / 2,
            "NORMAL",
            ha="left",
            va="center",
            fontweight="bold",
        )

        ax.text(
            end_year + 0.4,
            (q3 + max_rain) / 2,
            "WET",
            ha="left",
            va="center",
            fontweight="bold",
        )

        # --------------------------------------------------
        # Figure formatting
        # --------------------------------------------------

        ax.set_xlabel("Weather Year")
        ax.set_ylabel("Maize Growing-Season Precipitation (mm)")

        ax.set_title(
            f"Growing-Season Precipitation Classification, " f"{start_year}-{end_year}"
        )

        ax.set_xticks(years)
        ax.set_xticklabels(years, rotation=90)

        # Extra room on the right for Dry / Normal / Wet labels
        ax.set_xlim(start_year - 2, end_year + 2)

        ax.set_ylim(0, max_rain)

        fig.tight_layout()

        output_file = (
            comparison_dir / f"weather_classification_{start_year}_{end_year}.png"
        )

        fig.savefig(
            output_file,
            dpi=300,
        )

        plt.close(fig)

        print(f"Saved weather classification figure to {output_file}")

    def plot_top_schedule_timeline(
        self,
        start_year,
        end_year,
        top_n=5,
    ):
        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        top_df = self.get_top_schedules_by_year(
            start_year,
            end_year,
            top_n,
        )

        if top_df is None or top_df.empty:
            return

        # Make sure schedules are parsed
        if "parsed_schedule" not in top_df.columns:
            top_df["parsed_schedule"] = top_df["schedule"].apply(ast.literal_eval)

        fig, ax = plt.subplots(figsize=(11, 8))

        y_position = 0
        y_ticks = []
        y_labels = []

        years = sorted(top_df["weather_year"].unique())

        for year in years:
            year_df = top_df[top_df["weather_year"] == year].sort_values("rank")

            for _, row in year_df.iterrows():

                y_ticks.append(y_position)

                y_labels.append(
                    f"{year}  #{int(row['rank'])}  " f"Y={int(row['HARWT'])}"
                )

                for date, amount in row["parsed_schedule"]:

                    if amount <= 0:
                        continue

                    # Remove the two-digit year from DSSAT date
                    # Example: 21149 -> DOY 149
                    doy = int(str(int(date))[-3:])

                    ax.scatter(
                        doy,
                        y_position,
                        s=max(amount * 3, 30),
                        alpha=0.7,
                    )

                    ax.text(
                        doy,
                        y_position,
                        f"{int(amount)}",
                        ha="center",
                        va="center",
                        fontsize=8,
                    )

                y_position += 1

            # Small gap between weather years
            y_position += 0.5

        ax.set_yticks(y_ticks)
        ax.set_yticklabels(y_labels)

        ax.set_xlim(135, 230)

        ax.set_xlabel("Fertilizer Application Day of Year")
        ax.set_ylabel("Top-Ranked Schedule")
        ax.set_title(f"Top {top_n} Optimized Fertilizer Schedules " "by Weather Year")

        # Useful calendar references
        tick_doys = [140, 152, 182, 213, 227]
        tick_labels = [
            "May 20\nDOY 140",
            "Jun 1\nDOY 152",
            "Jul 1\nDOY 182",
            "Aug 1\nDOY 213",
            "Aug 15\nDOY 227",
        ]

        ax.set_xticks(tick_doys)
        ax.set_xticklabels(tick_labels)

        ax.grid(axis="x", alpha=0.3)

        fig.tight_layout()

        fig.savefig(
            comparison_dir / f"top{top_n}_schedule_timeline_"
            f"{start_year}_{end_year}.png",
            dpi=300,
        )

        plt.close(fig)

        print("Saved top schedule timeline.")

    def get_top_schedules_by_year(self, start_year, end_year, top_n=5):
        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        top_rows = []

        # --------------------------------------------------
        # Load weather classifications if available
        # --------------------------------------------------

        classification_file = comparison_dir / f"weather_classification_1990_2025.csv"

        weather_classes = {}

        if classification_file.exists():
            weather_df = pd.read_csv(classification_file)

            weather_df["year"] = weather_df["year"].astype(int)

            weather_classes = dict(
                zip(
                    weather_df["year"],
                    weather_df["weather_class"],
                )
            )

        # --------------------------------------------------
        # Find top N schedules for every optimized year
        # --------------------------------------------------

        for year in range(start_year, end_year + 1):

            results_file = Path(f"output/optimizer_results/results_{year}.csv")

            if not results_file.exists():
                print(f"Skipping {year}: optimizer results not found.")
                continue

            df = pd.read_csv(results_file)

            df["parsed_schedule"] = df["schedule"].apply(ast.literal_eval)

            # Standardized schedule representation
            df["schedule_clean"] = df["parsed_schedule"].apply(
                lambda schedule: tuple(
                    (date, amount) for date, amount in schedule if amount > 0
                )
            )

            # Remove duplicate schedules and rank by objective
            top_year = (
                df.drop_duplicates(subset=["schedule_clean"])
                .sort_values(
                    "score",
                    ascending=False,
                )
                .head(top_n)
                .copy()
            )

            top_year["rank"] = range(
                1,
                len(top_year) + 1,
            )

            top_year["weather_year"] = year

            top_year["weather_class"] = weather_classes.get(
                int(year),
                "Unknown",
            )
            # Make schedule readable
            top_year["fertilizer_schedule"] = top_year["parsed_schedule"].apply(
                self.format_schedule_for_table
            )

            top_rows.append(top_year)

        if not top_rows:
            print("No optimizer results found.")
            return None

        # --------------------------------------------------
        # Combine all years
        # --------------------------------------------------

        top_df = pd.concat(
            top_rows,
            ignore_index=True,
        )

        # Round numerical outputs for readability
        top_df["HARWT"] = top_df["HARWT"].round(1)
        top_df["score"] = top_df["score"].round(1)
        top_df["TNUP"] = top_df["TNUP"].round(1)
        top_df["TNLF"] = top_df["TNLF"].round(1)
        top_df["NUE"] = top_df["NUE"].round(1)

        # --------------------------------------------------
        # Publication/presentation table
        # --------------------------------------------------

        table_df = top_df[
            [
                "weather_year",
                "weather_class",
                "rank",
                "fertilizer_schedule",
                "total_n_applied",
                "HARWT",
                "score",
                "TNUP",
                "TNLF",
                "NUE",
            ]
        ].copy()

        table_df.columns = [
            "Weather Year",
            "Weather Class",
            "Rank",
            "Fertilizer Schedule",
            "Total N Applied (kg/ha)",
            "Yield (kg/ha)",
            "Objective Score",
            "TNUP (kg/ha)",
            "TNLF (kg/ha)",
            "NUE",
        ]

        output_file = (
            comparison_dir / f"top{top_n}_schedule_table_"
            f"{start_year}_{end_year}.csv"
        )

        table_df.to_csv(
            output_file,
            index=False,
        )

        print(f"Saved top {top_n} schedule table to " f"{output_file}")

        return table_df

    def compare_weather_years(self, start_year, end_year):
        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        years = list(range(start_year, end_year + 1))

        # Read yearly optimizer result files
        all_dfs = []

        for year in years:
            file = Path(f"output/optimizer_results/results_{year}.csv")

            if not file.exists():
                print(f"Skipping {year}: {file} not found.")
                continue

            df = pd.read_csv(file)
            df["weather_year"] = year
            all_dfs.append(df)

        if not all_dfs:
            print("No result files found.")
            return

        combined = pd.concat(all_dfs, ignore_index=True)
        combined.to_csv(
            comparison_dir / f"combined_results_{start_year}_{end_year}.csv",
            index=False,
        )

        # ---------------------------
        # 1. Overlay yield response curves
        # ---------------------------
        fig, ax = plt.subplots(figsize=(10, 6))

        for year in sorted(combined["weather_year"].unique()):
            df_year = combined[combined["weather_year"] == year]

            summary = (
                df_year.groupby("total_n_applied")
                .agg(mean_yield=("HARWT", "mean"))
                .reset_index()
                .sort_values("total_n_applied")
            )

            ax.plot(
                summary["total_n_applied"],
                summary["mean_yield"],
                marker="o",
                label=str(year),
            )

        ax.set_xlabel("Total Fertilizer N Applied (kg/ha)")
        ax.set_ylabel("Mean Yield (HARWT kg/ha)")
        ax.set_title("Yield Response by Weather Year")
        ax.grid(True)
        ax.legend(title="Weather Year")

        fig.tight_layout()
        fig.savefig(
            comparison_dir / f"yield_response_overlay_{start_year}_{end_year}.png"
        )
        plt.close(fig)

        # ---------------------------
        # 2. Best result per weather year
        # ---------------------------
        best_rows = []

        for year in sorted(combined["weather_year"].unique()):
            df_year = combined[combined["weather_year"] == year]
            best = df_year.sort_values("score", ascending=False).iloc[0]
            best_rows.append(best)

        best_df = pd.DataFrame(best_rows)
        best_df.to_csv(
            comparison_dir / f"best_by_year_{start_year}_{end_year}.csv", index=False
        )

        # Best fertilizer N by year
        plt.figure(figsize=(8, 5))
        plt.bar(best_df["weather_year"].astype(str), best_df["total_n_applied"])
        plt.xlabel("Weather Year")
        plt.ylabel("Best Fertilizer N Applied (kg/ha)")
        plt.title("Best Fertilizer N by Weather Year")
        plt.tight_layout()
        plt.savefig(
            comparison_dir / f"best_fertilizer_by_year_{start_year}_{end_year}.png"
        )
        plt.close()

        # Best yield by year
        plt.figure(figsize=(8, 5))
        plt.bar(best_df["weather_year"].astype(str), best_df["HARWT"])
        plt.xlabel("Weather Year")
        plt.ylabel("Best Yield (HARWT kg/ha)")
        plt.title("Best Yield by Weather Year")
        plt.tight_layout()
        plt.savefig(comparison_dir / f"best_yield_by_year_{start_year}_{end_year}.png")
        plt.close()

        # TNUP by year
        plt.figure(figsize=(8, 5))
        plt.bar(best_df["weather_year"].astype(str), best_df["TNUP"])
        plt.xlabel("Weather Year")
        plt.ylabel("TNUP (kg/ha)")
        plt.title("Crop Nitrogen Uptake by Weather Year")
        plt.tight_layout()
        plt.savefig(comparison_dir / f"tnup_by_year_{start_year}_{end_year}.png")
        plt.close()

        # TNLF by year
        plt.figure(figsize=(8, 5))
        plt.bar(best_df["weather_year"].astype(str), best_df["TNLF"])
        plt.xlabel("Weather Year")
        plt.ylabel("TNLF (kg/ha)")
        plt.title("Nitrogen Loss by Weather Year")
        plt.tight_layout()
        plt.savefig(comparison_dir / f"tnlf_by_year_{start_year}_{end_year}.png")
        plt.close()

        print(f"Weather comparison figures saved to {comparison_dir}")

    # ---------------------------
    # 7. User Input
    # ---------------------------
    def get_user_schedule(self):

        schedule = []

        while True:

            num_apps = int(
                input(
                    f"How many fertilizer applications? (1-{self.max_applications}): "
                )
            )

            if 1 <= num_apps <= self.max_applications:
                break

            print(
                f"Number of applications must be between 1 and {self.max_applications}."
            )

        for i in range(num_apps):

            while True:

                date = int(
                    input(
                        f"Enter DSSAT date for application {i + 1} ({self.min_fert_date}-{self.max_fert_date}): "
                    )
                )

                if self.min_fert_date <= date <= self.max_fert_date:
                    break

                print(
                    f"Date must be between {self.min_fert_date} and {self.max_fert_date}."
                )

            while True:

                amount = float(
                    input(f"Enter nitrogen amount for application {i + 1} kg/ha: ")
                )

                if 0 <= amount <= self.max_n_per_application:
                    break

                print(
                    f"Nitrogen amount must be between 0 and {self.max_n_per_application} kg/ha."
                )

            schedule.append((date, amount))

        return schedule


# ---------------------------
# TEST RUN
# ---------------------------
if __name__ == "__main__":
    sim = MaizeSimulator()

    print("Choose mode:")
    print("1. Test a user-defined fertilizer schedule")
    print("2. Run evolutionary optimization")
    print("3. Run multi-year weather comparison")
    print("4. Create weather comparison visualizations")
    print("5. Test weather data")

    choice = input("Enter 1, 2, 3, 4, or 5: ")

    if choice in ["1", "2"]:
        year = int(input("Enter weather year to run (e.g. 2021): "))
        sim.set_year(year)

    if choice == "1":

        # ---------------------------
        # GET USER SCHEDULE
        # ---------------------------

        schedule = sim.get_user_schedule()

        # ---------------------------
        # RUN USER SCHEDULE ONLY
        # ---------------------------

        result = sim.evaluate_candidate(schedule)

        total_n = sim.get_total_n_applied(schedule)
        score = sim.score_result(result, total_n)
        nue = result["HARWT"] / total_n if total_n > 0 else 0

        # Save this one test schedule to results.csv
        sim.save_result_to_csv(schedule, result)

        # Save DSSAT output files for this test
        sim.save_best_dssat_output_files()

        print("\nUser Schedule:")
        print(schedule)

        print("\nDSSAT Predicted Results:")
        print(result)

        print("\nSummary:")
        print("Weather Year:", sim.year)
        print("Total N Applied:", total_n)
        print("Yield:", result["HARWT"])
        print("TNUP:", result["TNUP"])
        print("TNLF:", result["TNLF"])
        print("Estimated N Loss:", result["estimated_n_loss"])
        print("Montse N Loss Proxy:", result["montse_n_loss_proxy"])
        print("NUE:", nue)
        print("Score:", score)

    elif choice == "2":
        sim.clear_optimizer_outputs()

        sim.max_total_n = 300  # CHANGE MAX TOTAL N

        best_schedule, best_result, best_score = sim.evolutionary_search(
            population_size=100, generations=50
        )

        print("\nMAX N:")
        print(sim.max_total_n)
        print("\nBEST SCHEDULE FOUND:")
        print(best_schedule)
        print("BEST RESULT:")
        print(best_result)
        print("BEST SCORE:")
        print(best_score)

        sim.visualize_results()
        sim.plot_convergence()
        sim.plot_convergence_yield()
        sim.plot_population_diversity()

    elif choice == "3":

        start_year = int(input("Enter first weather year (e.g. 2021): "))
        end_year = int(input("Enter last weather year (e.g. 2023): "))

        if start_year > end_year:
            print("First year must be less than or equal to last year.")
            exit()

        years = list(range(start_year, end_year + 1))

        summary_file = f"output/weather_summary_{start_year}_{end_year}.csv"
        Path(summary_file).unlink(missing_ok=True)

        for year in years:

            print("\n" + "=" * 60)
            print(f"RUNNING WEATHER YEAR {year}")
            print("=" * 60)

            sim.set_year(year)
            sim.clear_optimizer_outputs()

            sim.max_total_n = 300

            best_schedule, best_result, best_score = sim.evolutionary_search(
                population_size=100,
                generations=50,
            )

            sim.save_weather_summary_row(
                best_schedule,
                best_result,
                best_score,
                summary_file,
            )

            print("\nWEATHER YEAR:", sim.year)
            print("BEST SCHEDULE:", best_schedule)
            print("BEST SCORE:", best_score)

            sim.visualize_results()
            sim.plot_convergence()
            sim.plot_convergence_yield()
            sim.plot_population_diversity()

        print("\nFinished weather comparison!")
        print(f"Summary saved to {summary_file}")

    elif choice == "4":

        start_year = int(input("Enter first weather year to compare (e.g. 2021): "))
        end_year = int(input("Enter last weather year to compare (e.g. 2023): "))

        sim.compare_weather_years(start_year, end_year)

        sim.plot_annual_rainfall_by_year(start_year, end_year)

        sim.plot_growing_season_rainfall(start_year, end_year)

        sim.get_top_schedules_by_year(
            start_year,
            end_year,
            top_n=5,
        )

        print("\nWeather comparison visualizations complete.")

    elif choice == "5":
        start_year = int(input("Enter first weather year (e.g. 1990): "))
        end_year = int(input("Enter last weather year (e.g. 2025): "))

        summary_rows = []

        for year in range(start_year, end_year + 1):

            weather_df = sim.load_weather_data(year)

            missing_rain = weather_df["RAIN"].isna().sum()
            missing_tmax = weather_df["TMAX"].isna().sum()
            missing_tmin = weather_df["TMIN"].isna().sum()

            # Annual rainfall
            if weather_df["RAIN"].isna().any():
                annual_rain = float("nan")
            else:
                annual_rain = weather_df["RAIN"].sum()

            # Maize growing season: planting DOY 134 to harvest DOY 272
            growing_season = weather_df[
                (weather_df["DOY"] >= 134) & (weather_df["DOY"] <= 272)
            ]

            if growing_season["RAIN"].isna().any():
                growing_season_rain = float("nan")
            else:
                growing_season_rain = growing_season["RAIN"].sum()

            # Fertilizer optimization window
            fert_window = weather_df[
                (weather_df["DOY"] >= 140) & (weather_df["DOY"] <= 227)
            ]

            if fert_window["RAIN"].isna().any():
                fert_window_rain = float("nan")
            else:
                fert_window_rain = fert_window["RAIN"].sum()

            # Temperatures
            annual_mean_temp = weather_df["TMEAN"].mean()
            growing_season_mean_temp = growing_season["TMEAN"].mean()

            # Save one row for this year
            summary_rows.append(
                {
                    "year": year,
                    "annual_rainfall_mm": (
                        round(annual_rain, 1) if pd.notna(annual_rain) else float("nan")
                    ),
                    "growing_season_rainfall_mm": (
                        round(growing_season_rain, 1)
                        if pd.notna(growing_season_rain)
                        else float("nan")
                    ),
                    "fertilizer_window_rainfall_mm": round(fert_window_rain, 1),
                    "annual_mean_temp_c": round(annual_mean_temp, 1),
                    "growing_season_mean_temp_c": round(growing_season_mean_temp, 1),
                    "missing_rain_records": missing_rain,
                    "missing_tmax_records": missing_tmax,
                    "missing_tmin_records": missing_tmin,
                }
            )

            print(f"\nWeather Year: {year}")
            print(f"Number of weather records: {len(weather_df)}")
            print(f"Missing rainfall records: {missing_rain}")
            print(f"Missing Tmax records: {missing_tmax}")
            print(f"Missing Tmin records: {missing_tmin}")
            print(f"Annual rainfall: {annual_rain:.1f} mm")
            print(
                f"Growing-season rainfall (DOY 134-272): "
                f"{growing_season_rain:.1f} mm"
            )
            print(
                f"Fertilizer-window rainfall (DOY 140-227): "
                f"{fert_window_rain:.1f} mm"
            )
            print(f"Average annual temperature: {annual_mean_temp:.1f} C")
            print(
                f"Average growing-season temperature: "
                f"{growing_season_mean_temp:.1f} C"
            )

        # Save all years after loop finishes
        summary_df = pd.DataFrame(summary_rows)

        comparison_dir = Path("output") / "weather_comparison"
        comparison_dir.mkdir(parents=True, exist_ok=True)

        summary_file = (
            comparison_dir / f"weather_year_summary_{start_year}_{end_year}.csv"
        )

        summary_df.to_csv(summary_file, index=False)

        print(f"\nWeather summary saved to {summary_file}")

        # Classify years and create classification figure
        sim.plot_weather_classification(
            start_year,
            end_year,
        )

    else:
        print("Invalid choice.")
