from pathlib import Path
import pandas as pd

# =========================
# PARSE EVALUATE.OUT (Yield)
# =========================
def parse_evaluate_out(path):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    header = None
    data = None

    for line in lines:
        if line.startswith("@RUN"):
            header = line.split()
        elif header and line.strip() and not line.startswith("*") and not line.startswith("@"):
            data = line.split()
            break

    if not header or not data:
        raise ValueError(f"Could not parse Evaluate.OUT: {path}")

    row = dict(zip(header, data))

    return {
        "yield_kg_ha": float(row["HWAMS"])
    }


# =========================
# PARSE ET.OUT (Total ET)
# =========================
def parse_et_out(path):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    header = None
    last_data = None

    for line in lines:
        if line.startswith("@YEAR"):
            header = line.split()
        elif header and line.strip() and not line.startswith("*") and not line.startswith("@"):
            last_data = line.split()

    if not header or not last_data:
        raise ValueError(f"Could not parse ET.OUT: {path}")

    row = dict(zip(header, last_data))

    return {
        "total_et_mm": float(row["ETAC"])
    }


# =========================
# PARSE N2O.OUT (Nitrogen losses)
# =========================
def parse_n2o_out(path):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    header = None
    last_data = None

    for line in lines:
        if line.startswith("@YEAR"):
            header = line.split()
        elif header and line.strip() and not line.startswith("*") and not line.startswith("@"):
            last_data = line.split()

    if not header or not last_data:
        raise ValueError(f"Could not parse N2O.OUT: {path}")

    row = dict(zip(header, last_data))

    return {
        "total_n2o_kg_ha": float(row["N2OEC"]),
        "total_denit_kg_ha": float(row["NDNC"]),
        "total_nitrif_kg_ha": float(row["NITC"])
    }


# =========================
# COMBINE ONE RUN
# =========================
def extract_run(run_folder, treatment_id, n_rate):
    row = {
        "treatment_id": treatment_id,
        "n_rate": n_rate
    }

    row.update(parse_evaluate_out(run_folder / "Evaluate.OUT"))
    row.update(parse_et_out(run_folder / "ET.OUT"))
    row.update(parse_n2o_out(run_folder / "N2O.OUT"))

    # Add NUE
    row["nue"] = row["yield_kg_ha"] / n_rate if n_rate > 0 else 0

    return row

# =========================
# MAIN SCRIPT
# =========================
if __name__ == "__main__":
    runs = [
        {"id": "N147", "n": 147, "path": Path("runs/N147")},
        {"id": "N294", "n": 294, "path": Path("runs/N294")},
        {"id": "N441", "n": 441, "path": Path("runs/N441")},
    ]

    rows = []

    for r in runs:
        print(f"Processing {r['id']}...")
        row = extract_run(r["path"], r["id"], r["n"])
        rows.append(row)

    df = pd.DataFrame(rows)

    # Save CSV
    df.to_csv("dssat_summary.csv", index=False)

    print("\nFinal dataset:")
    print(df)
    print("\nSaved to dssat_summary.csv")