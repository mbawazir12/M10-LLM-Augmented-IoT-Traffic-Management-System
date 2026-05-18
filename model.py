"""Notebook-derived evaluation script for the traffic LLM project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RESULTS_CSV = Path(__file__).resolve().parent / 'evaluation' / 'results.csv'

CATEGORIES = ['command', 'ambiguous', 'out_of_scope', 'reporting']
CAT_LABELS = {
    'command':      'Command',
    'ambiguous':    'Ambiguous',
    'out_of_scope': 'Out-of-Scope',
    'reporting':    'Reporting',
}


def load_results(path: Path = RESULTS_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    df['llm_correct'] = df['llm_correct'].map(lambda v: str(v).strip().lower() in ('true', '1', 'yes'))
    df['baseline_correct'] = df['baseline_correct'].map(lambda v: str(v).strip().lower() in ('true', '1', 'yes'))
    df['llm_latency_ms'] = pd.to_numeric(df['llm_latency_ms'], errors='coerce')
    return df


def is_tool_call_empty(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    if not isinstance(value, str):
        return True
    return not value.strip() or value.strip().lower() in ('null', 'none')


def is_structurally_valid(tool_call_json: Any) -> bool | None:
    if is_tool_call_empty(tool_call_json):
        return None
    if not isinstance(tool_call_json, str):
        return None
    try:
        tc = json.loads(tool_call_json)
    except (json.JSONDecodeError, TypeError):
        return False

    if tc is None:
        return None

    name = tc.get('name')
    inp = tc.get('input', {})

    if name != 'traffic_override':
        return False

    action = inp.get('action')
    direction = inp.get('direction')
    duration = inp.get('duration_ms')

    if action not in ('extend_green', 'force_green', 'hold_green', 'reset'):
        return False
    if direction not in ('WE', 'EW'):
        return False
    if not isinstance(duration, (int, float)) or duration <= 0:
        return False

    return True


def category_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cat in CATEGORIES:
        subset = df[df['category'] == cat]
        llm_acc = subset['llm_correct'].mean() if len(subset) > 0 else 0.0
        bl_acc = subset['baseline_correct'].mean() if len(subset) > 0 else 0.0
        rows.append((CAT_LABELS[cat], len(subset), llm_acc, bl_acc))
    return pd.DataFrame(rows, columns=['Category', 'N', 'LLM Accuracy', 'Regex Baseline'])


def latency_summary(df: pd.DataFrame) -> dict[str, float]:
    valid = df[df['llm_latency_ms'] >= 0]['llm_latency_ms']
    return {
        'p50': float(valid.quantile(0.50)),
        'p90': float(valid.quantile(0.90)),
        'p99': float(valid.quantile(0.99)),
    }


def refusal_rate(df: pd.DataFrame) -> float:
    mask = df['category'].isin(['ambiguous', 'out_of_scope'])
    subset = df[mask]
    if len(subset) == 0:
        return float('nan')
    return subset['llm_tool_call'].apply(is_tool_call_empty).mean()


def structural_validity(df: pd.DataFrame) -> float:
    tool_calls = df['llm_tool_call'].map(is_structurally_valid)
    valid_calls = tool_calls[tool_calls.notna()]
    if len(valid_calls) == 0:
        return float('nan')
    return float((valid_calls == True).mean())


def print_summary(df: pd.DataFrame) -> None:
    print(f"Loaded {len(df)} evaluation cases across {df['category'].nunique()} categories")
    print('\nCategory accuracy:')
    summary = category_summary(df)
    print(summary.to_string(index=False, float_format='%.3f'))

    latency = latency_summary(df)
    print('\nLatency summary (ms):')
    print(f"P50={latency['p50']:.0f}, P90={latency['p90']:.0f}, P99={latency['p99']:.0f}")

    llm_accuracy = df['llm_correct'].mean()
    baseline_accuracy = df['baseline_correct'].mean()
    print(f"\nOverall accuracy — LLM: {llm_accuracy:.3%}")
    print(f"Overall accuracy — Regex Baseline: {baseline_accuracy:.3%}")

    structural = structural_validity(df)
    print(f"Structural validity: {structural:.3%} (non-null tool calls)")
    print(f"Appropriate refusal rate: {refusal_rate(df):.3%}")


def main() -> None:
    df = load_results()
    print_summary(df)


if __name__ == '__main__':
    main()
