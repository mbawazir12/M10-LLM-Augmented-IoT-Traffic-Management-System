# Product Overview

## What it is

A smart traffic intersection controller that adapts signal timing to real-time vehicle queues — and lets an operator issue natural-language commands to override or query the system through a browser chat panel.

The system was built as an academic project (SE322 — IoT + LLM Augmentation) at Alfaisal University under the supervision of Prof. Nidal Nasser.

---

## Problem

Fixed-cycle traffic lights waste green time on empty lanes and hold up congested ones. Human operators who could respond to incidents (emergency vehicles, road events) have no easy way to interact with embedded intersection controllers in real time.

---

## Solution

Two complementary layers on top of a standard intersection controller:

**1. Adaptive timing (firmware layer)**
The ESP32 measures vehicle queues using ultrasonic sensors at the entry and exit of each lane. Green phase duration scales linearly with queue length (base 5 s + 1 s per car, capped at 15 s). No cloud dependency — this runs entirely on-device.

**2. LLM operator interface (bridge layer)**
A Python bridge subscribes to the live MQTT telemetry stream and exposes a chat endpoint. Operators type plain English; Claude translates commands into typed MQTT override actions and answers questions grounded in the live sensor state.

---

## Key capabilities

| Capability | How it works |
|------------|-------------|
| Adaptive green timing | Queue-proportional duration computed on ESP32 each cycle |
| Natural-language commands | Claude tool-use maps operator text to `traffic_override` MQTT messages |
| Emergency pre-emption | Dedicated `emergency_priority` tool locks the intersection for a specified duration, bypassing queue logic |
| Proactive congestion alerts | Bridge polls queue state every 10 s; pushes dashboard alerts when a queue reaches ≥ 4 cars |
| Live dashboard | Bootstrap + MQTT.js page shows signal states, queue counts, and phase countdown in real time |
| Hardware-free testing | `/api/inject` endpoint simulates sensor events so the full stack can be exercised without the ESP32 |

---

## Safety constraints

The LLM layer cannot crash or lock the intersection. All safety boundaries are enforced in firmware, not software:

- Override durations are hard-clamped to 1–20 s (emergency: 5–30 s) regardless of what the bridge sends.
- When an override expires the firmware transitions through yellow before resuming adaptive timing.
- A `reset` command clears any active override immediately and returns to adaptive timing.
- The bridge has no write access to firmware logic — it can only publish to the single `iot/traffic/override` topic.

---

## Intersection layout

```
          [EW entry sensor]
                |
  ← ← ← ← ← ← ← ← ← ← ←   East-West (EW)
                |
[WE     ]  ----+----  [WE     ]
[exit   ]       |      [entry  ]
[sensor ]  West-East  [sensor ]
                |
  → → → → → → → → → → → →
                |
          [EW exit sensor]
```

Two directions: **WE** (West → East) and **EW** (East → West). Each has an entry sensor (increments queue) and an exit sensor (decrements queue).

---

## MQTT topics

| Topic | Direction | Published by |
|-------|-----------|-------------|
| `iot/traffic/WE/state` | WE | ESP32 |
| `iot/traffic/WE/queue` | WE | ESP32 |
| `iot/traffic/EW/state` | EW | ESP32 |
| `iot/traffic/EW/queue` | EW | ESP32 |
| `iot/traffic/phase` | both | ESP32 (remaining_ms + active direction) |
| `iot/traffic/override` | — | Bridge → ESP32 |

---

## Override command reference

```json
{"action": "extend_green",  "direction": "WE", "duration_ms": 8000}
{"action": "force_phase",   "direction": "EW", "duration_ms": 6000}
{"action": "set_priority",  "direction": "WE", "duration_ms": 15000}
{"action": "emergency_priority", "direction": "WE", "duration_ms": 20000}
{"action": "reset"}
```

---

## Team

| Name | Student ID |
|------|------------|
| Zakariya Ba Alawi | 220027 |
| Mohammed Bawazir | 230035 |
| Ahmed Bin Halabi | 220026 |
| Saad Alkeridis | 220621 |
| Mohammed Haythem | 220601 |

**Supervisor:** Prof. Nidal Nasser — Alfaisal University
