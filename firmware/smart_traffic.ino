// =============================================================
// Smart Traffic & Congestion Control - LLM-Augmented Version
// SE322 IoT + LLM Augmentation Project
// Alfaisal University
//
// Adds override topic (iot/traffic/override) to existing
// adaptive traffic control firmware. Override commands accept
// JSON of the form:
//   {"action":"extend_green","direction":"WE","duration_ms":8000}
//   {"action":"force_phase","direction":"EW","duration_ms":6000}
//   {"action":"set_priority","direction":"WE","duration_ms":15000}
//   {"action":"reset"}
//
// Override is bounded in duration. When it expires the system
// returns to the existing adaptive timing logic untouched.
// =============================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// -------------------- WIFI / MQTT --------------------
const char* ssid       = "Zakariya's Galaxy S22 Ultra";
const char* password   = "chgr3080";
const char* mqtt_server = "broker.hivemq.com";

WiFiClient espClient;
PubSubClient client(espClient);

// -------------------- PINS --------------------
#define TRIG_ENTRY_WE 19
#define ECHO_ENTRY_WE 18
#define TRIG_EXIT_WE  5
#define ECHO_EXIT_WE  21

#define TRIG_ENTRY_EW 2
#define ECHO_ENTRY_EW 4
#define TRIG_EXIT_EW  22
#define ECHO_EXIT_EW  23

#define GREEN_WE  14
#define YELLOW_WE 27
#define RED_WE    26

#define GREEN_EW  25
#define YELLOW_EW 33
#define RED_EW    32

// -------------------- STATE --------------------
int queueWE = 0;
int queueEW = 0;
bool entryWE_Prev = false, exitWE_Prev = false;
bool entryEW_Prev = false, exitEW_Prev = false;

enum Phase { PHASE_GREEN_WE, PHASE_YELLOW_WE, PHASE_GREEN_EW, PHASE_YELLOW_EW };
Phase currentPhase = PHASE_GREEN_WE;
unsigned long phaseStartTime = 0;

const unsigned long BASE_GREEN_DURATION = 5000;
const unsigned long MAX_GREEN_DURATION  = 15000;
const unsigned long TIME_PER_CAR        = 1000;
const unsigned long YELLOW_DURATION     = 2000;

unsigned long greenDurationWE = BASE_GREEN_DURATION;
unsigned long greenDurationEW = BASE_GREEN_DURATION;

// -------------------- OVERRIDE STATE --------------------
// When overrideActive is true, the loop bypasses adaptive timing
// and holds overridePhase until overrideEndTime is reached.
bool overrideActive = false;
Phase overridePhase = PHASE_GREEN_WE;
unsigned long overrideEndTime = 0;
String overrideReason = "";

// Emergency pre-emption: higher priority than normal override.
// Locks out adaptive logic for the full duration regardless of queues.
bool emergencyActive = false;
unsigned long emergencyEndTime = 0;

// -------------------- WIFI / MQTT SETUP --------------------
void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("WiFi connected");
}

// Handles MQTT messages on subscribed topics. Currently only
// iot/traffic/override is subscribed.
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String topicStr = String(topic);
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];

  Serial.print("[MQTT] "); Serial.print(topicStr); Serial.print(" -> "); Serial.println(msg);

  if (topicStr != "iot/traffic/override") return;

  // Parse JSON command. ArduinoJson v6 syntax.
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, msg);
  if (err) {
    Serial.print("[OVERRIDE] JSON parse error: ");
    Serial.println(err.c_str());
    return;
  }

  const char* action = doc["action"] | "";
  const char* dir    = doc["direction"] | "";
  unsigned long dur  = doc["duration_ms"] | 5000UL;

  // Clamp duration so a malformed or hostile command can't lock the lights.
  if (dur < 1000)  dur = 1000;
  if (dur > 20000) dur = 20000;

  if (strcmp(action, "reset") == 0) {
    overrideActive = false;
    emergencyActive = false;
    overrideReason = "reset";
    Serial.println("[OVERRIDE] cleared");
    return;
  }

  bool isWE = (strcmp(dir, "WE") == 0);
  bool isEW = (strcmp(dir, "EW") == 0);
  if (!isWE && !isEW) {
    Serial.println("[OVERRIDE] missing or invalid direction");
    return;
  }

  if (strcmp(action, "emergency_priority") == 0) {
    // Emergency pre-emption: clamp 5-30s, cancel any normal override.
    if (dur < 5000)  dur = 5000;
    if (dur > 30000) dur = 30000;
    emergencyActive = true;
    emergencyEndTime = millis() + dur;
    overrideActive = false;
    overridePhase = isWE ? PHASE_GREEN_WE : PHASE_GREEN_EW;
    Serial.print("[EMERGENCY] pre-emption activated dir=");
    Serial.print(isWE ? "WE" : "EW");
    Serial.print(" dur="); Serial.print(dur); Serial.println("ms");
    return;
  }

  // For extend_green / force_phase / set_priority we drive the
  // requested direction's green phase for the requested duration.
  overrideActive  = true;
  overridePhase   = isWE ? PHASE_GREEN_WE : PHASE_GREEN_EW;
  overrideEndTime = millis() + dur;
  overrideReason  = String(action) + ":" + String(dir);
  Serial.print("[OVERRIDE] active phase=");
  Serial.print(isWE ? "WE" : "EW");
  Serial.print(" dur=");
  Serial.print(dur);
  Serial.println("ms");
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    if (client.connect("ESP32Client_Traffic")) {
      Serial.println("connected");
      client.subscribe("iot/traffic/override");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      delay(5000);
    }
  }
}

void publishState(const char* dir, const char* state, int queue) {
  char topicState[50], topicQueue[50];
  sprintf(topicState, "iot/traffic/%s/state", dir);
  sprintf(topicQueue, "iot/traffic/%s/queue", dir);
  client.publish(topicState, state);
  client.publish(topicQueue, String(queue).c_str());
}

// Publishes the predicted end time of the current phase.
// remaining_ms = how long the current phase will last from now.
// active_dir = which direction's countdown the dashboard should display
// ("WE", "EW", or "" for none, e.g. during yellow).
void publishPhaseEnd(unsigned long remaining_ms, const char* active_dir) {
  char payload[80];
  sprintf(payload, "{\"remaining_ms\":%lu,\"direction\":\"%s\"}", remaining_ms, active_dir);
  client.publish("iot/traffic/phase", payload);
}

// -------------------- SETUP --------------------
void setup() {
  Serial.begin(9600);
  setup_wifi();
  client.setServer(mqtt_server, 1883);
  client.setCallback(mqttCallback);

  pinMode(TRIG_ENTRY_WE, OUTPUT); pinMode(ECHO_ENTRY_WE, INPUT);
  pinMode(TRIG_EXIT_WE,  OUTPUT); pinMode(ECHO_EXIT_WE,  INPUT);
  pinMode(TRIG_ENTRY_EW, OUTPUT); pinMode(ECHO_ENTRY_EW, INPUT);
  pinMode(TRIG_EXIT_EW,  OUTPUT); pinMode(ECHO_EXIT_EW,  INPUT);

  pinMode(GREEN_WE, OUTPUT); pinMode(YELLOW_WE, OUTPUT); pinMode(RED_WE, OUTPUT);
  pinMode(GREEN_EW, OUTPUT); pinMode(YELLOW_EW, OUTPUT); pinMode(RED_EW, OUTPUT);
}

// -------------------- SENSOR HELPERS --------------------
long readDistanceCM(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH); delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  long duration = pulseIn(echoPin, HIGH, 50000);
  if (duration == 0) return -1;
  return duration * 0.034 / 2;
}

void handleQueue(bool detectedNow, bool &prevDetected, int &queue, const char* label) {
  if (detectedNow && !prevDetected) {
    queue++;
    Serial.print("ENTER "); Serial.print(label); Serial.print(" -> queue++ -> "); Serial.println(queue);
    prevDetected = true;
  }
  if (!detectedNow && prevDetected) prevDetected = false;
}

void handleExit(bool detectedNow, bool &prevDetected, int &queue, const char* label) {
  if (detectedNow && !prevDetected) {
    if (queue > 0) queue--;
    Serial.print("EXIT "); Serial.print(label); Serial.print(" -> queue-- -> "); Serial.println(queue);
    prevDetected = true;
  }
  if (!detectedNow && prevDetected) prevDetected = false;
}

void updateQueues() {
  long entryWE = readDistanceCM(TRIG_ENTRY_WE, ECHO_ENTRY_WE);
  long exitWE  = readDistanceCM(TRIG_EXIT_WE,  ECHO_EXIT_WE);
  handleQueue((entryWE > 0 && entryWE < 8), entryWE_Prev, queueWE, "WE");
  handleExit ((exitWE  > 0 && exitWE  < 8), exitWE_Prev,  queueWE, "WE");

  long entryEW = readDistanceCM(TRIG_ENTRY_EW, ECHO_ENTRY_EW);
  long exitEW  = readDistanceCM(TRIG_EXIT_EW,  ECHO_EXIT_EW);
  handleQueue((entryEW > 0 && entryEW < 8), entryEW_Prev, queueEW, "EW");
  handleExit ((exitEW  > 0 && exitEW  < 8), exitEW_Prev,  queueEW, "EW");
}

// -------------------- LIGHT DRIVERS --------------------
void switchToWE() {
  digitalWrite(GREEN_WE, HIGH); digitalWrite(YELLOW_WE, LOW);  digitalWrite(RED_WE, LOW);
  digitalWrite(GREEN_EW, LOW);  digitalWrite(YELLOW_EW, LOW);  digitalWrite(RED_EW, HIGH);
}
void switchToEW() {
  digitalWrite(GREEN_WE, LOW);  digitalWrite(YELLOW_WE, LOW);  digitalWrite(RED_WE, HIGH);
  digitalWrite(GREEN_EW, HIGH); digitalWrite(YELLOW_EW, LOW);  digitalWrite(RED_EW, LOW);
}
void switchToYellowWE() {
  digitalWrite(GREEN_WE, LOW);  digitalWrite(YELLOW_WE, HIGH); digitalWrite(RED_WE, LOW);
  digitalWrite(GREEN_EW, LOW);  digitalWrite(YELLOW_EW, LOW);  digitalWrite(RED_EW, HIGH);
}
void switchToYellowEW() {
  digitalWrite(GREEN_WE, LOW);  digitalWrite(YELLOW_WE, LOW);  digitalWrite(RED_WE, HIGH);
  digitalWrite(GREEN_EW, LOW);  digitalWrite(YELLOW_EW, HIGH); digitalWrite(RED_EW, LOW);
}

// -------------------- MAIN LOOP --------------------
void loop() {
  updateQueues();

  if (!client.connected()) reconnect();
  client.loop();

  // -------- Emergency pre-emption path (highest priority) --------
  if (emergencyActive) {
    if (millis() >= emergencyEndTime) {
      emergencyActive = false;
      currentPhase = (overridePhase == PHASE_GREEN_WE) ? PHASE_YELLOW_WE : PHASE_YELLOW_EW;
      phaseStartTime = millis();
      Serial.println("[EMERGENCY] expired -> transitioning to yellow");
    } else {
      static unsigned long lastEmergencyPublish = 0;
      if (millis() - lastEmergencyPublish > 1000) {
        unsigned long remaining = emergencyEndTime - millis();
        const char* dir = (overridePhase == PHASE_GREEN_WE) ? "WE" : "EW";
        publishPhaseEnd(remaining, dir);
        lastEmergencyPublish = millis();
      }
      if (overridePhase == PHASE_GREEN_WE) {
        switchToWE();
        publishState("WE", "GREEN", queueWE);
        publishState("EW", "RED",   queueEW);
      } else {
        switchToEW();
        publishState("EW", "GREEN", queueEW);
        publishState("WE", "RED",   queueWE);
      }
      currentPhase = overridePhase;
      delay(200);
      return;  // skip all adaptive logic
    }
  }

  // -------- Override path --------
  // While override is active we drive the lights and publish state
  // matching the overridden phase. Adaptive timing is bypassed.
  if (overrideActive) {
    if (millis() >= overrideEndTime) {
      overrideActive = false;
      // Force a yellow transition so the opposite direction gets its turn immediately.
      // Without this the adaptive loop restarts elapsed=0 and keeps the same phase green.
      currentPhase = (overridePhase == PHASE_GREEN_WE) ? PHASE_YELLOW_WE : PHASE_YELLOW_EW;
      phaseStartTime = millis();
      Serial.println("[OVERRIDE] expired -> transitioning to yellow");
    } else {
      // Publish remaining override time once per second so the dashboard stays in sync.
      static unsigned long lastOverridePublish = 0;
      if (millis() - lastOverridePublish > 1000) {
        unsigned long remaining = overrideEndTime - millis();
        const char* dir = (overridePhase == PHASE_GREEN_WE) ? "WE" : "EW";
        publishPhaseEnd(remaining, dir);
        lastOverridePublish = millis();
      }
      if (overridePhase == PHASE_GREEN_WE) {
        switchToWE();
        publishState("WE", "GREEN", queueWE);
        publishState("EW", "RED",   queueEW);
      } else {
        switchToEW();
        publishState("EW", "GREEN", queueEW);
        publishState("WE", "RED",   queueWE);
      }
      currentPhase = overridePhase;
      delay(200);
      return;
    }
  }

  // -------- Normal adaptive path (unchanged) --------
  if (currentPhase == PHASE_GREEN_WE)  { publishState("WE", "GREEN",  queueWE); publishState("EW", "RED",    queueEW); }
  if (currentPhase == PHASE_YELLOW_WE) { publishState("WE", "YELLOW", queueWE); publishState("EW", "RED",    queueEW); }
  if (currentPhase == PHASE_GREEN_EW)  { publishState("EW", "GREEN",  queueEW); publishState("WE", "RED",    queueWE); }
  if (currentPhase == PHASE_YELLOW_EW) { publishState("EW", "YELLOW", queueEW); publishState("WE", "RED",    queueWE); }

  Serial.print("queueWE: "); Serial.print(queueWE);
  Serial.print(" | queueEW: "); Serial.println(queueEW);

  greenDurationWE = BASE_GREEN_DURATION + queueWE * TIME_PER_CAR;
  if (greenDurationWE > MAX_GREEN_DURATION) greenDurationWE = MAX_GREEN_DURATION;
  greenDurationEW = BASE_GREEN_DURATION + queueEW * TIME_PER_CAR;
  if (greenDurationEW > MAX_GREEN_DURATION) greenDurationEW = MAX_GREEN_DURATION;

  unsigned long now = millis();
  unsigned long elapsed = now - phaseStartTime;

  switch (currentPhase) {
    case PHASE_GREEN_WE:
      switchToWE();
      if (elapsed == 0) publishPhaseEnd(greenDurationWE, "WE");
      if (elapsed >= greenDurationWE && queueEW > 0) {
        currentPhase = PHASE_YELLOW_WE; phaseStartTime = now;
        publishPhaseEnd(YELLOW_DURATION, "");
      }
      break;
    case PHASE_YELLOW_WE:
      switchToYellowWE();
      if (elapsed >= YELLOW_DURATION) {
        currentPhase = PHASE_GREEN_EW; phaseStartTime = now;
        publishPhaseEnd(greenDurationEW, "EW");
      }
      break;
    case PHASE_GREEN_EW:
      switchToEW();
      if (elapsed == 0) publishPhaseEnd(greenDurationEW, "EW");
      if (elapsed >= greenDurationEW && queueWE > 0) {
        currentPhase = PHASE_YELLOW_EW; phaseStartTime = now;
        publishPhaseEnd(YELLOW_DURATION, "");
      }
      break;
    case PHASE_YELLOW_EW:
      switchToYellowEW();
      if (elapsed >= YELLOW_DURATION) {
        currentPhase = PHASE_GREEN_WE; phaseStartTime = now;
        publishPhaseEnd(greenDurationWE, "WE");
      }
      break;
  }

  delay(200);
}