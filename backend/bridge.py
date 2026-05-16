"""
LLM-Augmented Traffic Bridge
============================

Sits between the existing ESP32/MQTT system and an Anthropic Claude model.
Subscribes to the existing telemetry topics, maintains a rolling state
window, and exposes two LLM-driven capabilities through a FastAPI endpoint:

  1. Conversational reporting: free-form questions about current and
     recent traffic state, answered in natural language and grounded in
     the live MQTT stream.
  2. Command translation: natural-language operator instructions are
     mapped via Claude's tool-use interface to typed override commands
     and published to iot/traffic/override.

The bridge is overlay-only. It does not modify the existing firmware
control logic; the firmware exposes one new subscription
(iot/traffic/override) and applies bounded-duration overrides that
fall back to adaptive timing when they expire.
"""

import json
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import paho.mqtt.client as mqtt
from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# -------------------- CONFIG --------------------
MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BRIDGE_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8000"))
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

TOPICS = [
    ("iot/traffic/WE/state", 0),
    ("iot/traffic/EW/state", 0),
    ("iot/traffic/WE/queue", 0),
    ("iot/traffic/EW/queue", 0),
]
OVERRIDE_TOPIC = "iot/traffic/override"

# How many state events to keep in the rolling window passed to Claude.
HISTORY_LEN = 120  # ~60s of context at the firmware's ~500ms publish cadence

ALERT_THRESHOLD = 4     # queue >= this triggers a proactive alert
ALERT_COOLDOWN  = 30.0  # seconds before the same direction can alert again


# -------------------- STATE STORE --------------------
@dataclass
class TrafficState:
    """Thread-safe rolling state derived from MQTT telemetry."""
    current: dict[str, Any] = field(default_factory=lambda: {
        "WE": {"state": "UNKNOWN", "queue": 0},
        "EW": {"state": "UNKNOWN", "queue": 0},
    })
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LEN))
    last_update: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, direction: str, field_name: str, value: str):
        with self._lock:
            if direction not in self.current:
                return
            if field_name == "queue":
                try:
                    self.current[direction]["queue"] = int(value)
                except ValueError:
                    return
            elif field_name == "state":
                self.current[direction]["state"] = value
            self.last_update = time.time()
            self.history.append({
                "t": round(time.time(), 2),
                "direction": direction,
                "field": field_name,
                "value": value,
            })

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "current": {k: dict(v) for k, v in self.current.items()},
                "history": list(self.history),
                "last_update": self.last_update,
                "now": time.time(),
            }


state = TrafficState()


# -------------------- ALERT STORE --------------------
@dataclass
class AlertStore:
    """Thread-safe queue of proactive congestion alerts for the dashboard to poll."""
    _queue: deque = field(default_factory=lambda: deque(maxlen=20))
    _last_alert: dict = field(default_factory=lambda: {"WE": 0.0, "EW": 0.0})
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def push(self, text: str):
        with self._lock:
            self._queue.append({"t": time.time(), "text": text})

    def can_alert(self, direction: str) -> bool:
        with self._lock:
            return time.time() - self._last_alert[direction] > ALERT_COOLDOWN

    def mark_alerted(self, direction: str):
        with self._lock:
            self._last_alert[direction] = time.time()

    def drain(self) -> list:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items


alerts = AlertStore()


# -------------------- MQTT CLIENT --------------------
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="llm-bridge")


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[MQTT] connected rc={reason_code}")
    for topic, qos in TOPICS:
        client.subscribe(topic, qos)
        print(f"[MQTT] subscribed {topic}")


def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="replace")
    parts = msg.topic.split("/")
    # Expected: iot/traffic/{WE|EW}/{state|queue}
    if len(parts) == 4 and parts[0] == "iot" and parts[1] == "traffic":
        direction, field_name = parts[2], parts[3]
        state.update(direction, field_name, payload)


mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


def publish_override(payload: dict) -> None:
    body = json.dumps(payload)
    mqtt_client.publish(OVERRIDE_TOPIC, body, qos=1)
    print(f"[OVERRIDE] published {body}")


# -------------------- CLAUDE TOOL SCHEMA --------------------
# A single typed tool. The enums constrain Claude's output so a malformed
# command is impossible: direction is WE or EW, action is one of four
# defined values, and duration_ms is bounded by the firmware as well.
TRAFFIC_TOOL = {
    "name": "traffic_override",
    "description": (
        "Send an override command to the physical traffic light controller. "
        "Use this ONLY when the operator is requesting an action that changes "
        "the lights (extend a green, force a phase, give priority, or reset). "
        "For questions about current or recent traffic state, do NOT use this "
        "tool; answer in natural language using the state in the system prompt."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["extend_green", "force_phase", "set_priority", "reset"],
                "description": (
                    "extend_green: extend the current green for the named direction. "
                    "force_phase: immediately switch to green for the named direction. "
                    "set_priority: hold green for the named direction (typically longer). "
                    "reset: clear any active override and resume adaptive timing."
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["WE", "EW"],
                "description": "Direction the override targets. Omit for action=reset.",
            },
            "duration_ms": {
                "type": "integer",
                "minimum": 1000,
                "maximum": 20000,
                "description": "Override duration in milliseconds. Omit for action=reset.",
            },
        },
        "required": ["action"],
    },
}

EMERGENCY_TOOL = {
    "name": "emergency_priority",
    "description": (
        "Activate emergency vehicle pre-emption. Forces the requested direction "
        "green and locks out adaptive timing for the full duration — the firmware "
        "will not switch phases until the timer expires. Use ONLY when the operator "
        "reports an emergency vehicle (ambulance, fire truck, police) approaching. "
        "This is distinct from set_priority: it cannot be interrupted by queue changes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["WE", "EW"],
                "description": "Direction the emergency vehicle is approaching from.",
            },
            "eta_seconds": {
                "type": "integer",
                "minimum": 5,
                "maximum": 30,
                "description": "How long to hold the green clear, in seconds.",
            },
        },
        "required": ["direction", "eta_seconds"],
    },
}


SYSTEM_PROMPT_TEMPLATE = """You are an operator assistant for a small IoT traffic intersection.

The intersection has two directions: WE (West-East) and EW (East-West).
Each direction has a queue counter (number of cars waiting) and a signal
state (GREEN, YELLOW, RED). The system normally runs adaptive timing
based on queue length. You can issue overrides through the
traffic_override tool.

Live state right now:
{current_state}

Recent events (last ~60 seconds, oldest first):
{recent_events}

Guidance:
- For questions ("which direction is busier?", "what just happened?"),
  answer in 1-3 sentences using the state above. Do not call any tool.
- For commands ("give priority to WE", "extend the busier green by 5
  seconds"), call traffic_override with the right parameters. If the
  operator says "the busier direction" pick whichever currently has the
  higher queue.
- If a request is ambiguous (e.g. "fix the traffic"), ask one short
  clarifying question instead of guessing.
- Keep replies short. Operators are reading them in a live dashboard.
"""


def build_system_prompt() -> str:
    snap = state.snapshot()
    cur = snap["current"]
    age = snap["now"] - snap["last_update"] if snap["last_update"] else None
    age_str = f"{age:.1f}s ago" if age is not None else "no data yet"

    current_lines = [
        f"  WE: state={cur['WE']['state']}, queue={cur['WE']['queue']}",
        f"  EW: state={cur['EW']['state']}, queue={cur['EW']['queue']}",
        f"  Last sensor update: {age_str}",
    ]

    recent = snap["history"][-40:]  # cap context size
    if recent:
        t0 = recent[0]["t"]
        event_lines = [
            f"  +{e['t']-t0:5.1f}s  {e['direction']} {e['field']}={e['value']}"
            for e in recent
        ]
    else:
        event_lines = ["  (no events yet)"]

    return SYSTEM_PROMPT_TEMPLATE.format(
        current_state="\n".join(current_lines),
        recent_events="\n".join(event_lines),
    )


# -------------------- CLAUDE CLIENT --------------------
anthropic_client: Anthropic | None = None
if ANTHROPIC_API_KEY:
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


def handle_tool_call(tool_name: str, tool_input: dict) -> dict:
    """Apply a Claude tool call to the physical system via MQTT."""
    if tool_name == "emergency_priority":
        direction = tool_input.get("direction", "WE")
        eta_s = int(tool_input.get("eta_seconds", 15))
        payload: dict[str, Any] = {
            "action": "emergency_priority",
            "direction": direction,
            "duration_ms": eta_s * 1000,
        }
        publish_override(payload)
        return {"ok": True, "published": payload, "emergency": True}

    # traffic_override
    action = tool_input.get("action")
    direction = tool_input.get("direction")
    duration_ms = tool_input.get("duration_ms", 5000)

    payload = {"action": action}
    if action != "reset":
        if direction not in ("WE", "EW"):
            return {"ok": False, "error": "missing direction"}
        payload["direction"] = direction
        payload["duration_ms"] = int(duration_ms)

    publish_override(payload)
    return {"ok": True, "published": payload}


def chat_with_claude(user_message: str) -> dict:
    """Single-turn (with tool round-trip) chat. Returns a dict with the
    final assistant text, the tool call (if any), the resulting override
    payload (if any), an emergency flag, and a reasoning summary."""
    if anthropic_client is None:
        return {
            "reply": "ANTHROPIC_API_KEY is not set. Add it to .env and restart.",
            "tool_used": None,
            "published": None,
            "emergency": False,
            "reasoning": "",
        }

    snap = state.snapshot()
    system_prompt = build_system_prompt()
    messages = [{"role": "user", "content": user_message}]
    tools = [TRAFFIC_TOOL, EMERGENCY_TOOL]

    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        system=system_prompt,
        tools=tools,
        messages=messages,
    )

    tool_used = None
    published = None
    is_emergency = False

    # If Claude requested a tool, execute it and feed the result back so it
    # can produce a final natural-language confirmation for the operator.
    if response.stop_reason == "tool_use":
        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_block is not None:
            tool_used = {"name": tool_block.name, "input": tool_block.input}
            result = handle_tool_call(tool_block.name, tool_block.input)
            published = result.get("published")
            is_emergency = bool(result.get("emergency"))

            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": json.dumps(result),
                }],
            })

            response = anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=256,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

    reply_text = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    ).strip()
    if not reply_text:
        reply_text = "(no reply)"

    # Build a brief reasoning summary from the snapshot taken before the call.
    cur = snap["current"]
    age = snap["now"] - snap["last_update"] if snap["last_update"] else None
    age_str = f"{age:.0f}s ago" if age is not None else "no data"
    tool_desc = ""
    if tool_used:
        tool_desc = f" Called {tool_used['name']} with {json.dumps(tool_used['input'])}."
    reasoning = (
        f"Saw: WE {cur['WE']['state']} queue={cur['WE']['queue']}, "
        f"EW {cur['EW']['state']} queue={cur['EW']['queue']} "
        f"(data {age_str}).{tool_desc}"
    )

    return {
        "reply": reply_text,
        "tool_used": tool_used,
        "published": published,
        "emergency": is_emergency,
        "reasoning": reasoning,
    }


# -------------------- PROACTIVE ALERT WORKER --------------------
def generate_alert(direction: str, queue_count: int) -> str:
    snap = state.snapshot()
    other = "EW" if direction == "WE" else "WE"
    other_q = snap["current"][other]["queue"]
    return (
        f"⚠ {direction} queue at {queue_count} cars "
        f"(vs {other_q} on {other}). "
        f'Try: "give priority to {direction}"'
    )


_alert_stop = threading.Event()


def alert_worker():
    while not _alert_stop.is_set():
        _alert_stop.wait(timeout=10)
        if _alert_stop.is_set():
            break
        snap = state.snapshot()
        if snap["last_update"] == 0:
            continue
        for direction in ("WE", "EW"):
            q = snap["current"][direction]["queue"]
            if q >= ALERT_THRESHOLD and alerts.can_alert(direction):
                alerts.mark_alerted(direction)
                text = generate_alert(direction, q)
                alerts.push(text)


# -------------------- FASTAPI APP --------------------
class ChatRequest(BaseModel):
    message: str


class InjectRequest(BaseModel):
    topic: str
    value: str


_INJECT_TOPICS = {
    "iot/traffic/WE/queue",
    "iot/traffic/EW/queue",
    "iot/traffic/WE/state",
    "iot/traffic/EW/state",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start MQTT and background workers.
    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()
    alert_thread = threading.Thread(target=alert_worker, daemon=True)
    alert_thread.start()
    try:
        yield
    finally:
        _alert_stop.set()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


app = FastAPI(title="LLM-Augmented Traffic Bridge", lifespan=lifespan)


@app.get("/api/state")
def api_state():
    return JSONResponse(state.snapshot())


@app.get("/api/alerts")
def api_alerts():
    return JSONResponse({"alerts": alerts.drain()})


@app.post("/api/inject")
def api_inject(req: InjectRequest):
    if req.topic not in _INJECT_TOPICS:
        return JSONResponse({"error": "topic not allowed"}, status_code=400)
    mqtt_client.publish(req.topic, req.value, qos=0)
    print(f"[INJECT] {req.topic} = {req.value}")
    return JSONResponse({"ok": True})


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    try:
        result = chat_with_claude(req.message)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse(
            {"reply": f"Error: {e}", "tool_used": None, "published": None,
             "emergency": False, "reasoning": ""},
            status_code=500,
        )


@app.get("/")
def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "dashboard.html"))


# Static files served at /static (the existing dashboard, with the chat panel added).
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "frontend")),
    name="static",
)


if __name__ == "__main__":
    import uvicorn
    print(f"[BRIDGE] Claude model: {CLAUDE_MODEL}")
    print(f"[BRIDGE] MQTT broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"[BRIDGE] Web UI: http://{BRIDGE_HOST}:{BRIDGE_PORT}/")
    uvicorn.run(app, host=BRIDGE_HOST, port=BRIDGE_PORT, log_level="info")
