import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog 
import json
import os
import sys
import time
import ctypes
import winreg 
import shutil
from bleak import BleakScanner, BleakClient
import keyboard
import pystray
from PIL import Image


HAS_AI = False
try:
    from google import genai # type: ignore
    from google.genai import types # type: ignore
    HAS_AI = True
except ImportError:
    print("ACHTUNG: 'google-genai' ist nicht installiert. KI-Funktionen deaktiviert.")

# --- CONFIG ---
SERVICE_UUID = "12345678-1234-1234-1234-1234567890ab"
CHAR_BUTTON_UUID = "12345678-1234-1234-1234-1234567890ac"
CHAR_BATTERY_UUID = "12345678-1234-1234-1234-1234567890ad"

# Config Datei liegt immer im gleichen Ordner wie die Exe/Script
if getattr(sys, 'frozen', False):
    # Wenn als EXE ausgeführt
    APP_DIR = os.path.dirname(sys.executable)
    # PyInstaller extrahiert Dateien nach _MEIPASS
    BUNDLE_DIR = sys._MEIPASS
else:
    # Wenn als Skript ausgeführt
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

CONFIG_FILE = os.path.join(APP_DIR, "remote_config.json")

# Extrahiere Icon beim ersten Start (nur bei EXE)
def extract_icon():
    """Extrahiert das Icon aus der EXE ins APP_DIR beim ersten Start"""
    icon_dest = os.path.join(APP_DIR, "icon.ico")
    
    # Wenn Icon bereits existiert, nichts tun
    if os.path.exists(icon_dest):
        return
    
    # Icon aus Bundle kopieren
    icon_source = os.path.join(BUNDLE_DIR, "icon.ico")
    if os.path.exists(icon_source):
        try:
            shutil.copy2(icon_source, icon_dest)
            print(f"Icon extrahiert nach: {icon_dest}")
        except Exception as e:
            print(f"Icon Extraktion fehlgeschlagen: {e}")

# Beim Start Icon extrahieren
extract_icon()

class BluetoothRemoteApp:
    def __init__(self, root, startup_delay=0, start_hidden=False):
        self.root = root
        self.root.title("Remote-Switch")
        self.root.geometry("500x650") 
        self.root.iconbitmap(default=os.path.join(APP_DIR, "icon.ico"))
        
        self.client = None
        self.connected = False
        self.battery_level = 0
        self.startup_delay = startup_delay
        self.tray_icon = None
        
        # Fenster-Protokoll für Schließen (verstecken statt beenden)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        # Default Config
        self.config = {
            "api_key": "",
            "btn1_action": "pagedown", 
            "btn2_action": "pageup"    
        }
        self.load_config()
        
        self.setup_ui()
        
        # System Tray Icon erstellen
        self.setup_tray_icon()
        
        # Bei Autostart: Fenster verstecken
        if start_hidden:
            self.root.after(100, self.hide_window)
        
        # Start BLE Loop
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.start_async_loop, daemon=True)
        self.thread.start()

    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#333", height=60)
        header.pack(fill="x")
        self.lbl_status = tk.Label(header, text="Suche Gerät...", bg="#333", fg="white", font=("Arial", 12, "bold"))
        self.lbl_status.pack(pady=10)
        
        # Battery
        self.lbl_battery = tk.Label(self.root, text="🔋 Akku: --%", font=("Arial", 14))
        self.lbl_battery.pack(pady=20)

        # Settings
        frame_settings = tk.LabelFrame(self.root, text="Tasten Konfiguration", padx=10, pady=10)
        frame_settings.pack(fill="x", padx=20)

        # Button 1
        tk.Label(frame_settings, text="Taste 1 (Next):").pack(anchor="w")
        self.entry_btn1 = tk.Entry(frame_settings)
        self.entry_btn1.insert(0, self.config["btn1_action"])
        self.entry_btn1.pack(fill="x", pady=(0, 5))
        
        btn_ai_1 = tk.Button(frame_settings, text="✨ KI Config Taste 1", command=lambda: self.ask_ai(1), bg="#e0e7ff")
        btn_ai_1.pack(fill="x", pady=(0, 10))

        # Button 2
        tk.Label(frame_settings, text="Taste 2 (Prev):").pack(anchor="w")
        self.entry_btn2 = tk.Entry(frame_settings)
        self.entry_btn2.insert(0, self.config["btn2_action"])
        self.entry_btn2.pack(fill="x", pady=(0, 5))
        
        btn_ai_2 = tk.Button(frame_settings, text="✨ KI Config Taste 2", command=lambda: self.ask_ai(2), bg="#e0e7ff")
        btn_ai_2.pack(fill="x", pady=(0, 10))
        
        tk.Button(frame_settings, text="💾 Speichern", command=self.save_config, bg="#d4edda").pack(fill="x", pady=10)

        # API Key
        frame_api = tk.LabelFrame(self.root, text="Google Gemini API Key", padx=10, pady=10)
        frame_api.pack(fill="x", padx=20, pady=10)
        self.entry_api = tk.Entry(frame_api, show="*")
        self.entry_api.insert(0, self.config["api_key"])
        self.entry_api.pack(fill="x")

        # --- AUTOSTART CHECKBOX ---
        self.var_autostart = tk.BooleanVar()
        self.var_autostart.set(self.is_autostart_enabled())
        cb_autostart = tk.Checkbutton(self.root, text="Automatisch mit Windows starten", 
                                      variable=self.var_autostart, command=self.toggle_autostart)
        cb_autostart.pack(side="bottom", pady=15)


    # --- AUTOSTART LOGIK ---
    def is_autostart_enabled(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "Remote-Switch")
            key.Close()
            return True
        except FileNotFoundError:
            return False

    def toggle_autostart(self):
        app_name = "Remote-Switch"
        
        # Ermittle Pfad zur Exe oder zum Skript
        exe_path = sys.executable
        if not getattr(sys, 'frozen', False):
            # Wenn Skript: Wir nutzen pythonw.exe (ohne Konsole)
            script_path = os.path.abspath(__file__)
            exe_path = f'"{exe_path.replace("python.exe", "pythonw.exe")}" "{script_path}"'
        else:
            # Wenn EXE: Pfad in Anführungszeichen
            exe_path = f'"{exe_path}"'

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            if self.var_autostart.get():
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
                messagebox.showinfo("Autostart", "Autostart aktiviert!\nDie App startet nun beim Login minimiert.")
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                    messagebox.showinfo("Autostart", "Autostart deaktiviert!")
                except FileNotFoundError:
                    pass
            key.Close()
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte Registry nicht ändern: {e}")
            self.var_autostart.set(not self.var_autostart.get())

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config.update(json.load(f))
            except Exception:
                pass

    def save_config(self):
        self.config["btn1_action"] = self.entry_btn1.get()
        self.config["btn2_action"] = self.entry_btn2.get()
        self.config["api_key"] = self.entry_api.get()
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f)
        messagebox.showinfo("Info", "Konfiguration gespeichert!")

    def ask_ai(self, btn_num):
        if not HAS_AI:
            messagebox.showerror("Fehler", "Library fehlt. pip install google-genai")
            return

        api_key = self.entry_api.get()
        if not api_key:
            messagebox.showerror("Fehler", "Bitte API Key eingeben!")
            return

        prompt = simpledialog.askstring("KI Konfiguration", "Was soll die Taste tun? (z.B. 'Lautstärke hoch', 'Screenshot machen')")
        if not prompt: return

        try:
            client = genai.Client(api_key=api_key) # type: ignore
            
            system_instruction = """
            You are a keyboard configuration assistant.
            Convert the user request into a Python 'keyboard' library hotkey string.
            Examples:
            'Nächste Seite' -> 'pagedown'
            'Lauter' -> 'volume up'
            'Copy' -> 'ctrl+c'
            'Screenshot' -> 'print screen'
            
            ONLY return the key string. No markdown, no explanations.
            """
            
            response_text = ""
            used_model = ""

            print("Lade verfügbare Modelle...")
            available_models = []
            
            try:
                for m in client.models.list(): # type: ignore
                    model_name = m.name
                    if "embedding" not in model_name and "gemini" in model_name:
                         available_models.append(model_name)
            except Exception as e:
                print(f"Fallback Modelle nutzen: {e}")
                available_models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"]

            def model_priority(name):
                name = name.lower()
                if "flash" in name and "exp" not in name: return 0  
                if "pro" in name and "exp" not in name: return 1    
                if "flash" in name: return 2                        
                return 3                                            

            available_models.sort(key=model_priority)

            for model_name in available_models:
                clean_name = model_name.replace("models/", "")
                try:
                    print(f"Versuche Modell: {clean_name}...")
                    response = client.models.generate_content( # type: ignore
                        model=clean_name,
                        contents=prompt,
                        config=types.GenerateContentConfig( # type: ignore
                            system_instruction=system_instruction,
                            temperature=0.1
                        )
                    )
                    if response.text:
                        response_text = response.text
                        used_model = clean_name
                        print(f"✅ Erfolg mit {clean_name}!")
                        break 
                except Exception:
                    pass
            
            if not response_text:
                raise Exception("Kein verfügbares Modell antwortet.")

            key_code = response_text.strip().lower()
            key_code = key_code.replace('`', '').replace("'", "").replace('"', "")

            if btn_num == 1:
                self.entry_btn1.delete(0, tk.END)
                self.entry_btn1.insert(0, key_code)
            else:
                self.entry_btn2.delete(0, tk.END)
                self.entry_btn2.insert(0, key_code)
                
            self.save_config()
            messagebox.showinfo("Erfolg", f"Taste konfiguriert auf: {key_code}\n(Modell: {used_model})")
            
        except Exception as e:
            messagebox.showerror("KI Fehler", f"Konnte KI nicht erreichen:\n{str(e)}")

    def start_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.ble_main())

    async def ble_main(self):
        # Beim Autostart: Warte bis Bluetooth bereit ist
        if self.startup_delay > 0:
            self.update_status(f"Warte {self.startup_delay}s auf Bluetooth...", "orange")
            await asyncio.sleep(self.startup_delay)
        
        # Warte bis BLE-Scanner verfügbar ist (max 30s)
        for attempt in range(6):
            try:
                # Test ob Scanner funktioniert
                await BleakScanner.discover(timeout=0.5) # type: ignore
                break  # Scanner funktioniert!
            except Exception as e:
                if attempt < 5:
                    self.update_status(f"Bluetooth noch nicht bereit... ({attempt+1}/6)", "orange")
                    await asyncio.sleep(5)
                else:
                    self.update_status("⚠️ Bluetooth-Problem! Retry...", "red")
        
        while True:
            self.update_status("Scanne nach Remote-Switch...", "orange")
            try:
                device = await BleakScanner.find_device_by_name("Remote-Switch", timeout=5.0) # type: ignore
                
                if device:
                    self.update_status(f"Gefunden! Verbinde...", "blue")
                    try:
                        async with BleakClient(device, disconnected_callback=self.on_disconnect) as client:
                            self.client = client
                            self.connected = True
                            self.update_status("✅ Verbunden & Bereit", "green")
                            
                            await client.start_notify(CHAR_BUTTON_UUID, self.notification_handler)
                            try:
                                await client.start_notify(CHAR_BATTERY_UUID, self.battery_handler)
                            except Exception:
                                pass

                            while client.is_connected:
                                await asyncio.sleep(1)
                    except Exception as e:
                        print(f"Connection Error: {e}")
                        self.update_status("Verbindungsfehler. Retry in 2s...", "red")
                
                await asyncio.sleep(2)
            except Exception as e:
                print(f"Scan Error: {e}")
                await asyncio.sleep(2)

    def on_disconnect(self, client):
        self.connected = False
        self.update_status("Verbindung verloren.", "red")

    def notification_handler(self, sender, data):
        try:
            val = int.from_bytes(data, byteorder="little")
            action = ""
            if val == 1: action = self.config["btn1_action"]
            elif val == 2: action = self.config["btn2_action"]
                
            print(f"Trigger: {action}")
            if action: keyboard.send(action)
        except Exception as e:
            print(f"Key Error: {e}")

    def battery_handler(self, sender, data):
        try:
            val = int.from_bytes(data, byteorder="little")
            self.battery_level = val
            self.root.after(0, lambda: self.lbl_battery.config(text=f"🔋 Akku: {val}%"))
        except Exception:
            pass

    def update_status(self, text, color="black"):
        try:
            self.root.after(0, lambda: self.lbl_status.config(text=text, fg=color))
        except Exception:
            pass

    # --- SYSTEM TRAY FUNKTIONEN ---
    def setup_tray_icon(self):
        """Erstellt das System Tray Icon"""
        try:
            # Lade Icon aus .ico Datei oder erstelle ein einfaches
            icon_path = os.path.join(APP_DIR, "icon.ico")
            if os.path.exists(icon_path):
                image = Image.open(icon_path)
            else:
                # Fallback: Erstelle einfaches Icon
                image = Image.new('RGB', (64, 64), color='blue')
            
            # Tray Menu
            menu = pystray.Menu(
                pystray.MenuItem("Einstellungen öffnen", self.show_window, default=True),  # Default = Doppelklick
                pystray.MenuItem("Beenden", self.quit_app)
            )
            
            # Erstelle Tray Icon
            self.tray_icon = pystray.Icon(
                "Remote-Switch",
                image,
                "Remote-Switch",
                menu,
                on_activated=self.show_window  # Doppelklick öffnet Fenster
            )
            
            # Starte Tray Icon in separatem Thread
            tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            tray_thread.start()
            
        except Exception as e:
            print(f"Tray Icon Fehler: {e}")
    
    def show_window(self, icon=None, item=None):
        """Zeigt das Hauptfenster"""
        self.root.after(0, self._show_window)
    
    def _show_window(self):
        """Interne Methode zum Anzeigen (muss im Tkinter-Thread laufen)"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def hide_window(self):
        """Versteckt das Fenster (läuft weiter im Tray)"""
        self.root.withdraw()
    
    def quit_app(self, icon=None, item=None):
        """Beendet die App komplett"""
        try:
            if self.tray_icon:
                self.tray_icon.stop()
            self.root.after(0, self.root.destroy)  # Sauberes Beenden im Tkinter-Thread
        except Exception:
            pass

if __name__ == "__main__":
    # Single-instance guard (Windows mutex)
    mutex_name = "Global\\Remote-Switch-SingleInstance"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:
        messagebox.showinfo("Remote-Switch", "Die App läuft bereits.")
        sys.exit(0)

    # Erkenne ob via Autostart gestartet (weniger als 3 Min nach Boot)
    uptime_sec = time.time() - os.path.getmtime("C:\\Windows\\System32")
    is_autostart = uptime_sec < 180  # < 3 Minuten nach Boot
    
    root = tk.Tk()
    # Bei Autostart: 8 Sekunden warten auf Bluetooth + versteckt starten
    startup_delay = 8 if is_autostart else 0
    app = BluetoothRemoteApp(root, startup_delay=startup_delay, start_hidden=is_autostart)
    root.mainloop()