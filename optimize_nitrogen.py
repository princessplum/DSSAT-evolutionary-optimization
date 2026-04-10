from pathlib import Path
import pandas as pd

DATA_FILE = Path("dssat_summary.csv")


def load_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Could not find {path.resolve()}")

    df = pd.read_csv(path)

    # Add NUE if it is not already in the CSV
    if "nue" not in df.columns:
        df["nue"] = df["yield_kg_ha"] / df["n_rate"]

    return df


def optimize_for_yield(df: pd.DataFrame) -> pd.Series:
    return df.loc[df["yield_kg_ha"].idxmax()]


def optimize_for_nue(df: pd.DataFrame) -> pd.Series:
    return df.loc[df["nue"].idxmax()]


def optimize_for_low_n2o(df: pd.DataFrame) -> pd.Series:
    return df.loc[df["total_n2o_kg_ha"].idxmin()]


def optimize_weighted_tradeoff(
    df: pd.DataFrame,
    yield_weight: float = 1.0,
    n2o_weight: float = 10000.0,
    denit_weight: float = 1000.0,
    nitrif_weight: float = 10.0
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Higher score is better.
    Adjust weights depending on how strongly you want to penalize losses.
    """

    scored = df.copy()

    scored["objective_score"] = (
        yield_weight * scored["yield_kg_ha"]
        - n2o_weight * scored["total_n2o_kg_ha"]
        - denit_weight * scored["total_denit_kg_ha"]
        - nitrif_weight * scored["total_nitrif_kg_ha"]
    )

    best_row = scored.loc[scored["objective_score"].idxmax()]
    return best_row, scored


def print_result(title: str, row: pd.Series) -> None:
    print(f"\n=== {title} ===")
    print(f"Treatment: {row['treatment_id']}")
    print(f"N rate: {row['n_rate']}")
    print(f"Yield: {row['yield_kg_ha']}")
    print(f"ET: {row['total_et_mm']}")
    print(f"N2O: {row['total_n2o_kg_ha']}")
    print(f"Denit: {row['total_denit_kg_ha']}")
    print(f"Nitrif: {row['total_nitrif_kg_ha']}")
    print(f"NUE: {row['nue']:.3f}")
    if "objective_score" in row.index:
        print(f"Objective score: {row['objective_score']:.3f}")


def main():
    df = load_data(DATA_FILE)

    print("Input dataset:")
    print(df)

    # Optimization result 1: maximize yield
    best_yield = optimize_for_yield(df)
    print_result("Best for Maximum Yield", best_yield)

    # Optimization result 2: maximize NUE
    best_nue = optimize_for_nue(df)
    print_result("Best for Maximum NUE", best_nue)

    # Optimization result 3: minimize N2O
    best_low_n2o = optimize_for_low_n2o(df)
    print_result("Best for Minimum N2O", best_low_n2o)

    # Optimization result 4: weighted tradeoff
    best_tradeoff, scored_df = optimize_weighted_tradeoff(df)
    print_result("Best for Weighted Tradeoff", best_tradeoff)

    scored_df.to_csv("optimization_results.csv", index=False)
    print("\nSaved scored results to optimization_results.csv")


if __name__ == "__main__":
    main()