# DSSAT Evolutionary Optimization

This project focuses on automating DSSAT simulations and optimizing nitrogen fertilizer strategies using evolutionary algorithms and surrogate modeling.

## Goals
- Automate DSSAT experiment generation and execution
- Parse simulation outputs (yield, ET, N2O, soil nitrogen)
- Optimize nitrogen application strategies
- Reduce DSSAT runtime using surrogate models

## Current Components
- `generate_experiments.py`: Creates DSSAT experiment files with varying parameters
- `optimize_nitrogen.py`: Optimization logic for nitrogen strategies
- `parse_outputs.py`: Extracts metrics from DSSAT output files
- `make_plots.py`: Visualization of results

## Planned Features
- Evolutionary optimization (genetic algorithms)
- Surrogate model for fast DSSAT approximation
- Automated DSSAT execution pipeline
- Multi-objective optimization (yield vs nitrogen loss)

## Notes
- DSSAT installation files are not included
- Generated experiment files and outputs are excluded from version control