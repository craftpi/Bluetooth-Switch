#include <Arduino.h>
#include <BleKeyboard.h>
#include <NimBLEDevice.h> 
#include <WiFi.h> 
#include <Preferences.h> // Zum dauerhaften Speichern

// --- KONFIGURATION ---
BleKeyboard bleKeyboard("OneNote Switch", "DeinName", 100);
Preferences preferences;

// UUIDs für den Konfigurations-Kanal (Zufällig generiert)
#define CONFIG_SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CONFIG_CHAR_UUID    "beb5483e-36e1-4688-b7f5-ea07361b26a8"

// PINS
const int buttonNextPin = 25; 
const int buttonPrevPin = 32; 
const int batteryPin = 36; 

const unsigned long SLEEP_TIMEOUT = 60000 * 5; // 5 Minuten wach bleiben für Config
unsigned long lastActivityTime = 0;
int pendingAction = 0;

// Variablen für die Tasten (werden aus Speicher geladen)
uint8_t nextKeyMod = KEY_LEFT_CTRL;
uint8_t nextKeyCode = KEY_PAGE_DOWN;
uint8_t prevKeyMod = KEY_LEFT_CTRL;
uint8_t prevKeyCode = KEY_PAGE_UP;

// --- CALLBACK FÜR BLUETOOTH CONFIG ---
class ConfigCallbacks: public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic *pCharacteristic) {
        std::string value = pCharacteristic->getValue();
        if (value.length() > 0) {
            String command = String(value.c_str());
            Serial.println("Config Empfangen: " + command);
            
            // Format: "N:128:217" (Next:Mod:Code) oder "P:128:214" (Prev:Mod:Code)
            if (command.startsWith("N:")) {
                int firstColon = command.indexOf(':');
                int secondColon = command.lastIndexOf(':');
                if (firstColon > 0 && secondColon > firstColon) {
                    nextKeyMod = command.substring(firstColon + 1, secondColon).toInt();
                    nextKeyCode = command.substring(secondColon + 1).toInt();
                    preferences.putUChar("modNext", nextKeyMod);
                    preferences.putUChar("codeNext", nextKeyCode);
                    Serial.println("NEXT Taste gespeichert!");
                }
            }
            else if (command.startsWith("P:")) {
                int firstColon = command.indexOf(':');
                int secondColon = command.lastIndexOf(':');
                if (firstColon > 0 && secondColon > firstColon) {
                    prevKeyMod = command.substring(firstColon + 1, secondColon).toInt();
                    prevKeyCode = command.substring(secondColon + 1).toInt();
                    preferences.putUChar("modPrev", prevKeyMod);
                    preferences.putUChar("codePrev", prevKeyCode);
                    Serial.println("PREV Taste gespeichert!");
                }
            }
            // Wach bleiben, weil der User gerade konfiguriert
            lastActivityTime = millis();
        }
    }
};

// --- HILFSFUNKTIONEN ---

void debug(String msg) {
  Serial.println(msg);       
}

int getBatteryPercentage() {
  long sum = 0;
  for(int i = 0; i < 10; i++) {
    sum += analogRead(batteryPin);
    delay(5);
  }
  int rawValue = sum / 10; 
  float voltage = (rawValue / 4095.0) * 3.3 * 2.43; 

  int percentage = 0;
  if (voltage >= 4.2) percentage = 100;
  else if (voltage <= 3.3) percentage = 0;
  else percentage = (int)((voltage - 3.3) / (4.2 - 3.3) * 100);

  if (percentage > 100) percentage = 100;
  return percentage;
}

void goToDeepSleep() {
  debug("Gute Nacht! Gehe in Deep Sleep.");
  bleKeyboard.end(); 
  esp_sleep_enable_ext0_wakeup((gpio_num_t)buttonNextPin, 0); 
  esp_sleep_enable_ext1_wakeup((1ULL << buttonPrevPin), ESP_EXT1_WAKEUP_ALL_LOW);
  esp_deep_sleep_start();
}

void sendKey(uint8_t modifier, uint8_t key) {
  debug("Sende Taste: Mod=" + String(modifier) + " Key=" + String(key));
  if (modifier != 0) bleKeyboard.press(modifier);
  bleKeyboard.press(key);
  delay(50);
  bleKeyboard.releaseAll();
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_OFF);
  
  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  analogReadResolution(12); 

  // 1. Tasten laden
  preferences.begin("keyconf", false); 
  nextKeyMod = preferences.getUChar("modNext", KEY_LEFT_CTRL);   
  nextKeyCode = preferences.getUChar("codeNext", KEY_PAGE_DOWN); 
  prevKeyMod = preferences.getUChar("modPrev", KEY_LEFT_CTRL);   
  prevKeyCode = preferences.getUChar("codePrev", KEY_PAGE_UP);   

  // 2. Aufwach-Grund
  esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
  if (wakeup_reason == ESP_SLEEP_WAKEUP_EXT0) pendingAction = 1; 
  else if (wakeup_reason == ESP_SLEEP_WAKEUP_EXT1) pendingAction = 2; 
  else pendingAction = 0;

  // 3. Bluetooth starten
  int startBatteryLevel = getBatteryPercentage();
  bleKeyboard.setBatteryLevel(startBatteryLevel, true); 
  bleKeyboard.begin();

  // 4. Config Service erstellen
  NimBLEServer* pServer = NimBLEDevice::getServer();
  if (pServer) {
      NimBLEService* pService = pServer->createService(CONFIG_SERVICE_UUID);
      NimBLECharacteristic* pChar = pService->createCharacteristic(
                                       CONFIG_CHAR_UUID, 
                                       NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
                                    );
      pChar->setCallbacks(new ConfigCallbacks());
      pService->start();

      // --- HIER IST DER FIX FÜR DAS FINDEN ---
      
      NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
      
      // Erstmal stoppen, damit wir die Daten sauber neu setzen können
      pAdvertising->stop();
      
      // Paket 1 (Das "Hallo, hier bin ich"-Paket): Enthält NUR die UUID des Config-Service
      // Damit kann der Browser sofort filtern.
      NimBLEAdvertisementData mainAd;
      mainAd.setFlags(0x06); // General Discoverable + BLE Only
      mainAd.setCompleteServices(NimBLEUUID(CONFIG_SERVICE_UUID)); 
      pAdvertising->setAdvertisementData(mainAd);

      // Paket 2 (Die Antwort, wenn der PC fragt "Wer bist du?"): Enthält den Namen
      NimBLEAdvertisementData scanResponse;
      scanResponse.setName("OneNote Switch");
      pAdvertising->setScanResponseData(scanResponse);
      
      // Jetzt wieder starten
      pAdvertising->start();
  }
  
  lastActivityTime = millis();
  debug("Bluetooth gestartet. Advertising Split-Fix aktiv!");
}

void loop() {
  if (millis() - lastActivityTime >= SLEEP_TIMEOUT) {
    goToDeepSleep();
  }

  if (bleKeyboard.isConnected()) {
    // Aktion ausführen, falls wir gerade aufgewacht sind
    if (pendingAction != 0) {
       delay(500); // Warten bis Windows bereit
       if (pendingAction == 1) sendKey(nextKeyMod, nextKeyCode);
       if (pendingAction == 2) sendKey(prevKeyMod, prevKeyCode);
       bleKeyboard.setBatteryLevel(getBatteryPercentage(), true); // Notify True Fix
       pendingAction = 0;
       lastActivityTime = millis();
    }

    // Tasten manuell
    if(digitalRead(buttonNextPin) == LOW) {
      sendKey(nextKeyMod, nextKeyCode);
      bleKeyboard.setBatteryLevel(getBatteryPercentage(), true); 
      lastActivityTime = millis(); 
      delay(300); 
    }

    if(digitalRead(buttonPrevPin) == LOW) {
      sendKey(prevKeyMod, prevKeyCode);
      bleKeyboard.setBatteryLevel(getBatteryPercentage(), true);
      lastActivityTime = millis(); 
      delay(300); 
    }
  } 

  // Wenn Verbindung verloren, kurz wach bleiben für Reconnect, dann schlafen
  if (!bleKeyboard.isConnected()) {
      if (millis() - lastActivityTime > 120000) goToDeepSleep(); 
  }
}