import os

from .version import get_app_version


APP_TITLE = "Codex Account Monitor"
APP_NAME = "CodexMonitor"
APP_VERSION = get_app_version()
WINDOW_GEOMETRY = "980x460"
WINDOW_MIN_SIZE = (540, 320)
UPDATE_CHECK_INTERVAL_SECONDS = 21600

GITHUB_REPOSITORY = "koodev24/codex-monitor"
RELEASE_ASSET_NAME = "CodexMonitor-macOS.zip"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
DEFAULT_INSTALL_DIR = os.path.expanduser("~/Applications")
DEFAULT_APP_INSTALL_PATH = os.path.join(DEFAULT_INSTALL_DIR, f"{APP_NAME}.app")
HTTP_USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# Automatically resolves to /Users/<your_username>/.codex/auth.json
AUTH_FILE_PATH = os.path.expanduser("~/.codex/auth.json")
AUTH_DIR = os.path.dirname(AUTH_FILE_PATH)

# Lightweight local storage equivalent
LOCAL_STORAGE_FILE = os.path.expanduser("~/.codex_usage_store.json")
LOCAL_STORAGE_META_FILE = os.path.expanduser("~/.codex_usage_store.meta.json")

# API URL
USAGE_API_URL = "https://chatgpt.com/backend-api/wham/usage"

AUTO_FETCH_INTERVALS = {
    "1 Hr": 3600,
    "3 Hrs": 10800,
    "12 Hrs": 43200,
    "24 Hrs": 86400,
}

AUTO_FETCH_OPTIONS = ["None", *AUTO_FETCH_INTERVALS.keys()]
