## models/

This project uses the Anthropic Claude API rather than local model weights.

`model.py` — evaluation helper module derived from `notebooks/analysis.ipynb`.
Exposes `load_results()`, `category_summary()`, `latency_summary()`,
`structural_validity()`, and `refusal_rate()` as importable functions.
Unit-tested by `tests/test_model.py`.
