#include <Arduino.h>
#include <BleKeyboard.h>
#include <NimBLEDevice.h> 

// Initialisierung
BleKeyboard bleKeyboard("OneNote Remote", "DeinName", 100);

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
}

int getBatteryPercentage() {
  // Wir machen 10 Messungen und nehmen den Durchschnitt
  long sum = 0;
  for(int i = 0; i < 10; i++) {
    sum += analogRead(batteryPin);
    delay(10);
  }
  
  int rawValue = sum / 10; 
  // Dein angepasster Faktor (z.B. 2.43 oder was du ermittelt hast)
  float voltage = (rawValue / 4095.0) * 3.3 * 2.43; 

  int percentage = 0;
  if (voltage >= 4.2) percentage = 100;
  else if (voltage <= 3.3) percentage = 0;
  else percentage = (int)((voltage - 3.3) / (4.2 - 3.3) * 100);

  if (percentage > 100) percentage = 100;

  return percentage;
}

void setup() {
  Serial.begin(115200);
  
  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  analogReadResolution(12); 

  // Startwerte setzen
  int startBatteryLevel = getBatteryPercentage();
  oldLevel = startBatteryLevel;
  bleKeyboard.setBatteryLevel(startBatteryLevel, true); // Initial mit Senden

  debug("Starte Bluetooth...");
  bleKeyboard.begin();

}

void loop() {
  if (bleKeyboard.isConnected()) {
    if (!wasConnected) {
        wasConnected = true;
        debug("Bluetooth: Verbunden!");
        bleKeyboard.setBatteryLevel(oldLevel, true);
    }
  } else {
    if (wasConnected) {
        wasConnected = false;
        debug("Verbindung weg -> Starte Advertising neu (Auto-Reconnect)");
        NimBLEDevice::getAdvertising()->start();
    }
  }
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    int newLevel = getBatteryPercentage();
    if (bleKeyboard.isConnected() && newLevel != oldLevel) {
       
       debug("Akku Änderung: " + String(oldLevel) + "% -> " + String(newLevel) + "%");
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