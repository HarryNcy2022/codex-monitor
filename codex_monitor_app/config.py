import os


APP_TITLE = "Codex Account Monitor"
WINDOW_GEOMETRY = "980x460"
WINDOW_MIN_SIZE = (620, 320)

# Automatically resolves to /Users/<your_username>/.codex/auth.json
AUTH_FILE_PATH = os.path.expanduser("~/.codex/auth.json")
AUTH_DIR = os.path.dirname(AUTH_FILE_PATH)

# Lightweight local storage equivalent
LOCAL_STORAGE_FILE = os.path.expanduser("~/.codex_usage_store.json")

# API URL
USAGE_API_URL = "https://chatgpt.com/backend-api/wham/usage"

AUTO_FETCH_INTERVALS = {
    "1 Hr": 3600,
    "3 Hrs": 10800,
    "12 Hrs": 43200,
    "24 Hrs": 86400,
}

AUTO_FETCH_OPTIONS = ["None", *AUTO_FETCH_INTERVALS.keys()]
