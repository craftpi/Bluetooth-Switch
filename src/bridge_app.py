import argparse
import asyncio
import ctypes
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import winreg
from ctypes import wintypes

# --- DPI AWARENESS FIX ---
# Verhindert verschwommene Schrift auf hochauflösenden Displays (wie Laptops)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

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

HAS_AI = False
try:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    HAS_AI = True
except ImportError:
    HAS_AI = False

APP_NAME = "Remote-Switch"
MUTEX_NAME = "RemoteSwitchAppMutex"
SERVICE_UUID = "12345678-1234-1234-1234-1234567890ab"
CHAR_BUTTON_UUID = "12345678-1234-1234-1234-1234567890ac"
CHAR_BATTERY_UUID = "12345678-1234-1234-1234-1234567890ad"
HOTKEY_MAX_LENGTH = 120
# Erlaubt übliche Tastenkombinationen (z. B. ctrl+shift+s, pagedown, print screen).
HOTKEY_RE = re.compile(r"^[A-Za-z0-9 +_\-*,.]+$")
DEFAULT_CONFIG = {
    "btn1_action": "pagedown",
    "btn2_action": "pageup",
    "btn3_action": "print screen",
}

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

LEGACY_CONFIG_FILE = os.path.join(APP_DIR, "remote_config.json")
LOG = logging.getLogger(APP_NAME)


def _acquire_single_instance_mutex():
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:
        print("App läuft bereits!")
        sys.exit(0)
    return handle


_MUTEX_HANDLE = _acquire_single_instance_mutex()


def setup_logging():
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), APP_NAME)
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "remote-switch.log")
        handlers = [logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()]
    except Exception:
        handlers = [logging.StreamHandler()]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def resolve_config_file():
    appdata = os.environ.get("APPDATA")
    if appdata:
        preferred_dir = os.path.join(appdata, APP_NAME)
        preferred = os.path.join(preferred_dir, "remote_config.json")
        try:
            os.makedirs(preferred_dir, exist_ok=True)
            if os.path.exists(LEGACY_CONFIG_FILE) and not os.path.exists(preferred):
                shutil.copy2(LEGACY_CONFIG_FILE, preferred)
                LOG.info("Konfiguration nach %%APPDATA%% migriert: %s", preferred)
            return preferred
        except Exception as exc:
            LOG.warning("Konnte %%APPDATA%%-Konfigurationspfad nicht verwenden: %s", exc)
    return LEGACY_CONFIG_FILE


CONFIG_FILE = resolve_config_file()
CREDENTIAL_TARGET = f"{APP_NAME}/GeminiAPIKey"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_credential_secret(target=CREDENTIAL_TARGET):
    cred_ptr = ctypes.POINTER(CREDENTIAL)()
    try:
        ok = ctypes.windll.advapi32.CredReadW(wintypes.LPCWSTR(target), CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
        if not ok or not cred_ptr:
            return ""
        cred = cred_ptr.contents
        if cred.CredentialBlobSize and cred.CredentialBlob:
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return blob.decode("utf-16-le", errors="ignore").rstrip("\x00").strip()
        return ""
    except Exception as exc:
        LOG.info("Credential Manager konnte nicht gelesen werden: %s", exc)
        return ""
    finally:
        try:
            if cred_ptr:
                ctypes.windll.advapi32.CredFree(cred_ptr)
        except Exception:
            pass


def write_credential_secret(secret, target=CREDENTIAL_TARGET):
    secret = str(secret or "")
    blob = secret.encode("utf-16-le")
    blob_buffer = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = None
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = APP_NAME
    try:
        ok = ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0)
        if not ok:
            raise ctypes.WinError()
        return True
    except Exception as exc:
        LOG.warning("Credential Manager konnte nicht geschrieben werden: %s", exc)
        return False


def delete_credential_secret(target=CREDENTIAL_TARGET):
    try:
        ok = ctypes.windll.advapi32.CredDeleteW(wintypes.LPCWSTR(target), CRED_TYPE_GENERIC, 0)
        if not ok:
            err = ctypes.get_last_error()
            if err != 1168:  # ERROR_NOT_FOUND
                raise ctypes.WinError(err)
        return True
    except Exception as exc:
        LOG.info("Credential Manager-Eintrag konnte nicht gelöscht werden: %s", exc)
        return False


def extract_icon():
    icon_dest = os.path.join(APP_DIR, "icon.ico")
    if os.path.exists(icon_dest):
        return icon_dest
    icon_source = os.path.join(BUNDLE_DIR, "icon.ico")
    if os.path.exists(icon_source):
        try:
            shutil.copy2(icon_source, icon_dest)
            return icon_dest
        except Exception as exc:
            LOG.warning("Icon-Extraktion fehlgeschlagen: %s", exc)
    return icon_dest


ICON_PATH = extract_icon()


def normalize_hotkey(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*\+\s*", "+", text)
    if not text:
        return ""
    if len(text) > HOTKEY_MAX_LENGTH:
        raise ValueError(f"Hotkey ist zu lang (max. {HOTKEY_MAX_LENGTH} Zeichen).")
    if not HOTKEY_RE.fullmatch(text):
        raise ValueError(
            "Ungültiger Hotkey. Erlaubt sind Buchstaben, Zahlen, Leerzeichen, +, -, _, * und Komma."
        )
    return text


def validate_hotkey(value):
    try:
        return normalize_hotkey(value)
    except ValueError:
        return ""


class BluetoothRemoteApp:
    def __init__(self, root, startup_delay=0, start_hidden=False):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("760x880")
        self.root.minsize(720, 820)
        if os.path.exists(ICON_PATH):
            try:
                self.root.iconbitmap(default=ICON_PATH)
            except Exception:
                pass

        if HAS_SV_TTK:
            sv_ttk.set_theme("dark")

        if HAS_MICA:
            try:
                pywinstyles.apply_style(self.root, "mica")
                pywinstyles.change_header_color(self.root, color="#1c1c1c")
            except Exception:
                pass

        self.client = None
        self.connected = False
        self.battery_level = 0
        self.startup_delay = startup_delay
        self.tray_icon = None
        self._closing = False

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.config = dict(DEFAULT_CONFIG)
        self.api_key = ""
        self.load_config()
        self.setup_ui()
        self.setup_tray_icon()

        if start_hidden:
            self.root.after(100, self.hide_window)

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.start_async_loop, daemon=True, name="ble-loop")
        self.thread.start()

    def setup_ui(self):
        style = ttk.Style()
        style.configure("Accent.TButton", font=("Segoe UI Variable Display", 13, "bold"), padding=10)
        style.configure("KI.TButton", font=("Segoe UI Variable Text", 11, "bold"), padding=5)

        container = ttk.Frame(self.root, padding=25)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 20))

        self.lbl_status = ttk.Label(
            header,
            text="Suche Gerät...",
            font=("Segoe UI Variable Display", 14, "bold"),
            foreground="#38bdf8",
        )
        self.lbl_status.pack(side="left")

        btn_reconnect = ttk.Button(header, text="🔄 Reconnect", command=self.force_reconnect)
        btn_reconnect.pack(side="right")

        self.lbl_battery = ttk.Label(container, text="🔋 Akku: --%", font=("Segoe UI Variable Display", 18))
        self.lbl_battery.pack(pady=(10, 25))

        lbl_settings_title = ttk.Label(
            container, text=" Tasten Konfiguration ", font=("Segoe UI Variable Display", 11, "bold")
        )
        frame_settings = ttk.LabelFrame(container, labelwidget=lbl_settings_title, padding=15)
        frame_settings.pack(fill="x", pady=10)

        def create_key_row(parent, label_text, default_val, btn_num):
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=8)
            ttk.Label(row, text=label_text, font=("Segoe UI Variable Text", 13), width=15).pack(
                side="left", anchor="w"
            )
            entry = ttk.Entry(row, font=("Segoe UI Variable Text", 13))
            entry.insert(0, default_val)
            entry.pack(side="left", padx=10, expand=True, fill="x")
            ai_btn = ttk.Button(row, text="✨ KI Config", command=lambda: self.ask_ai(btn_num), style="KI.TButton")
            ai_btn.pack(side="right")
            return entry

        self.entry_btn1 = create_key_row(frame_settings, "Taste 1 (Next):", self.config["btn1_action"], 1)
        self.entry_btn2 = create_key_row(frame_settings, "Taste 2 (Prev):", self.config["btn2_action"], 2)
        self.entry_btn3 = create_key_row(frame_settings, "Beide Tasten:", self.config["btn3_action"], 3)

        btn_save = ttk.Button(frame_settings, text="💾 Speichern", command=self.save_config, style="Accent.TButton")
        btn_save.pack(fill="x", pady=(15, 0))

        lbl_api_title = ttk.Label(
            container, text=" Google Gemini API Key ", font=("Segoe UI Variable Display", 11, "bold")
        )
        frame_api = ttk.LabelFrame(container, labelwidget=lbl_api_title, padding=15)
        frame_api.pack(fill="x", pady=15)

        self.entry_api = ttk.Entry(frame_api, show="*", font=("Segoe UI Variable Text", 13))
        self.entry_api.insert(0, self.api_key)
        self.entry_api.pack(fill="x")
        ttk.Label(
            frame_api,
            text="Der eingegebene API-Key wird beim Speichern automatisch in der Windows Anmeldeinformationsverwaltung abgelegt. Optional kann REMOTE_SWITCH_GEMINI_API_KEY als Umgebungsvariable gesetzt werden (hat Vorrang).",
            font=("Segoe UI Variable Text", 9),
            foreground="#94a3b8",
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        self.var_autostart = tk.BooleanVar(value=self.is_autostart_enabled())
        switch_style = "Switch.TCheckbutton" if HAS_SV_TTK else ""
        cb_autostart = ttk.Checkbutton(
            container,
            text="Automatisch mit Windows starten",
            variable=self.var_autostart,
            command=self.toggle_autostart,
            style=switch_style,
        )
        cb_autostart.pack(side="bottom", pady=(12, 8), anchor="w")

    def is_autostart_enabled(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            winreg.QueryValueEx(key, APP_NAME)
            key.Close()
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            LOG.warning("Autostart-Status konnte nicht gelesen werden: %s", exc)
            return False

    def toggle_autostart(self):
        exe_path = sys.executable
        if not getattr(sys, "frozen", False):
            script_path = os.path.abspath(__file__)
            pythonw = exe_path.replace("python.exe", "pythonw.exe")
            exe_path = f'"{pythonw}" "{script_path}" --autostart'
        else:
            exe_path = f'"{exe_path}" --autostart'

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_ALL_ACCESS,
            )
            if self.var_autostart.get():
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
                messagebox.showinfo("Autostart", "Autostart aktiviert!\nDie App startet nun beim Login minimiert.")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
                messagebox.showinfo("Autostart", "Autostart deaktiviert!")
            key.Close()
        except Exception as exc:
            messagebox.showerror("Fehler", f"Konnte Registry nicht ändern: {exc}")
            self.var_autostart.set(not self.var_autostart.get())

    def load_config(self):
        env_key = os.environ.get("REMOTE_SWITCH_GEMINI_API_KEY", "").strip()
        config_key = ""
        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict):
                    data = loaded
                    for key, default in DEFAULT_CONFIG.items():
                        self.config[key] = validate_hotkey(data.get(key, default)) or default
                    config_key = str(data.get("api_key", "") or "").strip()
            except Exception as exc:
                LOG.warning("Konfiguration konnte nicht geladen werden: %s", exc)

        credential_key = read_credential_secret()
        migrated_to_credential = False
        if config_key and not credential_key and not env_key:
            if write_credential_secret(config_key):
                credential_key = config_key
                config_key = ""
                migrated_to_credential = True
                LOG.info("API-Key aus Konfigurationsdatei in Windows Credential Manager migriert.")

        # Env-Var hat Vorrang (bewusster Override); danach Credential Manager; danach Legacy-Config.
        self.api_key = env_key or credential_key or config_key

        # Bei erfolgreicher Migration Klartext-Key aus Config-Datei entfernen.
        if migrated_to_credential and os.path.exists(CONFIG_FILE):
            try:
                clean = dict(DEFAULT_CONFIG)
                if isinstance(data, dict):
                    for key in DEFAULT_CONFIG:
                        clean[key] = validate_hotkey(data.get(key, clean[key])) or clean[key]
                clean["api_key"] = ""
                os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
                temp_file = CONFIG_FILE + ".tmp"
                with open(temp_file, "w", encoding="utf-8") as file:
                    json.dump(clean, file, indent=2)
                os.replace(temp_file, CONFIG_FILE)
            except Exception as exc:
                LOG.warning("Klartext-API-Key konnte nach Migration nicht aus Config entfernt werden: %s", exc)

    def save_config(self):
        try:
            self.config["btn1_action"] = normalize_hotkey(self.entry_btn1.get())
            self.config["btn2_action"] = normalize_hotkey(self.entry_btn2.get())
            self.config["btn3_action"] = normalize_hotkey(self.entry_btn3.get())
            self.api_key = self.entry_api.get().strip()
        except ValueError as exc:
            messagebox.showerror("Ungültige Eingabe", str(exc))
            return

        env_key = os.environ.get("REMOTE_SWITCH_GEMINI_API_KEY", "").strip()
        credential_saved = False
        credential_error = None
        if self.api_key and self.api_key != env_key:
            credential_saved = write_credential_secret(self.api_key)
            if not credential_saved:
                credential_error = "Windows Credential Manager nicht verfügbar; Key wird als Fallback in der lokalen Config gespeichert."
        elif not self.api_key and not env_key:
            delete_credential_secret()

        data = dict(self.config)
        # Kein Klartext-API-Key in der Config-Datei, wenn Credential Manager genutzt wird oder Env-Var aktiv ist.
        data["api_key"] = "" if (credential_saved or env_key or not self.api_key) else self.api_key

        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            temp_file = CONFIG_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
            os.replace(temp_file, CONFIG_FILE)
            if credential_error:
                messagebox.showwarning("Hinweis", f"Konfiguration gespeichert.\n{credential_error}\n{CONFIG_FILE}")
            elif self.api_key and self.api_key != env_key and credential_saved:
                messagebox.showinfo("Info", "Konfiguration gespeichert.\nAPI-Key wurde sicher in der Windows Anmeldeinformationsverwaltung gespeichert.")
            elif env_key:
                messagebox.showinfo("Info", f"Konfiguration gespeichert.\nEs wird die Umgebungsvariable { 'REMOTE_SWITCH_GEMINI_API_KEY' } verwendet.")
            else:
                messagebox.showinfo("Info", f"Konfiguration gespeichert!\n{CONFIG_FILE}")
        except Exception as exc:
            LOG.exception("Konfiguration konnte nicht gespeichert werden")
            messagebox.showerror("Fehler", f"Konfiguration konnte nicht gespeichert werden:\n{exc}")

    def ask_ai(self, btn_num):
        if not HAS_AI:
            messagebox.showerror("Fehler", "Library fehlt. Bitte 'google-genai' installieren.")
            return

        api_key = self.entry_api.get().strip() or os.environ.get("REMOTE_SWITCH_GEMINI_API_KEY", "").strip()
        if not api_key:
            messagebox.showerror("Fehler", "Bitte API Key eingeben oder REMOTE_SWITCH_GEMINI_API_KEY setzen!")
            return

        prompt = simpledialog.askstring(
            "KI Konfiguration", "Was soll die Taste tun? (z.B. 'Lautstärke hoch', 'Screenshot machen')"
        )
        if not prompt:
            return

        try:
            client = genai.Client(api_key=api_key)  # type: ignore
            system_instruction = """
            You are a keyboard configuration assistant.
            Convert the user request into a Python 'keyboard' library hotkey string.
            ONLY return the key string. No markdown, no explanations.
            """.strip()

            response_text = ""
            errors = []
            candidate_models = (
                "gemini-2.5-flash",
                "gemini-flash-latest",
                "gemini-2.0-flash",
                "gemini-2.5-flash-lite",
            )
            for model_name in candidate_models:
                try:
                    response = client.models.generate_content(  # type: ignore
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(  # type: ignore
                            system_instruction=system_instruction,
                            temperature=0.1,
                        ),
                    )
                    if getattr(response, "text", None):
                        response_text = response.text
                        LOG.info("KI-Antwort erhalten von Modell: %s", model_name)
                        break
                    errors.append(f"{model_name}: leere Antwort")
                except Exception as model_exc:
                    msg = str(model_exc).strip() or model_exc.__class__.__name__
                    errors.append(f"{model_name}: {msg}")
                    LOG.info("KI-Modell fehlgeschlagen (%s): %s", model_name, msg)
                    continue

            if not response_text:
                detail = "\n".join(f"- {e}" for e in errors[:4]) if errors else "- Unbekannter Fehler"
                raise RuntimeError(
                    "Kein KI-Modell hat geantwortet.\n\n"
                    "Details:\n"
                    f"{detail}\n\n"
                    "Bitte prüfe API-Key, Internetverbindung und ob die Gemini API in deinem Google-Projekt aktiviert ist."
                )

            key_code = normalize_hotkey(response_text.replace("`", "").replace("'", "").replace('"', ""))
            if not key_code:
                raise ValueError(f"KI hat keinen gültigen Hotkey geliefert: {response_text!r}")

            target_entry = {1: self.entry_btn1, 2: self.entry_btn2, 3: self.entry_btn3}[btn_num]
            target_entry.delete(0, tk.END)
            target_entry.insert(0, key_code)
            self.save_config()
        except Exception as exc:
            LOG.warning("KI-Konfiguration fehlgeschlagen: %s", exc)
            messagebox.showerror("KI Fehler", f"Konnte KI nicht erreichen:\n\n{exc}")

    # --- BLE LOGIK ---
    def force_reconnect(self):
        self.update_status("🔄 Neustart...", "#fbbf24")
        if getattr(self, "loop", None) and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_disconnect(), self.loop)

    async def _async_disconnect(self):
        client = self.client
        self.client = None
        self.connected = False
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    def start_async_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.ble_main())
        except Exception:
            LOG.exception("BLE-Loop beendet sich unerwartet")
        finally:
            try:
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self.loop.close()

    async def ble_main(self):
        if self.startup_delay > 0:
            self.update_status("Warte auf Bluetooth...", "#fbbf24")
            await asyncio.sleep(self.startup_delay)

        for attempt in range(6):
            try:
                await BleakScanner.discover(timeout=0.5)  # type: ignore
                break
            except Exception:
                if attempt < 5:
                    self.update_status(f"Bluetooth nicht bereit... ({attempt + 1}/6)", "#fbbf24")
                    await asyncio.sleep(5)
                else:
                    self.update_status("⚠️ Bluetooth-Problem!", "#ef4444")

        while not self._closing:
            if not self.connected:
                self.update_status("Scanne nach Remote-Switch...", "#fbbf24")
                try:
                    device = await BleakScanner.find_device_by_name(APP_NAME, timeout=5.0)  # type: ignore
                    if device:
                        self.update_status("Gefunden! Verbinde...", "#38bdf8")
                        try:
                            client = BleakClient(device, disconnected_callback=self.on_disconnect)
                            await client.connect()
                            self.client = client
                            self.connected = True
                            self.update_status("✅ Verbunden & Bereit", "#4ade80")

                            await client.start_notify(CHAR_BUTTON_UUID, self.notification_handler)
                            try:
                                await client.start_notify(CHAR_BATTERY_UUID, self.battery_handler)
                            except Exception as exc:
                                LOG.info("Akku-Notify nicht verfügbar: %s", exc)

                            while not self._closing and self.connected and client.is_connected:
                                await asyncio.sleep(1)
                        except Exception as exc:
                            LOG.warning("Verbindungsfehler: %s", exc)
                            self.update_status("Verbindungsfehler. Retry...", "#ef4444")
                            await self._async_disconnect()
                    else:
                        await asyncio.sleep(2)
                except Exception as exc:
                    LOG.warning("Scan-Fehler: %s", exc)
                    await asyncio.sleep(2)
            else:
                await asyncio.sleep(1)

        await self._async_disconnect()

    def on_disconnect(self, _client):
        self.connected = False
        self.client = None
        if not self._closing:
            self.update_status("Verbindung verloren.", "#ef4444")

    def notification_handler(self, _sender, data):
        try:
            value = int.from_bytes(data, byteorder="little")
            action = ""
            if value == 1:
                action = self.config.get("btn1_action", "")
            elif value == 2:
                action = self.config.get("btn2_action", "")
            elif value == 3:
                action = self.config.get("btn3_action", "")

            action = validate_hotkey(action)
            if not action:
                return
            LOG.info("Trigger %s -> %s", value, action)
            keyboard.send(action)
        except Exception as exc:
            LOG.warning("Tastenaktion fehlgeschlagen: %s", exc)

    def battery_handler(self, _sender, data):
        try:
            value = max(0, min(100, int.from_bytes(data, byteorder="little")))
            self.battery_level = value
            self.root.after(0, lambda v=value: self.lbl_battery.config(text=f"🔋 Akku: {v}%"))
        except Exception:
            pass

    def update_status(self, text, color="white"):
        if self._closing:
            return
        try:
            self.root.after(0, lambda t=text, c=color: self.lbl_status.config(text=t, foreground=c))
        except Exception:
            pass

    # --- SYSTEM TRAY FUNKTIONEN ---
    def setup_tray_icon(self):
        try:
            if os.path.exists(ICON_PATH):
                image = Image.open(ICON_PATH)
            else:
                image = Image.new("RGB", (64, 64), color="blue")

            menu = pystray.Menu(
                pystray.MenuItem("Einstellungen öffnen", self.show_window, default=True),
                pystray.MenuItem("Beenden", self.quit_app),
            )
            self.tray_icon = pystray.Icon(APP_NAME, image, APP_NAME, menu, on_activated=self.show_window)
            threading.Thread(target=self.tray_icon.run, daemon=True, name="tray").start()
        except Exception as exc:
            LOG.warning("Tray-Icon Fehler: %s", exc)

    def show_window(self, _icon=None, _item=None):
        self.root.after(0, self._show_window)

    def _show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def hide_window(self):
        try:
            self.root.withdraw()
        except Exception:
            pass

    def quit_app(self, _icon=None, _item=None):
        self._closing = True
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            if getattr(self, "loop", None) and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Remote-Switch Windows bridge")
    parser.add_argument("--autostart", action="store_true", help="Start minimized for Windows autostart/login")
    parser.add_argument("--hidden", action="store_true", help="Start with the main window hidden")
    parser.add_argument("--startup-delay", type=int, default=None, help="Seconds to wait before BLE init")
    return parser.parse_args(argv)


def detect_autostart(args):
    if args.autostart or args.hidden:
        return True
    try:
        uptime_sec = time.time() - os.path.getmtime("C:\\Windows\\System32")
        return uptime_sec < 180
    except Exception:
        return False


if __name__ == "__main__":
    setup_logging()
    args = parse_args()
    is_autostart = detect_autostart(args)
    startup_delay = args.startup_delay if args.startup_delay is not None else (8 if is_autostart else 0)
    start_hidden = args.hidden or is_autostart

    root = tk.Tk()
    app = BluetoothRemoteApp(root, startup_delay=startup_delay, start_hidden=start_hidden)
    root.mainloop()
