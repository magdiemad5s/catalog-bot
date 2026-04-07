import os
import json
import logging

log = logging.getLogger(__name__)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "settings.json")

def _init_data_dir():
    dir_path = os.path.dirname(SETTINGS_PATH)
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

def load_settings() -> dict:
    _init_data_dir()
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load settings.json: {e}")
        return {}

def save_settings(data: dict):
    _init_data_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        log.error(f"Failed to save settings.json: {e}")

def get_presets() -> dict:
    settings = load_settings()
    return settings.get("presets", {})

def save_preset(name: str, prompt: str):
    settings = load_settings()
    if "presets" not in settings:
        settings["presets"] = {}
    settings["presets"][name] = prompt
    save_settings(settings)
