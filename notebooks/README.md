## notebooks/

This folder contains Jupyter notebooks for interactive data analysis and visualisation.

**`analysis.ipynb`** — full evaluation analysis of the LLM-augmented traffic bridge:
reads `evaluation/results.csv`, computes per-category accuracy, F1, latency percentiles,
structural validity rate, and appropriate-refusal rate, and renders inline charts.
Run it after executing `evaluation/run_eval.py` against the live bridge.
