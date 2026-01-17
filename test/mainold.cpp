#include <Arduino.h>
#include <BleKeyboard.h>

// Initialisierung
BleKeyboard bleKeyboard("OneNote Remote", "DeinName");

// PINS FÜR WEMOS D1 MINI ESP32
const int buttonNextPin = 18; 
const int buttonPrevPin = 19;
const int batteryPin = 36; // Lötbrücke J2 auf Shield muss zu sein!

// Zeitsteuerung
unsigned long previousMillis = 0;
const long interval = 600;  

// --- HILFSFUNKTION: Akku messen und Prozent berechnen ---
int getBatteryPercentage() {
  int rawValue = analogRead(batteryPin);
  
  // Spannung berechnen (Faktor 2.0 für Standard Shield Teiler)
  // Kalibrierung: Wenn Wert falsch, ändere die 2.0 leicht (z.B. 2.1 oder 1.9)
  float voltage = (rawValue / 4095.0) * 3.3 * 2.0; 

  int percentage = 0;
  if (voltage >= 4.2) percentage = 100;
  else if (voltage <= 3.3) percentage = 0;
  else percentage = (int)((voltage - 3.3) / (4.2 - 3.3) * 100);

  // Debug-Ausgabe im Serial Monitor
  Serial.print("Batterie: ");
  Serial.print(voltage);
  Serial.print("V -> ");
  Serial.print(percentage);
  Serial.println("%");
  
  return percentage;
}

void setup() {
  Serial.begin(115200);
  
  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  
  // Wichtig für ESP32: 12-Bit Auflösung einstellen
  analogReadResolution(12); 

  // --- DER TRICK FÜR WINDOWS ---
  // 1. Wir messen den Akku BEVOR Bluetooth startet
  Serial.println("Messe Akku für Startwert...");
  int startBatteryLevel = getBatteryPercentage();
  
  // 2. Wir setzen den Wert in die Library
  // Wenn Windows sich gleich verbindet, ist dieser Wert schon da!
  bleKeyboard.setBatteryLevel(startBatteryLevel);

  // 3. JETZT erst starten wir Bluetooth
  Serial.println("Starte Bluetooth (NimBLE)...");
  bleKeyboard.begin();
}

void loop() {
  // --- AKKU AKTUALISIERUNG (alle 60 Sek) ---
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    // Nur messen und senden, wenn wir verbunden sind, um Ressourcen zu sparen
    if(bleKeyboard.isConnected()) {
      int newLevel = getBatteryPercentage();
      bleKeyboard.setBatteryLevel(newLevel);
    }
  }

  // --- TASTEN ---
  if(bleKeyboard.isConnected()) {
    
    // NÄCHSTE SEITE
    if(digitalRead(buttonNextPin) == LOW) {
      Serial.println("Klick: Next");
      bleKeyboard.press(KEY_LEFT_CTRL);
      bleKeyboard.press(KEY_PAGE_DOWN);
      delay(50);
      bleKeyboard.releaseAll();
      delay(350); 
    }

    // VORHERIGE SEITE
    if(digitalRead(buttonPrevPin) == LOW) {
      Serial.println("Klick: Prev");
      bleKeyboard.press(KEY_LEFT_CTRL);
      bleKeyboard.press(KEY_PAGE_UP);
      delay(50);
      bleKeyboard.releaseAll();
      delay(350);
    }
  }
}