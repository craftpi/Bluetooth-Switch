import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog 
import json
import os
import sys
import winreg 
from bleak import BleakScanner, BleakClient
import keyboard


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
else:
    # Wenn als Skript ausgeführt
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "remote_config.json")

class BluetoothRemoteApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Remote-Switch")
        self.root.geometry("500x650") 
        self.root.iconbitmap(default=os.path.join(APP_DIR, "icon.ico"))
        self.root.minsize(500, 650)
        self.root.minimize()  # Start minimiert
        self.root.after(100, lambda: self.root.iconify()) # Minimiere nach kurzer Verzögerung
        self.root.protocol("WM_DELETE_WINDOW", self.root.iconify)  # Schließe Fenster minimizer
        
        self.client = None
        self.connected = False
        self.battery_level = 0
        
        # Default Config
        self.config = {
            "api_key": "",
            "btn1_action": "pagedown", 
            "btn2_action": "pageup"    
        }
        self.load_config()
        
        self.setup_ui()
        
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

if __name__ == "__main__":
    root = tk.Tk()
    app = BluetoothRemoteApp(root)
    root.mainloop()