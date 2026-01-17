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
const long interval = 60000;  // Alle 60 Sekunden messen

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
  float voltage = (rawValue / 4095.0) * 3.3 * 2.43; 
  
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
  bleKeyboard.setBatteryLevel(startBatteryLevel, true); // Initial mit Senden

  Serial.println("Starte Bluetooth...");
  bleKeyboard.begin();

}

void loop() {
  // --- AUTO-RECONNECT LOGIK ---
  // (Diese behalten wir, falls Windows mal von sich aus trennt,
  // damit der ESP sofort wieder sichtbar wird)
  if (bleKeyboard.isConnected()) {
    if (!wasConnected) {
        wasConnected = true;
        debug("Bluetooth: Verbunden!");
        // Einmaliges Update beim Verbinden
        bleKeyboard.setBatteryLevel(oldLevel, true);
    }
  } else {
    // Wenn Verbindung weg ist -> Sofort wieder rufen!
    if (wasConnected) {
        wasConnected = false;
        debug("Verbindung weg -> Starte Advertising neu (Auto-Reconnect)");
        NimBLEDevice::getAdvertising()->start();
    }
  }

  // --- AKKU MESSUNG (JETZT SANFT) ---
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    
    // Immer messen
    int newLevel = getBatteryPercentage();
    
    // Wenn wir verbunden sind UND sich der Wert geändert hat
    if (bleKeyboard.isConnected() && newLevel != oldLevel) {
       
       debug("Akku Änderung: " + String(oldLevel) + "% -> " + String(newLevel) + "%");
       
       // HIER IST DER UNTERSCHIED:
       // Einfach nur senden. Dank deinem Library-Fix ("true") 
       // wird das jetzt sofort an Windows gepusht.
       // Kein Disconnect mehr nötig!
       bleKeyboard.setBatteryLevel(newLevel, true);
       
       oldLevel = newLevel; 
    }
  }

  // --- TASTEN ---
  if(bleKeyboard.isConnected()) {
    if(digitalRead(buttonNextPin) == LOW) {
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