import os
import json
import logging

log = logging.getLogger(__name__)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "settings.json")

def _init_data_dir():
    dir_path = os.path.dirname(SETTINGS_PATH)
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

def load_settings(guild_id: int = None) -> dict:
    _init_data_dir()
    path = os.path.join(os.path.dirname(SETTINGS_PATH), f"settings_{guild_id}.json") if guild_id else SETTINGS_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load settings for {guild_id or 'global'}: {e}")
        return {}

def save_settings(data: dict, guild_id: int = None):
    _init_data_dir()
    path = os.path.join(os.path.dirname(SETTINGS_PATH), f"settings_{guild_id}.json") if guild_id else SETTINGS_PATH
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        log.error(f"Failed to save settings for {guild_id or 'global'}: {e}")

def get_presets(guild_id: int = None) -> dict:
    filename = f"presets_{guild_id}.json" if guild_id else "presets_global.json"
    path = os.path.join(os.path.dirname(SETTINGS_PATH), filename)
    if not os.path.exists(path): return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def save_preset(guild_id: int, name: str, prompt: str):
    presets = get_presets(guild_id)
    presets[name] = prompt
    filename = f"presets_{guild_id}.json" if guild_id else "presets_global.json"
    path = os.path.join(os.path.dirname(SETTINGS_PATH), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=4)

def delete_preset(guild_id: int, name: str):
    presets = get_presets(guild_id)
    if name in presets:
        del presets[name]
        filename = f"presets_{guild_id}.json" if guild_id else "presets_global.json"
        path = os.path.join(os.path.dirname(SETTINGS_PATH), filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=4)

def rename_preset(guild_id: int, old_name: str, new_name: str):
    presets = get_presets(guild_id)
    if old_name in presets:
        prompt = presets.pop(old_name)
        presets[new_name] = prompt
        filename = f"presets_{guild_id}.json" if guild_id else "presets_global.json"
        path = os.path.join(os.path.dirname(SETTINGS_PATH), filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=4)
