#include <Arduino.h>
#include <BleKeyboard.h>
#include <NimBLEDevice.h> 
#include <WiFi.h> 

// --- KONFIGURATION ---
BleKeyboard bleKeyboard("OneNote Remote", "DeinName", 100);

// PINS
const int buttonNextPin = 25; // Weckt via ext0
const int buttonPrevPin = 32; // Weckt via ext1
const int batteryPin = 36; 

const unsigned long SLEEP_TIMEOUT = 60000*5; // 5 Minuten Inaktivität bis Deep Sleep
unsigned long lastActivityTime = 0;

int pendingAction = 0;

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
  
  // Weck-Trigger scharfschalten
  esp_sleep_enable_ext0_wakeup((gpio_num_t)buttonNextPin, 0); 
  esp_sleep_enable_ext1_wakeup((1ULL << buttonPrevPin), ESP_EXT1_WAKEUP_ALL_LOW);
  
  esp_deep_sleep_start();
}

void sendNext() {
  debug("Sende: NEXT");
  bleKeyboard.press(KEY_LEFT_CTRL);
  bleKeyboard.press(KEY_PAGE_DOWN);
  delay(50);
  bleKeyboard.releaseAll();
}

void sendPrev() {
  debug("Sende: PREV");
  bleKeyboard.press(KEY_LEFT_CTRL);
  bleKeyboard.press(KEY_PAGE_UP);
  delay(50);
  bleKeyboard.releaseAll();
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_OFF);
  
  pinMode(buttonNextPin, INPUT_PULLUP);
  pinMode(buttonPrevPin, INPUT_PULLUP);
  analogReadResolution(12); 

  esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
  
  if (wakeup_reason == ESP_SLEEP_WAKEUP_EXT0) {
      debug("Aufgewacht durch NEXT Taste -> Merke Aktion!");
      pendingAction = 1; // 1 steht für NEXT
  } 
  else if (wakeup_reason == ESP_SLEEP_WAKEUP_EXT1) {
      debug("Aufgewacht durch PREV Taste -> Merke Aktion!");
      pendingAction = 2; // 2 steht für PREV
  } 
  else {
      debug("Normaler Start");
      pendingAction = 0;
  }

  int startBatteryLevel = getBatteryPercentage();
  bleKeyboard.setBatteryLevel(startBatteryLevel, true); 

  debug("Starte Bluetooth...");
  bleKeyboard.begin();
  
  lastActivityTime = millis();
}

void loop() {
  if (millis() - lastActivityTime >= SLEEP_TIMEOUT) {
    goToDeepSleep();
  }

  // --- LOGIK WENN VERBUNDEN ---
  if (bleKeyboard.isConnected()) {

    if (pendingAction != 0) {
       debug("Verbindung steht! Führe gemerkte Aktion aus...");

       delay(500); 

       if (pendingAction == 1) sendNext();
       if (pendingAction == 2) sendPrev();
       
       bleKeyboard.setBatteryLevel(getBatteryPercentage(), true);

       pendingAction = 0;
       
       lastActivityTime = millis();
    }

    if(digitalRead(buttonNextPin) == LOW) {
      sendNext();
      bleKeyboard.setBatteryLevel(getBatteryPercentage(), true);
      lastActivityTime = millis(); 
      delay(300); 
    }

    if(digitalRead(buttonPrevPin) == LOW) {
      sendPrev();
      bleKeyboard.setBatteryLevel(getBatteryPercentage(), true);
      lastActivityTime = millis(); 
      delay(300); 
    }
  } 

  if (!bleKeyboard.isConnected()) {
      lastActivityTime = millis(); 
      if (millis() > 120000) goToDeepSleep();
  }
}