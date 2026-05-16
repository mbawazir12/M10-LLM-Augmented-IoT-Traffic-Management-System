"""
Pure-regex baseline matcher for the traffic override tool schema.
Deterministic and self-contained — no LLM calls, no external state.
Returns the same tool-call structure the bridge uses.
"""

import re
from typing import Optional

VALID_ACTIONS = {"extend_green", "force_phase", "set_priority", "reset"}
VALID_DIRECTIONS = {"WE", "EW"}

_WORD_TO_SEC = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}

_WE_RE = re.compile(
    r"\b(WE|west[- ]?east|westbound|west\s+to\s+east|from\s+(?:the\s+)?west)\b",
    re.IGNORECASE,
)
_EW_RE = re.compile(
    r"\b(EW|east[- ]?west|eastbound|east\s+to\s+west|from\s+(?:the\s+)?east)\b",
    re.IGNORECASE,
)

_EMERGENCY_RE = re.compile(
    r"\b(ambulance|fire\s*truck|emergency\s*vehicle|police\s*car|paramedic|pre[- ]?empt)\b",
    re.IGNORECASE,
)
_RESET_RE = re.compile(
    r"\b(reset|clear\s+(?:the\s+)?override|resume\s+(?:normal|adaptive)|"
    r"cancel\s+(?:the\s+)?override|normal\s+operation|back\s+to\s+adaptive|"
    r"go\s+back\s+to\s+adaptive)\b",
    re.IGNORECASE,
)
_EXTEND_RE = re.compile(
    r"\b(extend|add\b.*\bgreen|more\s+green|extra\s+green|give\b.*\bmore\s+(?:green\s+)?time|"
    r"increase\b.*\bgreen|more\s+seconds?\s+of\s+green|add\s+\d+\s+seconds?)\b",
    re.IGNORECASE,
)
_FORCE_RE = re.compile(
    r"\b(force\b|switch\s+to\b|immediately\s+switch|give\b.*\bgreen\s+right\s+now|"
    r"make\b.*\bgo\s+green\s+right\s+now|right\s+now|immediately)\b",
    re.IGNORECASE,
)
_PRIORITY_RE = re.compile(
    r"\b(priorit(?:y|ize)|hold\b.*\bgreen|set\b.*\bpriority|give\b.*\bpriority)\b",
    re.IGNORECASE,
)

_DUR_DIGIT_RE = re.compile(r"\b(\d+)\s*(?:seconds?|s\b)", re.IGNORECASE)
_DUR_WORD_RE = re.compile(
    r"\b(" + "|".join(_WORD_TO_SEC.keys()) + r")\s+seconds?\b",
    re.IGNORECASE,
)


def _match_direction(text: str) -> Optional[str]:
    we = bool(_WE_RE.search(text))
    ew = bool(_EW_RE.search(text))
    if we and not ew:
        return "WE"
    if ew and not we:
        return "EW"
    return None  # ambiguous or absent


def _match_duration_ms(text: str) -> Optional[int]:
    m = _DUR_DIGIT_RE.search(text)
    if m:
        return max(1000, min(20000, int(m.group(1)) * 1000))
    m = _DUR_WORD_RE.search(text)
    if m:
        secs = _WORD_TO_SEC.get(m.group(1).lower(), 0)
        return max(1000, min(20000, secs * 1000))
    return None


def _match_action(text: str) -> Optional[str]:
    if _EMERGENCY_RE.search(text):
        return "emergency_priority"
    if _RESET_RE.search(text):
        return "reset"
    if _EXTEND_RE.search(text):
        return "extend_green"
    if _FORCE_RE.search(text):
        return "force_phase"
    if _PRIORITY_RE.search(text):
        return "set_priority"
    return None


def predict(utterance: str) -> dict:
    """
    Returns {"tool_call": <tool_call_dict>} or {"tool_call": None}.
    tool_call_dict matches the bridge's tool_used format:
      {"name": "...", "input": {...}}
    """
    action = _match_action(utterance)

    if action is None:
        return {"tool_call": None}

    if action == "reset":
        return {
            "tool_call": {
                "name": "traffic_override",
                "input": {"action": "reset"},
            }
        }

    direction = _match_direction(utterance)

    if action == "emergency_priority":
        if direction is None:
            return {"tool_call": None}
        dur_ms = _match_duration_ms(utterance)
        eta_s = max(5, min(30, (dur_ms // 1000) if dur_ms else 15))
        return {
            "tool_call": {
                "name": "emergency_priority",
                "input": {"direction": direction, "eta_seconds": eta_s},
            }
        }

    # extend_green / force_phase / set_priority all need a direction
    if direction is None:
        return {"tool_call": None}

    dur_ms = _match_duration_ms(utterance)
    inp = {
        "action": action,
        "direction": direction,
        "duration_ms": dur_ms if dur_ms is not None else 5000,
    }
    return {
        "tool_call": {
            "name": "traffic_override",
            "input": inp,
        }
    }
