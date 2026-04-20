# Codex Account Monitor

A lightweight, native macOS desktop application to monitor your API usage and quotas.

## Download

Go to the [Releases page](../../releases/latest) to download the latest `CodexMonitor-macOS.zip`.

If the zip is in your `~/Downloads` folder, you can unzip it, remove the quarantine flag, and open the app with:

```bash
ditto -x -k ~/Downloads/CodexMonitor-macOS.zip ~/Downloads && xattr -dr com.apple.quarantine ~/Downloads/CodexMonitor.app && open ~/Downloads/CodexMonitor.app
```

## Running from Source

1. Clone this repository.
2. Install dependencies with `pip install -r requirements.txt`.
3. Run `python codex_monitor.py`.
