import json
import os

APP_DIR = os.environ.get("CALAMO_DATA") or os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CalamoPDF")


def path(name):
    os.makedirs(APP_DIR, exist_ok=True)
    return os.path.join(APP_DIR, name)


def load_json(name, default):
    try:
        with open(path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, data):
    tmp = path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path(name))
