import ctypes

# --- DPI AWARENESS FIX ---
# Verhindert verschwommene Schrift auf hochauflösenden Displays (wie Laptops)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog 
import json
import os
import sys
import time
import winreg 
import shutil
from bleak import BleakScanner, BleakClient
import keyboard
import pystray
from PIL import Image

# --- MODERNE WIN 11 THEMES ---
try:
    import sv_ttk
    HAS_SV_TTK = True
except ImportError:
    HAS_SV_TTK = False

try:
    import pywinstyles
    HAS_MICA = True
except ImportError:
    HAS_MICA = False

mutex_name = "RemoteSwitchAppMutex"
mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
if ctypes.windll.kernel32.GetLastError() == 183:
    print("App läuft bereits!")
    sys.exit(0)

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

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

CONFIG_FILE = os.path.join(APP_DIR, "remote_config.json")

def extract_icon():
    icon_dest = os.path.join(APP_DIR, "icon.ico")
    if os.path.exists(icon_dest): return
    icon_source = os.path.join(BUNDLE_DIR, "icon.ico")
    if os.path.exists(icon_source):
        try: shutil.copy2(icon_source, icon_dest)
        except Exception as e: print(f"Icon Extraktion fehlgeschlagen: {e}")

extract_icon()

class BluetoothRemoteApp:
    def __init__(self, root, startup_delay=0, start_hidden=False):
        self.root = root
        self.root.title("Remote-Switch")
        self.root.geometry("560x680") 
        self.root.iconbitmap(default=os.path.join(APP_DIR, "icon.ico"))
        
        # Win 11 Theme aktivieren
        if HAS_SV_TTK:
            sv_ttk.set_theme("dark")
            
        if HAS_MICA:
            try:
                # Wendet den "Liquid Glass" (Mica) Effekt an
                pywinstyles.apply_style(self.root, "mica")
                # Titelleiste passend dunkel färben
                pywinstyles.change_header_color(self.root, color="#1c1c1c")
            except Exception:
                pass
        
        self.client = None
        self.connected = False
        self.battery_level = 0
        self.startup_delay = startup_delay
        self.tray_icon = None
        
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        self.config = {
            "api_key": "",
            "btn1_action": "pagedown", 
            "btn2_action": "pageup",   
            "btn3_action": "print screen" 
        }
        self.load_config()
        self.setup_ui()
        self.setup_tray_icon()
        
        if start_hidden:
            self.root.after(100, self.hide_window)
        
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.start_async_loop, daemon=True)
        self.thread.start()

    def setup_ui(self):
        # Transparenter Container für Mica-Effekt
        container = ttk.Frame(self.root, padding=25)
        container.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 20))
        
        self.lbl_status = ttk.Label(header, text="Suche Gerät...", font=("Segoe UI Variable Display", 14, "bold"), foreground="#38bdf8")
        self.lbl_status.pack(side="left")
        
        btn_reconnect = ttk.Button(header, text="🔄 Reconnect", command=self.force_reconnect)
        btn_reconnect.pack(side="right")

        # Battery
        self.lbl_battery = ttk.Label(container, text="🔋 Akku: --%", font=("Segoe UI Variable Display", 18))
        self.lbl_battery.pack(pady=(10, 25))

        # Settings Frame (Modernes LabelFrame)
        frame_settings = ttk.LabelFrame(container, text=" Tasten Konfiguration ", padding=15)
        frame_settings.pack(fill="x", pady=10)

        def create_key_row(parent, label_text, default_val, btn_num):
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=8)
            
            ttk.Label(row, text=label_text, font=("Segoe UI Variable Text", 10), width=15).pack(side="left", anchor="w")
            
            entry = ttk.Entry(row, font=("Segoe UI Variable Text", 10))
            entry.insert(0, default_val)
            entry.pack(side="left", padx=10, expand=True, fill="x")
            
            ai_btn = ttk.Button(row, text="✨ KI Config", command=lambda: self.ask_ai(btn_num))
            ai_btn.pack(side="right")
            return entry

        self.entry_btn1 = create_key_row(frame_settings, "Taste 1 (Next):", self.config["btn1_action"], 1)
        self.entry_btn2 = create_key_row(frame_settings, "Taste 2 (Prev):", self.config["btn2_action"], 2)
        self.entry_btn3 = create_key_row(frame_settings, "Beide Tasten:", self.config.get("btn3_action", "print screen"), 3)

        # Speichern mit "Accent" Style (Haupt-Button Farbe von Win11)
        btn_save = ttk.Button(frame_settings, text="💾 Speichern", command=self.save_config, style="Accent.TButton")
        btn_save.pack(fill="x", pady=(15, 0))

        # API Key Frame
        frame_api = ttk.LabelFrame(container, text=" Google Gemini API Key ", padding=15)
        frame_api.pack(fill="x", pady=15)
        
        self.entry_api = ttk.Entry(frame_api, show="*", font=("Segoe UI Variable Text", 10))
        self.entry_api.insert(0, self.config["api_key"])
        self.entry_api.pack(fill="x")

        # Autostart Checkbox als Win11 Toggle Switch (falls sv_ttk aktiv ist)
        self.var_autostart = tk.BooleanVar(value=self.is_autostart_enabled())
        switch_style = "Switch.TCheckbutton" if HAS_SV_TTK else ""
        cb_autostart = ttk.Checkbutton(container, text="Automatisch mit Windows starten", 
                                      variable=self.var_autostart, command=self.toggle_autostart, style=switch_style)
        cb_autostart.pack(side="bottom", pady=20)

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
        exe_path = sys.executable
        if not getattr(sys, 'frozen', False):
            script_path = os.path.abspath(__file__)
            exe_path = f'"{exe_path.replace("python.exe", "pythonw.exe")}" "{script_path}"'
        else:
            exe_path = f'"{exe_path}"'

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            if self.var_autostart.get():
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
                messagebox.showinfo("Autostart", "Autostart aktiviert!\nDie App startet nun beim Login minimiert.")
            else:
                try: winreg.DeleteValue(key, app_name)
                except FileNotFoundError: pass
                messagebox.showinfo("Autostart", "Autostart deaktiviert!")
            key.Close()
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte Registry nicht ändern: {e}")
            self.var_autostart.set(not self.var_autostart.get())

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config.update(json.load(f))
            except Exception: pass

    def save_config(self):
        self.config["btn1_action"] = self.entry_btn1.get()
        self.config["btn2_action"] = self.entry_btn2.get()
        self.config["btn3_action"] = self.entry_btn3.get()
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
            ONLY return the key string. No markdown, no explanations.
            """
            
            response_text = ""
            available_models = ["gemini-2.0-flash-exp", "gemini-1.5-flash", "gemini-1.5-pro"]
            
            for model_name in available_models:
                try:
                    response = client.models.generate_content( # type: ignore
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig( # type: ignore
                            system_instruction=system_instruction,
                            temperature=0.1
                        )
                    )
                    if response.text:
                        response_text = response.text
                        break 
                except Exception:
                    pass
            
            if not response_text:
                raise Exception("Kein KI Modell verfügbar.")

            key_code = response_text.strip().lower().replace('`', '').replace("'", "").replace('"', "")
            target_entry = {1: self.entry_btn1, 2: self.entry_btn2, 3: self.entry_btn3}[btn_num]
            target_entry.delete(0, tk.END)
            target_entry.insert(0, key_code)
            self.save_config()
            
        except Exception as e:
            messagebox.showerror("KI Fehler", f"Konnte KI nicht erreichen:\n{str(e)}")

    # --- BLE LOGIK ---
    def force_reconnect(self):
        self.update_status("🔄 Neustart...", "#fbbf24")
        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_disconnect(), self.loop)

    async def _async_disconnect(self):
        if self.client:
            try: await self.client.disconnect()
            except Exception: pass
            finally: self.client = None
        self.connected = False

    def start_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.ble_main())

    async def ble_main(self):
        if self.startup_delay > 0:
            self.update_status(f"Warte auf Bluetooth...", "#fbbf24")
            await asyncio.sleep(self.startup_delay)
        
        for attempt in range(6):
            try:
                await BleakScanner.discover(timeout=0.5) # type: ignore
                break
            except Exception:
                if attempt < 5:
                    self.update_status(f"Bluetooth nicht bereit... ({attempt+1}/6)", "#fbbf24")
                    await asyncio.sleep(5)
                else:
                    self.update_status("⚠️ Bluetooth-Problem!", "#ef4444")
        
        while True:
            if not self.connected:
                self.update_status("Scanne nach Remote-Switch...", "#fbbf24")
                try:
                    device = await BleakScanner.find_device_by_name("Remote-Switch", timeout=5.0) # type: ignore
                    if device:
                        self.update_status(f"Gefunden! Verbinde...", "#38bdf8")
                        try:
                            self.client = BleakClient(device, disconnected_callback=self.on_disconnect)
                            await self.client.connect()
                            
                            self.connected = True
                            self.update_status("✅ Verbunden & Bereit", "#4ade80")
                            
                            await self.client.start_notify(CHAR_BUTTON_UUID, self.notification_handler)
                            try: await self.client.start_notify(CHAR_BATTERY_UUID, self.battery_handler)
                            except Exception: pass

                            while self.connected and self.client.is_connected:
                                await asyncio.sleep(1)
                                
                        except Exception as e:
                            print(f"Connection Error: {e}")
                            self.update_status("Verbindungsfehler. Retry...", "#ef4444")
                            await self._async_disconnect()
                    else:
                        await asyncio.sleep(2)
                except Exception as e:
                    print(f"Scan Error: {e}")
                    await asyncio.sleep(2)
            else:
                await asyncio.sleep(1)

    def on_disconnect(self, client):
        self.connected = False
        self.update_status("Verbindung verloren.", "#ef4444")

    def notification_handler(self, sender, data):
        try:
            val = int.from_bytes(data, byteorder="little")
            action = ""
            if val == 1: action = self.config["btn1_action"]
            elif val == 2: action = self.config["btn2_action"]
            elif val == 3: action = self.config.get("btn3_action", "")
                
            print(f"Trigger: {action}")
            if action: keyboard.send(action)
        except Exception as e:
            print(f"Key Error: {e}")

    def battery_handler(self, sender, data):
        try:
            val = int.from_bytes(data, byteorder="little")
            self.battery_level = val
            self.root.after(0, lambda: self.lbl_battery.config(text=f"🔋 Akku: {val}%"))
        except Exception: pass

    def update_status(self, text, color="white"):
        try: self.root.after(0, lambda: self.lbl_status.config(text=text, foreground=color))
        except Exception: pass

    # --- SYSTEM TRAY FUNKTIONEN ---
    def setup_tray_icon(self):
        try:
            icon_path = os.path.join(APP_DIR, "icon.ico")
            if os.path.exists(icon_path): image = Image.open(icon_path)
            else: image = Image.new('RGB', (64, 64), color='blue')
            
            menu = pystray.Menu(
                pystray.MenuItem("Einstellungen öffnen", self.show_window, default=True),
                pystray.MenuItem("Beenden", self.quit_app)
            )
            self.tray_icon = pystray.Icon("Remote-Switch", image, "Remote-Switch", menu, on_activated=self.show_window)
            tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            tray_thread.start()
        except Exception as e: print(f"Tray Icon Fehler: {e}")
    
    def show_window(self, icon=None, item=None): self.root.after(0, self._show_window)
    
    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def hide_window(self): self.root.withdraw()
    
    def quit_app(self, icon=None, item=None):
        try:
            if self.tray_icon: self.tray_icon.stop()
            self.root.after(0, self.root.destroy)
        except Exception: pass

if __name__ == "__main__":
    uptime_sec = time.time() - os.path.getmtime("C:\\Windows\\System32")
    is_autostart = uptime_sec < 180 
    
    root = tk.Tk()
    startup_delay = 8 if is_autostart else 0
    app = BluetoothRemoteApp(root, startup_delay=startup_delay, start_hidden=is_autostart)
    root.mainloop()