"""Simple local config storage for the app (API key, last used model)."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PersianSubtitleApp"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "gemini_api_key": "",
    "whisper_model": "medium",
    "device": "cpu",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = DEFAULTS.copy()
            merged.update(data)
            return merged
        except Exception:
            return DEFAULTS.copy()
    return DEFAULTS.copy()


def save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    current = load_config()
    current.update(data)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
