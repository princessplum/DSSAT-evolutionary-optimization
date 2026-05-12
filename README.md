# DSSAT Evolutionary Optimization

This project focuses on automating DSSAT maize simulations and optimizing nitrogen fertilizer strategies using evolutionary optimization and future surrogate modeling approaches.

## Goals

- Automate DSSAT experiment generation and execution
- Parse DSSAT simulation outputs
- Optimize nitrogen fertilizer schedules
- Balance yield, nitrogen efficiency, and nitrogen loss
- Reduce DSSAT runtime using surrogate models

---

## Current Features

- Automated DSSAT execution pipeline
- Random and evolutionary optimization of fertilizer schedules
- Population-based genetic algorithm with:
  - fitness scoring
  - elite selection
  - mutation
  - crossover
- Automatic parsing of:
  - HARWT (yield)
  - TNLF (nitrogen loss)
  - TNUP (nitrogen uptake)
  - NUE (nitrogen use efficiency)
- CSV result generation
- Visualization of:
  - Yield vs Total Nitrogen
  - NUE vs Total Nitrogen
  - Nitrogen Loss vs Total Nitrogen
  - Yield vs Nitrogen Loss

---

## Planned Features

- Multi-objective optimization
- Improved weighted fitness scoring
- Pareto frontier analysis
- Surrogate machine learning models
- Parsing PlantN.OUT metrics:
  - CNAD
  - GNAD
- Interactive visualizations and automated reports
- Improved soil and environmental configuration

---

## Notes

- DSSAT installation files are not included
- Generated outputs are excluded from version control
- Weather, soil, and cultivar files require local configuration
