from pathlib import Path

# Base/template DSSAT experiment file
BASE_FILE = Path("UKLE2102_base.MZX")

# Output folder
OUTPUT_DIR = Path("generated_experiments")

# Base split from medN
BASE_SPLIT = [42, 84, 168]
BASE_TOTAL = sum(BASE_SPLIT)

# Your actual nitrogen levels
TARGET_TOTALS = [147, 294, 441]


def scaled_split(total_n: int):
    raw = [x / BASE_TOTAL * total_n for x in BASE_SPLIT]
    vals = [round(x) for x in raw]

    diff = total_n - sum(vals)
    vals[-1] += diff
    return vals


def replace_fertilizer_amounts(lines, new_amounts):
    new_lines = []
    fert_row_idx = 0

    for line in lines:
        stripped = line.strip()

        if "FE005" in stripped and "AP001" in stripped:
            parts = line.split()

            if len(parts) > 5 and fert_row_idx < len(new_amounts):
                parts[5] = str(new_amounts[fert_row_idx])
                fert_row_idx += 1
                line = " ".join(parts) + "\n"

        new_lines.append(line)

    return new_lines


def make_mzx_for_total_n(template_path, output_path, total_n):
    lines = template_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

    new_amounts = scaled_split(total_n)
    new_lines = replace_fertilizer_amounts(lines, new_amounts)

    output_path.write_text("".join(new_lines), encoding="utf-8")

    print(f"Created {output_path.name} with split {new_amounts}")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    for total_n in TARGET_TOTALS:
        out_file = OUTPUT_DIR / f"UKLE2102_N{total_n}.MZX"
        make_mzx_for_total_n(BASE_FILE, out_file, total_n)


if __name__ == "__main__":
    main()