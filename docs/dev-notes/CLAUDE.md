# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ESP32-based adaptive traffic intersection augmented with a Claude LLM. The Python bridge connects an MQTT-publishing ESP32 to the Anthropic API, translating natural-language operator commands into MQTT override actions and answering live traffic questions via a FastAPI server and browser dashboard.

## Running the bridge

```bash
# Install dependencies (Python 3.10+ required)
pip install -r requirements.txt

# Copy env template and set your API key
cp .env.example .env   # then edit .env

# Run from the bridge/ directory
cd bridge
python bridge.py
```

Dashboard: http://localhost:8000

Environment variables (all optional beyond the API key):

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Required. Anthropic key. |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | Model to use |
| `MQTT_BROKER` | `broker.hivemq.com` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `BRIDGE_HOST` | `0.0.0.0` | Listen address |
| `BRIDGE_PORT` | `8000` | Listen port |

## Firmware

Open `firmware/smart_traffic.ino` in Arduino IDE. Requires **ArduinoJson v6** from Library Manager. Wi-Fi credentials and MQTT broker are hardcoded at the top of the `.ino` file. Upload to ESP32-WROOM-32S.

## Architecture

```
[ESP32 + HC-SR04 ultrasonics + LEDs]
        | publishes iot/traffic/{WE,EW}/{state,queue}
        | publishes iot/traffic/phase (remaining_ms countdown)
        | subscribes iot/traffic/override
        v
[HiveMQ public broker — broker.hivemq.com:1883]
        v
[bridge/bridge.py — FastAPI + paho-mqtt]
        | TrafficState: thread-safe rolling deque(maxlen=120) ~60 s
        | AlertStore: proactive congestion alerts (queue >= 4, 30 s cooldown)
        | POST /api/chat → chat_with_claude()
        v
[Claude API — tool-use, two tools]
        | traffic_override(action, direction, duration_ms)
        | emergency_priority(direction, eta_seconds)
        v
[browser dashboard + chat panel — bridge/static/dashboard.html]
```

### Key design points

**Override priority in firmware:** `emergencyActive` > `overrideActive` > adaptive timing. Duration is clamped in firmware regardless of what the bridge sends (normal 1–20 s, emergency 5–30 s). When an override expires the firmware transitions through yellow before resuming adaptive timing — it does not snap directly back.

**Claude tool-use flow in `chat_with_claude()`:** single-turn with one optional tool round-trip. If `stop_reason == "tool_use"`, `handle_tool_call()` publishes to MQTT, the result is appended to messages, and a second API call produces the operator-facing confirmation text.

**State is in-memory only.** `TrafficState.history` is a `deque(maxlen=120)` (~60 s at the firmware's 500 ms publish cadence). The last 40 events are passed to Claude as context in each system prompt.

**Injection endpoint for testing without hardware.** `POST /api/inject` accepts only the four telemetry topics (`iot/traffic/{WE,EW}/{state,queue}`) and publishes them to the broker so the bridge picks them up as if the ESP32 sent them.

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Dashboard HTML |
| GET | `/api/state` | Raw `TrafficState` snapshot |
| GET | `/api/alerts` | Drain proactive congestion alerts |
| POST | `/api/chat` | `{"message": "..."}` → Claude response |
| POST | `/api/inject` | `{"topic": "...", "value": "..."}` → simulate sensor |
