#include <Arduino.h>
#include <BleKeyboard.h>
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <WebSerial.h>
#include <NimBLEDevice.h> 

// --- KONFIGURATION ---
const char* ssid = "GF1KB";     
const char* password = "G0#_hsPoQ$"; 

// Initialisierung
BleKeyboard bleKeyboard("OneNote Remote", "DeinName", 100);
AsyncWebServer server(80);

// PINS
const int buttonNextPin = 18; 
const int buttonPrevPin = 19;
const int batteryPin = 36; 

unsigned long previousMillis = 0;
const long interval = 60000;  // 1 Minute Intervall

// Status-Variable für den Auto-Reconnect
bool wasConnected = false; 
int oldLevel = -1; // Zum Speichern des alten Wertes

// --- HILFSFUNKTIONEN ---

void debug(String msg) {
  Serial.println(msg);       
  WebSerial.println(msg);    
}

int getBatteryPercentage() {
  int rawValue = analogRead(batteryPin);
  
  // Dein Faktor mit 100k Widerstand (2.3)
  float voltage = (rawValue / 4095.0) * 3.3 * 2.3; 
  
  int percentage = 0;
  if (voltage >= 4.2) percentage = 100;
  else if (voltage <= 3.3) percentage = 0;
  else percentage = (int)((voltage - 3.3) / (4.2 - 3.3) * 100);

  return percentage;
}

void setup() {
  Serial.begin(115200);
  
  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  analogReadResolution(12); 

  // WLAN Verbindung
  Serial.print("Verbinde mit WLAN");
  WiFi.begin(ssid, password);
  int tryCount = 0;
  while (WiFi.status() != WL_CONNECTED && tryCount < 20) {
    delay(500);
    Serial.print(".");
    tryCount++;
  }
  
  if(WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWLAN Verbunden!");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
    WebSerial.begin(&server);
    server.begin();
  } else {
    Serial.println("\nKein WLAN - mache ohne WebSerial weiter.");
  }

  // Startwerte setzen
  int startBatteryLevel = getBatteryPercentage();
  oldLevel = startBatteryLevel;
  bleKeyboard.setBatteryLevel(startBatteryLevel);

  Serial.println("Starte Bluetooth...");
  bleKeyboard.begin();
}

void checkSubscriptionStatus() {
  NimBLEServer* pServer = NimBLEDevice::getServer();
  
  if (pServer != nullptr) {
    // 1. Suche Batterie-Service (180F)
    NimBLEService* pBatService = pServer->getServiceByUUID("180F");
    
    if (pBatService != nullptr) {
      // 2. Suche Batterie-Level (2A19)
      NimBLECharacteristic* pBatLevelChar = pBatService->getCharacteristic("2A19");
      
      if (pBatLevelChar != nullptr) {
        // 3. Suche den "Abo-Schalter" (Descriptor 2902)
        // Das ist der Standard-Descriptor für "Notify"
        NimBLEDescriptor* pDesc = pBatLevelChar->getDescriptorByUUID("2902");
        
        if (pDesc != nullptr) {
          // Wir lesen den Wert aus dem Speicher
          // (Rückgabetyp kann je nach Version std::string oder NimBLEAttValue sein, 
          // aber beides verhält sich wie ein Array)
          auto value = pDesc->getValue();
          
          bool isSubscribed = false;
          // Prüfen ob Daten da sind und ob das erste Byte '1' ist
          if (value.length() > 0 && value[0] == 1) {
            isSubscribed = true;
          }

          if (isSubscribed) {
             debug("Windows Abo-Status: AKTIV (Windows hört zu!)");
          } else {
             debug("Windows Abo-Status: INAKTIV (Windows ignoriert uns!)");
          }
        } else {
            debug("Fehler: Descriptor 2902 nicht gefunden!");
        }
      }
    }
  }
}

void loop() {
  // --- AUTO-RECONNECT LOGIK ---
  if (bleKeyboard.isConnected()) {
    if (!wasConnected) {
        wasConnected = true;
        debug("Bluetooth: Verbunden!");
        // Windows beim Verbinden den aktuellen Wert servieren
        delay(500); 
        bleKeyboard.setBatteryLevel(oldLevel);
    }
  } else {
    // Wenn Verbindung weg ist -> Sofort wieder rufen!
    if (wasConnected) {
        wasConnected = false;
        debug("Verbindung weg -> Starte Advertising neu (Auto-Reconnect)");
        NimBLEDevice::getAdvertising()->start();
    }
  }

  // --- AKKU MESSUNG & WINDOWS REFRESH TRICK ---
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    
    // Wir messen immer, auch wenn nicht verbunden (damit der Wert bereit ist)
    int newLevel = getBatteryPercentage();
    
    // Nur wenn sich der Akku wirklich verändert hat (und wir verbunden sind)
    if (bleKeyboard.isConnected() && newLevel != oldLevel) {
       
       debug("Akku Änderung erkannt: " + String(oldLevel) + "% -> " + String(newLevel) + "%");
       
       // 1. Neuen Wert in die Library schreiben
       bleKeyboard.setBatteryLevel(newLevel);
       oldLevel = newLevel; // Speichern
       
       // 2. DER TRICK: Verbindung kurz trennen!
       // Das zwingt Windows dazu, sich neu zu verbinden und den Wert NEU ZU LESEN.
       debug("Erzwinge Windows-Update durch Reconnect...");
       
       // Holt sich die Server-Instanz und trennt alle Clients (Windows)
       NimBLEDevice::getServer()->disconnect(0); 
       
       // Der Code oben bei "if (!wasConnected)" sorgt dann dafür, 
       // dass Windows den ESP sofort wiederfindet.
    }
  }

  // --- TASTEN ---
  if(bleKeyboard.isConnected()) {
    if(digitalRead(buttonNextPin) == LOW) {
      checkSubscriptionStatus();
      debug("Klick: NEXT"); 
      bleKeyboard.press(KEY_LEFT_CTRL);
      bleKeyboard.press(KEY_PAGE_DOWN);
      delay(50);
      bleKeyboard.releaseAll();
      delay(350); 
    }

    if(digitalRead(buttonPrevPin) == LOW) {
      debug("Klick: PREV"); 
      bleKeyboard.press(KEY_LEFT_CTRL);
      bleKeyboard.press(KEY_PAGE_UP);
      delay(50);
      bleKeyboard.releaseAll();
      delay(350);
    }
  }
}