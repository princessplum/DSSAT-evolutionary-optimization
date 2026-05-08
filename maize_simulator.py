import subprocess
from pathlib import Path
import csv
import pandas as pd
import matplotlib.pyplot as plt
import random

COMMAND_STRING = r'C:\DSSAT48\DSCSM048.EXE MZCER048 B DSSBatch.v48'
BATCH_FILEV4 = r'DSSBatch.v4'
CROP_FOLDER = r'C:\DSSAT48\Maize\\'
CROP_FILE = r'UKLE2102.MZX'
# WEATHER_DSSAT_PATH =  r'C:\DSSAT48\Weather\BLUK7150.wth'
# CROP_TEMPLATE = r'templates\SBX_template.SBX'
# CROP_TEMPLATE_ALT = r'templates\SBX_template_alt.SBX'
# BATCHV48_TEMPLATE = r'templates\DSSBatch_template.txt'
# WEATHER_TEMPLATE = r'templates\WTH_template.wth'
# OUTPUT_FILE = r'summary.csv'
# OUTPUT_DIR = r'output\general'
# PLANT_DAY = 140
# FIRST_DECISION_DAY = 162
# LOG_FILE = r"output\general\sim_out.log"
# SOILWAT_FILE = r'soilwat.csv'

class MaizeSimulator:
    def __init__(self):
        # DSSAT paths
        self.dssat_root = Path("C:/DSSAT48")
        self.maize_dir = self.dssat_root / "Maize"
        self.batch_file = self.dssat_root / "DSSBatch.v48"
        self.exe = self.dssat_root / "DSCSM048.EXE"

        # Template path (your project)
        self.template_path = Path("templates/maize_template.MZX")

        # Output experiment file DSSAT will run
        self.output_exp = self.maize_dir / "UKLE2102.MZX"

    # ---------------------------
    # 1. Replace fertilizer block
    # ---------------------------
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
                for i, (date, amount) in enumerate(fertilizer_schedule, start=1):
                    fert_line = f" {i} {date} FE005 AP001     0   {amount:<5}     0     0     0     0   -99 UREA"
                    new_lines.append(fert_line)

                continue

            # Exit fertilizer block
            if in_fert_block and line.startswith("*") and not line.startswith("*FERTILIZERS"):
                in_fert_block = False

            if not in_fert_block:
                new_lines.append(line)

        return "\n".join(new_lines)

    # ---------------------------
    # 2. Write experiment file
    # ---------------------------
    def write_experiment_file(self, fertilizer_schedule):
        content = self.replace_fertilizer_block(fertilizer_schedule)
        self.output_exp.write_text(content)

    # ---------------------------
    # 3. Write batch file
    # ---------------------------
    def write_batch_file(self):
        batch_content = f"""$BATCH(MAIZE)
!
@FILEX                                                                                        TRTNO     RP     SQ     OP     CO
C:\\DSSAT48\\Maize\\UKLE2102.MZX                                                                   1      1      0      1      0
"""
        self.batch_file.write_text(batch_content)

    # ---------------------------
    # 4. Run DSSAT
    # ---------------------------
    def run_dssat(self):
        result = subprocess.run(
            [str(self.exe), "MZCER048", "B", "DSSBatch.v48"],
            cwd=self.dssat_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            raise RuntimeError("DSSAT run failed")

        print(result.stdout)
        return result.stdout

    # ---------------------------
    # 5. Extract outputs
    # ---------------------------
    def extract_summary_outputs(self, dssat_output):
        for line in dssat_output.splitlines():
            parts = line.split()

            if len(parts) >= 14 and parts[0] == "1" and parts[1] == "MZ":
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

    # ---------------------------
    # 6. Full pipeline
    # ---------------------------
    def evaluate_candidate(self, fertilizer_schedule):
        self.write_experiment_file(fertilizer_schedule)
        self.write_batch_file()
        dssat_output = self.run_dssat()
        return self.extract_summary_outputs(dssat_output)
    
    # ---------------------------
    # 7. Total Nitrogen Applied
    # ---------------------------
    def get_total_n_applied(self, fertilizer_schedule):
        return sum(amount for date, amount in fertilizer_schedule)
    
    # ---------------------------
    # 8. Save results as CSV
    # ---------------------------
    def save_result_to_csv(self, schedule, result, filename="results.csv"):
        file_exists = Path(filename).exists()

        total_n = self.get_total_n_applied(schedule)

        # Nitrogen Use Efficiency
        nue = result["HARWT"] / total_n if total_n > 0 else 0

        # Percent nitrogen loss
        percent_n_loss = (result["TNLF"] / total_n) * 100 if total_n > 0 else 0

        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)

            # Write header once
            if not file_exists:
                writer.writerow([
                    "schedule",
                    "total_n_applied",
                    "HARWT",
                    "TNUP",
                    "TNLF",
                    "percent_n_loss",
                    "NUE",
                    "RAIN",
                    "CET"
                ])

            writer.writerow([
                schedule,
                total_n,
                result["HARWT"],
                result["TNUP"],
                result["TNLF"],
                percent_n_loss,
                nue,
                result["RAIN"],
                result["CET"]
            ])

    # ---------------------------
    # 9. Visualization
    # ---------------------------
    def visualize_results(self, filename="results.csv"):
        df = pd.read_csv(filename)

        # --- Yield vs Nitrogen Loss ---
        plt.figure()
        plt.scatter(df["TNLF"], df["HARWT"])

        for i, row in df.iterrows():
            plt.annotate(i + 1, (row["TNLF"], row["HARWT"]))

        plt.xlabel("Nitrogen Loss (TNLF)")
        plt.ylabel("Yield (HARWT)")
        plt.title("Yield vs Nitrogen Loss")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig("yield_vs_n_loss.png")

        # --- Yield by schedule ---
        plt.figure()
        plt.bar(range(len(df)), df["HARWT"])

        plt.xlabel("Schedule Index")
        plt.ylabel("Yield (HARWT)")
        plt.title("Yield by Fertilizer Schedule")
        plt.tight_layout()
        plt.savefig("yield_by_schedule.png")

        plt.show()

    # ---------------------------
    # 10. Fertilizer Schedules
    # ---------------------------
    def generate_random_schedule(self):
        """
        Generate a random nitrogen fertilizer schedule.
        Dates use DSSAT YYDDD format for 2021.
        Example: 21150 = day 150 of 2021.
        """

        # Pick 1 to 3 fertilizer applications
        num_apps = random.randint(1, 3)

        # Possible application days after planting-ish
        possible_dates = list(range(21140, 21181, 5))

        selected_dates = sorted(random.sample(possible_dates, num_apps))

        schedule = []

        for date in selected_dates:
            amount = random.choice([25, 50, 75, 100, 125, 150])
            schedule.append((date, amount))

        return schedule
    
    # ---------------------------
    # 11. Find Best Fertilizer Schedules
    # ---------------------------
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
# TEST RUN
# ---------------------------
if __name__ == "__main__":
    sim = MaizeSimulator()

    best_schedule, best_result, best_score = sim.find_best_schedule(
        num_simulations=50
    )

    print("\nBEST SCHEDULE FOUND:")
    print(best_schedule)
    print("BEST RESULT:")
    print(best_result)
    print("BEST SCORE:")
    print(best_score)

    sim.visualize_results()