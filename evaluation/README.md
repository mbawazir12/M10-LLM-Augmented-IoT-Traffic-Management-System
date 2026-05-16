# Evaluation Suite

Quantitative benchmark for the LLM-augmented traffic bridge.
80 labeled test cases across four categories, a regex baseline, a test runner,
and an analysis script that produces `RESULTS.md` and two plots.

## Prerequisites

- Python 3.10+
- The bridge running at `http://localhost:8000` (see root `README.md`)

## Run end-to-end

```bash
# 1. Install evaluation dependencies (separate from bridge deps)
pip install -r evaluation/requirements.txt

# 2. Run all 80 cases against the live bridge → produces results.csv
python evaluation/run_eval.py

# 3. Compute metrics and generate plots → produces RESULTS.md + plots/
python evaluation/analyze.py
```

## Output files

| File | Description |
|------|-------------|
| `results.csv` | One row per case: utterance, ground truth, LLM response, tool call, latency, baseline response, correctness flags |
| `RESULTS.md` | One-page summary: accuracy table, F1, latency percentiles, validity and refusal rates, both plots embedded |
| `plots/latency_hist.png` | Histogram of LLM response latency with P50/P90/P99 markers |
| `plots/accuracy_comparison.png` | Grouped bar chart: LLM vs baseline accuracy per category |

## Dataset categories

| Category | n | Ground truth |
|----------|---|--------------|
| command | 40 | Tool call with correct action, direction, duration |
| ambiguous | 15 | No tool call (clarifying question expected) |
| out_of_scope | 10 | No tool call (deflection expected) |
| reporting | 15 | No tool call (text answer expected) |

## Override `BRIDGE_URL`

```bash
BRIDGE_URL=http://192.168.1.5:8000 python evaluation/run_eval.py
```
