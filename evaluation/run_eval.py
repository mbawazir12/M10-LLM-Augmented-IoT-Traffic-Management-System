"""
Evaluation runner for the LLM-augmented traffic bridge.

For each case in dataset.json:
  1. POST /api/inject to seed sensor state (if specified).
  2. POST /api/chat with the utterance; record wall-clock latency.
  3. Run the regex baseline locally.
  4. Evaluate correctness for both systems.
  5. Append one row to results.csv.

The bridge must be running at http://localhost:8000 (or BRIDGE_URL env var).
"""

import csv
import json
import os
import sys
import time

import requests

import baseline as bl

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://localhost:8000").rstrip("/")
DATASET_PATH = os.path.join(os.path.dirname(__file__), "dataset.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "results.csv")
INJECT_SETTLE_S = 0.4  # seconds to wait after injecting state

CSV_FIELDS = [
    "case_id",
    "category",
    "utterance",
    "ground_truth",
    "llm_response",
    "llm_tool_call",
    "llm_latency_ms",
    "baseline_response",
    "llm_correct",
    "baseline_correct",
]


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

def check_bridge() -> None:
    try:
        r = requests.get(f"{BRIDGE_URL}/api/state", timeout=5)
        r.raise_for_status()
    except Exception as exc:
        print(
            f"\nERROR: Cannot reach bridge at {BRIDGE_URL}/api/state\n"
            f"  Detail: {exc}\n\n"
            f"Start the bridge first:\n"
            f"  cd bridge && python bridge.py\n"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Inject helper
# ---------------------------------------------------------------------------

def inject_state(ops: list[dict]) -> None:
    for op in ops:
        try:
            requests.post(
                f"{BRIDGE_URL}/api/inject",
                json={"topic": op["topic"], "value": op["value"]},
                timeout=5,
            )
        except Exception as exc:
            print(f"  [WARN] inject failed for {op['topic']}: {exc}")
    time.sleep(INJECT_SETTLE_S)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_llm(utterance: str) -> tuple[dict, float]:
    """Returns (bridge_json_response, latency_ms)."""
    t0 = time.perf_counter()
    r = requests.post(
        f"{BRIDGE_URL}/api/chat",
        json={"message": utterance},
        timeout=60,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return r.json(), latency_ms


# ---------------------------------------------------------------------------
# Correctness evaluation
# ---------------------------------------------------------------------------

def _tool_call_correct(tool_call: dict | None, gt: dict) -> bool:
    """Check whether a tool_call dict (or None) satisfies ground_truth."""
    if not gt["expects_tool_call"]:
        return tool_call is None

    if tool_call is None:
        return False

    name = tool_call.get("name")
    inp = tool_call.get("input", {})

    if name != gt["tool_name"]:
        return False

    if name == "traffic_override":
        if inp.get("action") != gt["action"]:
            return False
        expected_dir = gt["direction"]
        if expected_dir and inp.get("direction") != expected_dir:
            return False
        lo = gt["duration_ms_min"]
        hi = gt["duration_ms_max"]
        if lo is not None and hi is not None:
            dur = inp.get("duration_ms", 0)
            if not (lo <= dur <= hi):
                return False

    elif name == "emergency_priority":
        expected_dir = gt["direction"]
        if expected_dir and inp.get("direction") != expected_dir:
            return False
        lo = gt["eta_seconds_min"]
        hi = gt["eta_seconds_max"]
        if lo is not None and hi is not None:
            eta = inp.get("eta_seconds", 0)
            if not (lo <= eta <= hi):
                return False

    return True


def evaluate(case: dict, tool_call: dict | None) -> bool:
    return _tool_call_correct(tool_call, case["ground_truth"])


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    check_bridge()

    with open(DATASET_PATH, encoding="utf-8") as f:
        dataset = json.load(f)

    total = len(dataset)
    print(f"Loaded {total} cases from {DATASET_PATH}")
    print(f"Bridge: {BRIDGE_URL}")
    print(f"Output: {OUTPUT_PATH}\n")

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for i, case in enumerate(dataset, 1):
            cid = case["case_id"]
            cat = case["category"]
            utt = case["utterance"]
            gt = case["ground_truth"]
            inject_ops = case.get("inject") or []

            print(f"[{i:02d}/{total}] {cid} ({cat})", end=" ... ", flush=True)

            # Inject state
            if inject_ops:
                inject_state(inject_ops)

            # LLM call
            llm_tool_call: dict | None = None
            llm_response_text = ""
            llm_latency_ms = -1.0
            llm_ok = False

            try:
                resp, llm_latency_ms = call_llm(utt)
                llm_response_text = resp.get("reply", "")
                llm_tool_call = resp.get("tool_used")  # None or {"name":..,"input":..}
                llm_ok = True
            except Exception as exc:
                llm_response_text = f"ERROR: {exc}"
                print(f"LLM ERROR: {exc}")

            # Baseline call
            bl_result = bl.predict(utt)
            baseline_tool_call: dict | None = bl_result.get("tool_call")

            # Evaluate
            llm_correct = evaluate(case, llm_tool_call) if llm_ok else False
            baseline_correct = evaluate(case, baseline_tool_call)

            status = "OK" if llm_correct else "FAIL"
            print(f"{status}  ({llm_latency_ms:.0f} ms)")

            writer.writerow({
                "case_id": cid,
                "category": cat,
                "utterance": utt,
                "ground_truth": json.dumps(gt),
                "llm_response": llm_response_text,
                "llm_tool_call": json.dumps(llm_tool_call),
                "llm_latency_ms": round(llm_latency_ms, 1),
                "baseline_response": json.dumps(baseline_tool_call),
                "llm_correct": llm_correct,
                "baseline_correct": baseline_correct,
            })

    llm_passed = sum(1 for r in open(OUTPUT_PATH, encoding="utf-8").readlines()[1:]
                     if r.split(",")[-2].strip().lower() == "true")
    print(f"\nDone. {llm_passed}/{total} LLM correct.  Results → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
