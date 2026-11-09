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
const unsigned long BATTERY_INTERVAL_MS = 5000;
const unsigned long COMBO_TOLERANCE_MS = 50;
const unsigned long BUTTON_COOLDOWN_MS = 250;
const unsigned long COMBO_COOLDOWN_MS = 400;
const unsigned long RELEASE_TIMEOUT_MS = 1500;
// Kalibrierung für Spannungsteiler (an Hardware anpassen, falls nötig).
const float BATTERY_ADC_MAX = 4095.0f;
const float BATTERY_REF_VOLTAGE = 3.3f;
const float BATTERY_DIVIDER_FACTOR = 2.43f;
const float BATTERY_MIN_VOLTAGE = 3.3f;
const float BATTERY_MAX_VOLTAGE = 4.2f;

NimBLEServer* pServer = nullptr;
NimBLECharacteristic* pCharButton = nullptr;
NimBLECharacteristic* pCharBattery = nullptr;
bool deviceConnected = false;

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

uint8_t getBatteryPercentage() {
  long sum = 0;
  for (int i = 0; i < 10; i++) {
    sum += analogRead(batteryPin);
    delay(5);
  }
  const int rawValue = sum / 10;
  const float voltage = (rawValue / BATTERY_ADC_MAX) * BATTERY_REF_VOLTAGE * BATTERY_DIVIDER_FACTOR;
  int percentage = static_cast<int>(((voltage - BATTERY_MIN_VOLTAGE) / (BATTERY_MAX_VOLTAGE - BATTERY_MIN_VOLTAGE)) * 100.0f);
  return static_cast<uint8_t>(constrain(percentage, 0, 100));
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

  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, LOW);  // Start AUS

  analogReadResolution(12);

  // NimBLE Init
  NimBLEDevice::init("Remote-Switch");

  // Kompatibilität mit einfachen Windows-Clients: kein Pairing/Bonding erforderlich.
  NimBLEDevice::setSecurityAuth(false, false, false);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  NimBLEService* pService = pServer->createService(SERVICE_UUID);

  pCharButton = pService->createCharacteristic(CHAR_BUTTON_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  pCharBattery = pService->createCharacteristic(CHAR_BATTERY_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);

  pService->start();

  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  NimBLEAdvertisementData advData;
  advData.setFlags(0x06);  // LE General Discoverable + BR/EDR not supported
  advData.addServiceUUID(SERVICE_UUID);
  pAdvertising->setAdvertisementData(advData);

  NimBLEAdvertisementData scanResponseData;
  scanResponseData.setName("Remote-Switch");
  pAdvertising->setScanResponseData(scanResponseData);

  pAdvertising->start();

  Serial.println("ESP32 Bereit. Warte auf Verbindung...");

  // Start-Signal
  blinkFeedback();
}

void loop() {
  // Robuster Check: nicht nur auf Callback verlassen.
  if (pServer->getConnectedCount() > 0) {
    if (!deviceConnected) {
      deviceConnected = true;  // Fallback, falls Callback verschluckt wurde
      Serial.println("LOOP-CHECK: Verbindung erkannt!");
    }

    bool nextPressed = isButtonPressed(buttonNextPin);
    bool prevPressed = isButtonPressed(buttonPrevPin);

    // Bei Tastendruck kurzes Toleranzfenster für gleichzeitige Betätigung.
    if (nextPressed || prevPressed) {
      delay(COMBO_TOLERANCE_MS);
      nextPressed = isButtonPressed(buttonNextPin);
      prevPressed = isButtonPressed(buttonPrevPin);

      if (nextPressed && prevPressed) {
        Serial.println("Beide Tasten gedrückt");
        sendButtonValue(3);
        delay(COMBO_COOLDOWN_MS);
        waitForButtonRelease();
      } else if (nextPressed) {
        Serial.println("Taste NEXT (25) gedrückt");
        sendButtonValue(1);
        delay(BUTTON_COOLDOWN_MS);
        waitForButtonRelease();
      } else if (prevPressed) {
        Serial.println("Taste PREV (32) gedrückt");
        sendButtonValue(2);
        delay(BUTTON_COOLDOWN_MS);
        waitForButtonRelease();
      }
    }

    // Akku senden (Timer)
    static unsigned long lastBat = 0;
    const unsigned long now = millis();
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
      Serial.println("LOOP-CHECK: Verbindung verloren.");
    }

    static unsigned long lastWait = 0;
    const unsigned long now = millis();
    if (now - lastWait > 3000) {
      lastWait = now;
      Serial.println("... warte auf App ...");
    }
  }

  delay(10);
}
