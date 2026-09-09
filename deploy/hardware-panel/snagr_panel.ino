/*
  SNAGR Hardware Panel — ESP32 firmware

  A physical kill switch + status LED for the SNAGR dashboard. Real
  integration, not a prop: the button calls the same cancellation path the
  dashboard's own Cancel button uses (triage/server.py's CancellationToken),
  and the LED reflects the engine's actual acquisition state polled live.

  Network model: the ESP32 runs its OWN WiFi access point — nothing depends
  on venue WiFi, a router, or the internet. The laptop running the engine
  joins this AP directly. Two devices, one private link, matching the
  project's "no cloud, no relay" posture end to end.

  Wiring (ESP32 DevKit, adjust pin numbers to taste — avoid strapping pins
  0/2/12/15 if you pick different ones):
    - Push button: one leg -> GPIO 4, other leg -> GND. No external resistor
      needed (internal pull-up used).
    - WS2812 (NeoPixel) data in -> GPIO 5. Power from 5V/GND (or 3V3 for a
      single low-current pixel — check your module's rating).
    - Optional piezo buzzer: + -> GPIO 15, - -> GND.

  Libraries needed (Arduino Library Manager): "Adafruit NeoPixel".
  WiFi.h / HTTPClient.h ship with the ESP32 board core.

  Before flashing:
    1. Set AP_PASSWORD to something real (8+ chars, WPA2 minimum).
    2. Flash, power the board, then on the laptop join WiFi network
       AP_SSID / AP_PASSWORD like any other network.
    3. Check what IP the laptop got (ifconfig / ipconfig) — the ESP32's
       DHCP server usually hands out 192.168.4.2 to the first client, but
       verify rather than assume. Update ENGINE_HOST below to match, or
       just start the engine with that IP as --host (see deploy/pi5-setup.md
       for the general --network-mode lan flow; on a plain laptop demo it's
       simply: `python -m triage.server --network-mode lan --host <that IP>`).
    4. Re-flash if ENGINE_HOST needed to change.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <Adafruit_NeoPixel.h>

// ---------------- Config — edit before flashing ----------------
const char *AP_SSID = "SNAGR-Panel";
const char *AP_PASSWORD = "snagrdemo1";     // WPA2, 8+ chars — change this
const char *ENGINE_HOST = "192.168.4.2";    // laptop's IP once it joins this AP
const uint16_t ENGINE_PORT = 5057;

// ---------------- Pins ----------------
const int PIN_BUTTON = 4;    // kill switch -> GND, internal pull-up
const int PIN_LED = 5;       // WS2812 data
const int PIN_BUZZER = 15;   // optional
#define USE_BUZZER 1

const int NUM_LEDS = 1;      // 8 if you're using a NeoPixel ring instead
Adafruit_NeoPixel strip(NUM_LEDS, PIN_LED, NEO_GRB + NEO_KHZ800);

// ---------------- State ----------------
unsigned long lastPoll = 0;
const unsigned long POLL_MS = 700;
bool buttonWasDown = false;
unsigned long lastButtonEdge = 0;
const unsigned long DEBOUNCE_MS = 250;   // one press should mean one cancel

String lastEvent = "idle";

void setColor(uint8_t r, uint8_t g, uint8_t b) {
  for (int i = 0; i < NUM_LEDS; i++) strip.setPixelColor(i, strip.Color(r, g, b));
  strip.show();
}

// idle=blue, running=amber, done=green, cancelled/error=red — matches the
// same states triage/server.py's /api/hardware/status reports.
void applyEventColor(const String &event) {
  if (event == "running")        setColor(255, 140, 0);
  else if (event == "done")      setColor(0, 200, 0);
  else if (event == "cancelled") setColor(200, 0, 0);
  else if (event == "error")     setColor(200, 0, 0);
  else                            setColor(0, 60, 200);   // idle
}

void beep(int ms) {
#if USE_BUZZER
  digitalWrite(PIN_BUZZER, HIGH);
  delay(ms);
  digitalWrite(PIN_BUZZER, LOW);
#endif
}

String engineUrl(const char *path) {
  return String("http://") + ENGINE_HOST + ":" + ENGINE_PORT + path;
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_BUTTON, INPUT_PULLUP);
#if USE_BUZZER
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
#endif

  strip.begin();
  setColor(0, 0, 0);

  WiFi.softAP(AP_SSID, AP_PASSWORD);
  Serial.print("SoftAP up, SSID=");
  Serial.print(AP_SSID);
  Serial.print(", IP=");
  Serial.println(WiFi.softAPIP());   // normally 192.168.4.1
  Serial.println("Join this network from the laptop, then start the engine");
  Serial.println("with --network-mode lan --host <laptop's IP on this AP>.");

  setColor(0, 60, 200);   // idle, waiting for the laptop
}

void pollStatus() {
  HTTPClient http;
  http.begin(engineUrl("/api/hardware/status"));
  http.setTimeout(400);   // never let a dropped link stall the loop

  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    // One-field hand-rolled extraction rather than pulling in ArduinoJson
    // for a single string value.
    int i = body.indexOf("\"event\":\"");
    if (i >= 0) {
      int start = i + 9;
      int end = body.indexOf('"', start);
      if (end > start) {
        lastEvent = body.substring(start, end);
        applyEventColor(lastEvent);
      }
    }
  }
  // A non-200/timeout just leaves the last known color showing — no flicker
  // to red on a single missed poll during a WiFi hiccup.
  http.end();
}

void handleButton() {
  bool down = (digitalRead(PIN_BUTTON) == LOW);

  if (down && !buttonWasDown && millis() - lastButtonEdge > DEBOUNCE_MS) {
    lastButtonEdge = millis();
    buttonWasDown = true;

    Serial.println("KILL SWITCH pressed -> POST /api/hardware/killswitch");
    HTTPClient http;
    http.begin(engineUrl("/api/hardware/killswitch"));
    http.setTimeout(400);
    http.POST("");
    http.end();

    setColor(255, 0, 0);   // immediate visual ack, independent of next poll
    beep(150);
  } else if (!down) {
    buttonWasDown = false;
  }
}

void loop() {
  handleButton();

  if (millis() - lastPoll > POLL_MS) {
    lastPoll = millis();
    pollStatus();
  }
}
