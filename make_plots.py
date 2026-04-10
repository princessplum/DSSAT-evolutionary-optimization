import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

df = pd.read_csv("optimization_results.csv")

PLOT_DIR = Path("plots")
PLOT_DIR.mkdir(exist_ok=True)

plt.figure()
plt.plot(df["n_rate"], df["objective_score"], marker="o")
plt.xlabel("Nitrogen rate")
plt.ylabel("Objective score")
plt.title("Weighted Objective Score vs Nitrogen Rate")
plt.savefig(PLOT_DIR / "objective_score_vs_n.png", bbox_inches="tight")
plt.close()

# =====================
# Yield vs N
# =====================
plt.figure()
plt.plot(df["n_rate"], df["yield_kg_ha"], marker="o")
plt.xlabel("Nitrogen rate")
plt.ylabel("Yield (kg/ha)")
plt.title("Yield vs Nitrogen Rate")
plt.savefig(PLOT_DIR / "yield_vs_n.png", bbox_inches="tight")
plt.close()

# =====================
# N2O vs N
# =====================
plt.figure()
plt.plot(df["n_rate"], df["total_n2o_kg_ha"], marker="o")
plt.xlabel("Nitrogen rate")
plt.ylabel("Total N2O (kg/ha)")
plt.title("N2O Emissions vs Nitrogen Rate")
plt.savefig(PLOT_DIR / "n2o_vs_n.png", bbox_inches="tight")
plt.close()

# =====================
# Denitrification vs N
# =====================
plt.figure()
plt.plot(df["n_rate"], df["total_denit_kg_ha"], marker="o")
plt.xlabel("Nitrogen rate")
plt.ylabel("Total Denitrification (kg/ha)")
plt.title("Denitrification vs Nitrogen Rate")
plt.savefig(PLOT_DIR / "denit_vs_n.png", bbox_inches="tight")
plt.close()

# =====================
# Nitrification vs N
# =====================
plt.figure()
plt.plot(df["n_rate"], df["total_nitrif_kg_ha"], marker="o")
plt.xlabel("Nitrogen rate")
plt.ylabel("Total Nitrification (kg/ha)")
plt.title("Nitrification vs Nitrogen Rate")
plt.savefig(PLOT_DIR / "nitrif_vs_n.png", bbox_inches="tight")
plt.close()

# =====================
# NUE
# =====================
plt.figure()
plt.plot(df["n_rate"], df["nue"], marker="o")
plt.xlabel("Nitrogen rate")
plt.ylabel("Nitrogen Use Efficiency")
plt.title("NUE vs Nitrogen Rate")
plt.savefig(PLOT_DIR / "nue_vs_n.png", bbox_inches="tight")
plt.close()

print(f"Plots saved in: {PLOT_DIR.resolve()}")