# Codex Account Monitor

A lightweight, native macOS desktop application to monitor your API usage and quotas.

## Download

Go to the [Releases page](../../releases/latest) to download the latest `CodexMonitor-macOS.zip`.

If the zip is in your `~/Downloads` folder, you can run this command:

```bash
ditto -x -k ~/Downloads/CodexMonitor-macOS.zip ~/Downloads && xattr -dr com.apple.quarantine ~/Downloads/CodexMonitor.app && open ~/Downloads/CodexMonitor.app
```

It will:

- unzip `CodexMonitor-macOS.zip` into `~/Downloads`
- remove the macOS quarantine flag from `CodexMonitor.app`
- open the app immediately

If you prefer to do it step by step, run:

```bash
ditto -x -k ~/Downloads/CodexMonitor-macOS.zip ~/Downloads
xattr -dr com.apple.quarantine ~/Downloads/CodexMonitor.app
open ~/Downloads/CodexMonitor.app
```

Each command will:

- `ditto -x -k ...`: unzip the downloaded archive
- `xattr -dr ...`: allow macOS to open the app without quarantine restrictions
- `open ...`: launch the app

## Running from Source

1. Clone this repository.
2. Run this command:

```bash
pip install -r requirements.txt
```

It will:

- install the Python packages required by the app

3. Run this command:

```bash
python codex_monitor.py
```

It will:

- start the app from source on your machine
