# Smart Traffic & Congestion Control — LLM-Augmented

An ESP32-based adaptive traffic intersection augmented with a Claude language model. Operators interact with the system through a live browser dashboard and a natural-language chat panel. Claude translates commands like *"give priority to the busier direction"* into typed MQTT override actions and answers questions about live traffic state.

---

## Repository layout

```
firmware/
  smart_traffic.ino       ESP32 firmware — adaptive timing + override logic

backend/
  bridge.py               Python bridge: MQTT client + Claude tool-use + FastAPI server
  static/
    dashboard.html        Live dashboard with signal cards and chat panel

requirements.txt          Python dependencies
.env.example              Copy to .env and fill in your API key
CLAUDE.md                 Developer reference for Claude Code
```

---

## Architecture

```
[ESP32 + HC-SR04 ultrasonics + LEDs]
        │  publishes iot/traffic/{WE,EW}/{state,queue,phase}
        │  subscribes iot/traffic/override
        ▼
[HiveMQ public broker  (broker.hivemq.com:1883)]
        ▼
[backend/bridge.py  —  FastAPI + paho-mqtt]
        │  rolling 60 s state window
        │  proactive congestion alerts
        ▼
[Claude API]   tools: traffic_override · emergency_priority
        ▼
[Browser dashboard + chat panel]
```

The bridge is **overlay-only**. It cannot break adaptive timing: the firmware enforces hard duration clamps (normal overrides 1–20 s, emergency 5–30 s) and resumes adaptive timing automatically when an override expires.

---

## Hardware

| Component | Detail |
|-----------|--------|
| MCU | ESP32-WROOM-32S |
| Sensors | 4× HC-SR04 ultrasonic (entry + exit per direction) |
| LEDs | 6 (green/yellow/red × WE and EW) |
| Connectivity | Wi-Fi → HiveMQ public MQTT broker |

---

## Setup

### 1. Get a Closed Source API key (i.e. Claude, GPT, Gemini)



### 2. Python environment

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set your key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Optional `.env` overrides:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAUDE_MODEL` | `claude-haiku-4-5` | Anthropic model to use |
| `MQTT_BROKER` | `broker.hivemq.com` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `BRIDGE_HOST` | `0.0.0.0` | Bridge listen address |
| `BRIDGE_PORT` | `8000` | Bridge listen port |

### 3. Flash the firmware

Open `firmware/smart_traffic.ino` in the Arduino IDE. Install the **ArduinoJson v6** library via Library Manager before uploading. Update the Wi-Fi credentials at the top of the file, then upload to the ESP32.

---

## Running

1. Power the ESP32 — LEDs should start cycling.
2. Start the bridge:

```bash
cd backend
python bridge.py
```

3. Open **http://localhost:8000** in a browser.

The dashboard shows live signal states and queue counts for both directions (WE and EW). The chat panel on the right accepts natural-language commands and questions.

---

## What you can ask

**Questions** (no hardware action):
- *"Which direction has more traffic right now?"*
- *"What happened in the last 30 seconds?"*

**Commands** (issues an MQTT override):
- *"Extend the green for WE by 8 seconds."*
- *"Give priority to the busier direction."*
- *"Force EW green for 10 seconds."*
- *"Reset to adaptive timing."*

**Emergency pre-emption**:
- *"There's an ambulance coming from the west — hold WE green for 20 seconds."*

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/state` | Live TrafficState snapshot (JSON) |
| `GET` | `/api/alerts` | Drain proactive congestion alerts |
| `POST` | `/api/chat` | `{"message": "..."}` → Claude reply + optional override |
| `POST` | `/api/inject` | Simulate a sensor event without hardware |

`/api/inject` accepts only the four telemetry topics (`iot/traffic/{WE,EW}/{state,queue}`) and is intended for demo/testing without a connected ESP32.

---

## Team

**Students**

| Name | Student ID |
|------|------------|
| Zakariya Ba Alawi | 220027 |
| Mohammed Bawazir | 230035 |
| Ahmed Bin Halabi | 220026 |
| Saad Alkeridis | 220621 |
| Mohammed Haythem | 220601 |

**Supervisor:** Prof. Nidal Nasser

---

## Stack

| Layer | Technology |
|-------|-----------|
| Hardware | ESP32, HC-SR04, Arduino C++ |
| Firmware libraries | `PubSubClient`, `WiFi`, `ArduinoJson v6` |
| MQTT broker | HiveMQ public broker (plain MQTT 1883 from firmware, WSS 8884 from browser) |
| Bridge | Python 3.10+, `paho-mqtt`, `anthropic`, `fastapi`, `uvicorn`, `python-dotenv` |
| LLM | Claude via Anthropic tool-use API |
| Frontend | Bootstrap 5, MQTT.js, fetch-based chat client |
