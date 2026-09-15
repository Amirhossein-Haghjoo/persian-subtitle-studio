import json
from pathlib import Path

# مسیر ذخیره فایل تنظیمات در کنار پروژه
CONFIG_FILE = Path(__file__).parent.parent / "config.json"

DEFAULT_CONFIG = {
    "api_keys": [],
    "models_priority": ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"],
    "whisper_model": "small",
    "device": "cpu"
}


def load_config() -> dict:
    """بارگذاری تنظیمات از فایل config.json"""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # اطمینان از وجود تمام کلیدهای پیش‌فرض
            for key, val in DEFAULT_CONFIG.items():
                data.setdefault(key, val)
            return data
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(config_data: dict) -> bool:
    """ذخیره تنظیمات در فایل config.json"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"خطا در ذخیره تنظیمات: {e}")
        return False