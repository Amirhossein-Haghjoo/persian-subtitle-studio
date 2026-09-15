"""Local persistence for API keys, model priority, and user preferences."""
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

DEFAULT_CONFIG = {
    "api_keys": [],
    "models_priority": ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"],
    "whisper_model": "small",
    "device": "cpu",
    # Which deliverables to produce.
    "outputs": ["en_srt", "fa_srt"],
    # How transliterated technical terms are marked in the Persian subtitle.
    "highlight_style": "both",      # color | bold | both | none
    "highlight_color": "#FFC857",
    # Persian text-to-speech settings.
    "tts_engine": "auto",           # auto | edge | gtts
    "tts_voice": "fa-IR-DilaraNeural",
    "tts_rate": "+0%",
    "tts_mode": "timed",            # timed | sequential
}


def load_config() -> dict:
    """Loads settings, filling in any missing keys with defaults."""
    if not CONFIG_FILE.exists():
        return _copy_defaults()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _copy_defaults()
        for key, val in DEFAULT_CONFIG.items():
            data.setdefault(key, val.copy() if isinstance(val, list) else val)
        return data
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # A corrupt or unreadable config should never stop the app from starting.
        return _copy_defaults()


def save_config(config_data: dict) -> bool:
    """Writes settings atomically so a crash mid-write can't corrupt the file."""
    try:
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        tmp.replace(CONFIG_FILE)
        return True
    except OSError as e:
        print(f"خطا در ذخیره تنظیمات: {e}")
        return False


def _copy_defaults() -> dict:
    return {k: (v.copy() if isinstance(v, list) else v) for k, v in DEFAULT_CONFIG.items()}
