#include <Arduino.h>
#include <NimBLEDevice.h>
#include <WiFi.h> 

// --- KONFIGURATION ---
#define SERVICE_UUID        "12345678-1234-1234-1234-1234567890ab"
#define CHAR_BUTTON_UUID    "12345678-1234-1234-1234-1234567890ac"
#define CHAR_BATTERY_UUID   "12345678-1234-1234-1234-1234567890ad"

// PINS
const int buttonNextPin = 25; 
const int buttonPrevPin = 32; 
const int batteryPin = 36; 
const int ledPin = 2; // Blaue LED

// EINSTELLUNGEN
const int BATTERY_INTERVAL = 5000; 

NimBLEServer* pServer = nullptr;
NimBLECharacteristic* pCharButton = nullptr;
NimBLECharacteristic* pCharBattery = nullptr;
bool deviceConnected = false;

// --- CALLBACKS ---
class MyServerCallbacks: public NimBLEServerCallbacks {
    void onConnect(NimBLEServer* pServer) {
        deviceConnected = true;
        Serial.println("CALLBACK: Gerät verbunden!");
    };
    void onDisconnect(NimBLEServer* pServer) {
        deviceConnected = false;
        Serial.println("CALLBACK: Gerät getrennt -> Starte Advertising...");
        NimBLEDevice::startAdvertising();
    }
};

int getBatteryPercentage() {
  long sum = 0;
  for(int i = 0; i < 10; i++) {
    sum += analogRead(batteryPin);
    delay(5);
  }
  int rawValue = sum / 10;
  float voltage = (rawValue / 4095.0) * 3.3 * 2.43; 
  int percentage = (int)((voltage - 3.3) / (4.2 - 3.3) * 100);
  return constrain(percentage, 0, 100);
}

// LED Feedback (Active HIGH: HIGH=AN, LOW=AUS)
void blinkFeedback() {
    digitalWrite(ledPin, HIGH);  
    delay(100);
    digitalWrite(ledPin, LOW);   
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_OFF); 
  
  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, LOW); // Start AUS

  analogReadResolution(12);

  // NimBLE Init
  NimBLEDevice::init("Remote-Switch");
  
  // WICHTIG: Security Settings für Windows Kompatibilität
  NimBLEDevice::setSecurityAuth(false, false, false);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9); 

  pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  NimBLEService* pService = pServer->createService(SERVICE_UUID);

  pCharButton = pService->createCharacteristic(
                      CHAR_BUTTON_UUID,
                      NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
                  );

  pCharBattery = pService->createCharacteristic(
                      CHAR_BATTERY_UUID,
                      NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
                  );

  pService->start();

  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  
  NimBLEAdvertisementData scanResponseData;
  scanResponseData.setName("Remote-Switch"); 
  pAdvertising->setScanResponseData(scanResponseData);
  
  pAdvertising->start();
  
  Serial.println("ESP32 Bereit. Warte auf Verbindung...");
  
  // Start-Signal
  blinkFeedback();
}

void loop() {
  // ROBUSTER CHECK: Verlassen wir uns nicht nur auf den Callback
  if (pServer->getConnectedCount() > 0) {
      if (!deviceConnected) {
          deviceConnected = true; // Fallback, falls Callback verschluckt wurde
          Serial.println("LOOP-CHECK: Verbindung erkannt!");
      }

      // 1. Tastenabfrage
      if (digitalRead(buttonNextPin) == LOW) {
          Serial.println("Taste NEXT (25) gedrückt");
          uint8_t val = 1; 
          pCharButton->setValue(&val, 1);
          pCharButton->notify();
          blinkFeedback(); 
          delay(250);      
      }
      
      if (digitalRead(buttonPrevPin) == LOW) {
          Serial.println("Taste PREV (32) gedrückt");
          uint8_t val = 2; 
          pCharButton->setValue(&val, 1);
          pCharButton->notify();
          blinkFeedback(); 
          delay(250);     
      }

      // 2. Akku Senden (Timer)
      static unsigned long lastBat = 0;
      if (millis() - lastBat > BATTERY_INTERVAL) {
          lastBat = millis();
          uint8_t level = getBatteryPercentage();
          // Serial.printf("Sende Akku: %d%%\n", level); // Optionales Debugging
          pCharBattery->setValue(&level, 1);
          pCharBattery->notify();
      }
      
  } else {
      // Wenn nicht verbunden, stellen wir sicher, dass der Status stimmt
      if (deviceConnected) {
          deviceConnected = false;
          Serial.println("LOOP-CHECK: Verbindung verloren.");
      }
      
      // Kleiner Hinweis im Monitor alle paar Sekunden
      static unsigned long lastWait = 0;
      if (millis() - lastWait > 3000) {
          lastWait = millis();
          Serial.println("... warte auf App ...");
      }
  }
  
  delay(10); 
}