#include <Arduino.h>
#include <NimBLEDevice.h>

// --- KONFIGURATION ---
#define SERVICE_UUID "12345678-1234-1234-1234-1234567890ab"
#define CHAR_BUTTON_UUID "12345678-1234-1234-1234-1234567890ac"
#define CHAR_BATTERY_UUID "12345678-1234-1234-1234-1234567890ad"

// PINS
const int buttonNextPin = 25;
const int buttonPrevPin = 32;
const int batteryPin = 36;
const int ledPin = 2;  // Blaue LED

// EINSTELLUNGEN
const unsigned long BATTERY_INTERVAL_MS = 30000; // Seltener messen (alle 30s)
const unsigned long COMBO_TOLERANCE_MS = 50;
const unsigned long BUTTON_COOLDOWN_MS = 250;
const unsigned long COMBO_COOLDOWN_MS = 400;
const unsigned long RELEASE_TIMEOUT_MS = 1500;
const unsigned long SLEEP_TIMEOUT_MS = 300000; // 5 Minuten Inaktivität bis Deep Sleep

// Kalibrierung für Spannungsteiler (an Hardware anpassen, falls nötig).
const float BATTERY_ADC_MAX = 4095.0f;
const float BATTERY_REF_VOLTAGE = 3.3f;
const float BATTERY_DIVIDER_FACTOR = 2.43f;
const float BATTERY_MIN_VOLTAGE = 3.3f;
const float BATTERY_MAX_VOLTAGE = 4.2f;

// Sonderwert für "Laden" (Heuristik oder fixer Wert)
const uint8_t BATTERY_CHARGING_SENTINEL = 255;

NimBLEServer* pServer = nullptr;
NimBLECharacteristic* pCharButton = nullptr;
NimBLECharacteristic* pCharBattery = nullptr;
bool deviceConnected = false;
unsigned long lastActivityTime = 0;
uint8_t pendingAction = 0; // 0=nichts, 1=next, 2=prev, 3=both

// --- CALLBACKS FÜR NIMBLE 2.x ---
class MyServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) override {
    (void)pServer;
    (void)connInfo;
    deviceConnected = true;
    Serial.println("CALLBACK: Gerät verbunden!");
  }

  void onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) override {
    (void)pServer;
    (void)connInfo;
    (void)reason;
    deviceConnected = false;
    Serial.println("CALLBACK: Gerät getrennt -> Starte Advertising...");
    NimBLEDevice::startAdvertising();
  }
};

// --- HILFSFUNKTIONEN ---

void updateActivity() {
  lastActivityTime = millis();
}

void goToDeepSleep() {
  Serial.println("Gehe in Deep Sleep...");
  delay(100);
  
  // Beide Tasten als Wakeup-Source (Low = Pressed)
  // ext1 erlaubt mehrere Pins. Bitmask für GPIO 25 und 32
  uint64_t mask = (1ULL << buttonNextPin) | (1ULL << buttonPrevPin);
  esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ALL_LOW);
  
  esp_deep_sleep_start();
}

uint8_t getBatteryPercentage() {
  long sum = 0;
  for (int i = 0; i < 5; i++) {
    sum += analogRead(batteryPin);
    delay(2);
  }
  const int rawValue = sum / 5;
  const float voltage = (rawValue / BATTERY_ADC_MAX) * BATTERY_REF_VOLTAGE * BATTERY_DIVIDER_FACTOR;
  
  // Einfache Heuristik für "Laden": Wenn Spannung > 4.3V (über LiPo Max), hängt es wahrscheinlich am USB
  if (voltage > 4.3f) return BATTERY_CHARGING_SENTINEL;

  // Realistischere LiPo-Entladekurve (grob stückweise linear)
  // 4.2V = 100%, 3.7V = 50%, 3.3V = 0%
  float percentage;
  if (voltage >= 4.2f) percentage = 100.0f;
  else if (voltage >= 3.7f) percentage = 50.0f + (voltage - 3.7f) * (50.0f / (4.2f - 3.7f));
  else if (voltage >= 3.3f) percentage = (voltage - 3.3f) * (50.0f / (3.7f - 3.3f));
  else percentage = 0.0f;

  return static_cast<uint8_t>(constrain(round(percentage), 0, 100));
}

// LED Feedback (Active HIGH: HIGH=AN, LOW=AUS)
void blinkFeedback() {
  digitalWrite(ledPin, HIGH);
  delay(100);
  digitalWrite(ledPin, LOW);
}

void sendButtonValue(uint8_t value) {
  if (pCharButton == nullptr) {
    return;
  }
  pCharButton->setValue(&value, 1);
  pCharButton->notify();
  blinkFeedback();
}

bool isButtonPressed(int pin) { return digitalRead(pin) == LOW; }

void waitForButtonRelease() {
  const unsigned long start = millis();
  while (millis() - start < RELEASE_TIMEOUT_MS) {
    if (!isButtonPressed(buttonNextPin) && !isButtonPressed(buttonPrevPin)) {
      delay(20);  // kurzes Nach-Entprellen
      return;
    }
    delay(10);
  }
}

void setup() {
  Serial.begin(115200);

  // CPU Frequenz senken (80MHz spart Strom gegenüber 240MHz)
  setCpuFrequencyMhz(80);

  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, LOW);

  analogReadResolution(12);

  // Wakeup-Check
  esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
  if (wakeup_reason == ESP_SLEEP_WAKEUP_EXT1) {
    uint64_t status = esp_sleep_get_ext1_wakeup_status();
    bool next = status & (1ULL << buttonNextPin);
    bool prev = status & (1ULL << buttonPrevPin);
    if (next && prev) pendingAction = 3;
    else if (next) pendingAction = 1;
    else if (prev) pendingAction = 2;
    Serial.printf("WAKEUP: Aktion gemerkt (%d)\n", pendingAction);
  }

  // NimBLE Init
  NimBLEDevice::init("Remote-Switch");
  NimBLEDevice::setSecurityAuth(false, false, false);
  NimBLEDevice::setPower(ESP_PWR_LVL_P3); // P9 auf P3 senken für Stromersparnis

  pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  NimBLEService* pService = pServer->createService(SERVICE_UUID);
  pCharButton = pService->createCharacteristic(CHAR_BUTTON_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  pCharBattery = pService->createCharacteristic(CHAR_BATTERY_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  pService->start();

  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  NimBLEAdvertisementData advData;
  advData.setFlags(0x06);
  advData.addServiceUUID(SERVICE_UUID);
  pAdvertising->setAdvertisementData(advData);
  pAdvertising->setScanResponseData(advData);
  pAdvertising->start();

  updateActivity();
  Serial.println("ESP32 Bereit.");
}

void loop() {
  const unsigned long now = millis();

  // Inaktivitäts-Check
  if (now - lastActivityTime > SLEEP_TIMEOUT_MS) {
    goToDeepSleep();
  }

  if (pServer->getConnectedCount() > 0) {
    if (!deviceConnected) {
      deviceConnected = true;
      Serial.println("Verbunden!");
    }

    // Wenn wir nach einem Wakeup verbunden sind: Gemerkte Aktion sofort raus.
    if (pendingAction != 0) {
      Serial.println("Sende Wakeup-Aktion...");
      sendButtonValue(pendingAction);
      pendingAction = 0;
      updateActivity();
    }

    bool nextPressed = isButtonPressed(buttonNextPin);
    bool prevPressed = isButtonPressed(buttonPrevPin);

    if (nextPressed || prevPressed) {
      updateActivity();
      delay(COMBO_TOLERANCE_MS);
      nextPressed = isButtonPressed(buttonNextPin);
      prevPressed = isButtonPressed(buttonPrevPin);

      if (nextPressed && prevPressed) {
        sendButtonValue(3);
        delay(COMBO_COOLDOWN_MS);
        waitForButtonRelease();
      } else if (nextPressed) {
        sendButtonValue(1);
        delay(BUTTON_COOLDOWN_MS);
        waitForButtonRelease();
      } else if (prevPressed) {
        sendButtonValue(2);
        delay(BUTTON_COOLDOWN_MS);
        waitForButtonRelease();
      }
      updateActivity();
    }

    // Akku senden (Timer)
    static unsigned long lastBat = 0;
    if (now - lastBat > BATTERY_INTERVAL_MS) {
      lastBat = now;
      const uint8_t level = getBatteryPercentage();
      if (pCharBattery != nullptr) {
        pCharBattery->setValue(&level, 1);
        pCharBattery->notify();
      }
    }
  } else {
    if (deviceConnected) {
      deviceConnected = false;
      Serial.println("Verbindung verloren.");
    }
    // Länger warten wenn nicht verbunden
    delay(100);
  }

  delay(20);
}
