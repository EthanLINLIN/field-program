#include <Arduino.h>

constexpr bool OUTPUT_ACTIVE_HIGH = true;
constexpr uint32_t SERIAL_BAUD_RATE = 115200;
constexpr uint32_t HEARTBEAT_TIMEOUT_MS = 1500;

struct PinMapping {
  const char* label;
  uint8_t pin;
};

const PinMapping PIN_MAPPINGS[] = {
    {"L2A", 12},
    {"L2B", 14},
    {"L2C", 27},
    {"L2E", 25},
    {"L2F", 33},
    {"L2G", 32},
    {"L1B", 13},
    {"L2D", 26},
    {"L3A", 2},
    {"L3B", 0},
    {"L4A", 17},
    {"L4B", 5},
    {"L4C", 18},
    {"L4D", 19},
    {"PWM", 21},
};

constexpr size_t PIN_COUNT = sizeof(PIN_MAPPINGS) / sizeof(PIN_MAPPINGS[0]);
constexpr uint8_t OUTPUT_ON_LEVEL = OUTPUT_ACTIVE_HIGH ? HIGH : LOW;
constexpr uint8_t OUTPUT_OFF_LEVEL = OUTPUT_ACTIVE_HIGH ? LOW : HIGH;

unsigned long g_lastHeartbeatMs = 0;
bool g_watchdogTripped = false;

bool isKnownPin(int pin);
void writePinState(int pin, bool enabled);
void allOff();
void reportStatus();
void handleCommand(const String& rawCommand);
void enforceHeartbeatWatchdog();

bool isKnownPin(int pin) {
  for (size_t i = 0; i < PIN_COUNT; ++i) {
    if (PIN_MAPPINGS[i].pin == pin) {
      return true;
    }
  }
  return false;
}

void writePinState(int pin, bool enabled) {
  if (!isKnownPin(pin)) {
    return;
  }

  digitalWrite(pin, enabled ? OUTPUT_ON_LEVEL : OUTPUT_OFF_LEVEL);
}

void allOff() {
  for (size_t i = 0; i < PIN_COUNT; ++i) {
    digitalWrite(PIN_MAPPINGS[i].pin, OUTPUT_OFF_LEVEL);
  }
}

void reportStatus() {
  Serial.print("{\"firmware\":\"preset-controller-20261011\",\"activeHigh\":");
  Serial.print(OUTPUT_ACTIVE_HIGH ? "true" : "false");
  Serial.print(",\"watchdogTripped\":");
  Serial.print(g_watchdogTripped ? "true" : "false");
  Serial.print(",\"activePins\":[");
  bool first = true;
  for (size_t i = 0; i < PIN_COUNT; ++i) {
    if (digitalRead(PIN_MAPPINGS[i].pin) == OUTPUT_ON_LEVEL) {
      if (!first) Serial.print(',');
      Serial.print(PIN_MAPPINGS[i].pin);
      first = false;
    }
  }
  Serial.print("],\"levels\":{");
  for (size_t i = 0; i < PIN_COUNT; ++i) {
    if (i > 0) Serial.print(',');
    Serial.print('"');
    Serial.print(PIN_MAPPINGS[i].pin);
    Serial.print("\":");
    Serial.print(digitalRead(PIN_MAPPINGS[i].pin));
  }
  Serial.println("}}");
}

void handleCommand(const String& rawCommand) {
  String command = rawCommand;
  command.trim();

  if (command.length() == 0) {
    return;
  }

  if (command.equals("PING")) {
    Serial.println("PONG");
    return;
  }

  if (command.equals("STATUS")) {
    reportStatus();
    return;
  }

  if (command.equals("ALL_OFF")) {
    allOff();
    return;
  }

  if (command.equals("HEARTBEAT")) {
    g_lastHeartbeatMs = millis();
    g_watchdogTripped = false;
    return;
  }

  if (command.startsWith("ON ")) {
    const int pin = command.substring(3).toInt();
    writePinState(pin, true);
    return;
  }

  if (command.startsWith("OFF ")) {
    const int pin = command.substring(4).toInt();
    writePinState(pin, false);
    return;
  }
}

void enforceHeartbeatWatchdog() {
  const unsigned long now = millis();
  if (!g_watchdogTripped && (now - g_lastHeartbeatMs > HEARTBEAT_TIMEOUT_MS)) {
    allOff();
    g_watchdogTripped = true;
  }
}

void setup() {
  for (size_t i = 0; i < PIN_COUNT; ++i) {
    pinMode(PIN_MAPPINGS[i].pin, OUTPUT);
  }

  allOff();

  Serial.begin(SERIAL_BAUD_RATE);
  g_lastHeartbeatMs = millis();
}

void loop() {
  while (Serial.available() > 0) {
    const String command = Serial.readStringUntil('\n');
    handleCommand(command);
  }

  enforceHeartbeatWatchdog();
  delay(5);
}

