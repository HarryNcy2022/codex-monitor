import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _normalize_version(raw_version: str) -> str:
    version = raw_version.strip()
    if version.startswith("refs/tags/"):
        version = version.rsplit("/", 1)[-1]
    return version.lstrip("vV")


def _looks_like_version(raw_version: str) -> bool:
    normalized = _normalize_version(raw_version)
    return bool(normalized) and normalized[0].isdigit()


def _version_from_env() -> Optional[str]:
    explicit_version = os.environ.get("CODEX_MONITOR_VERSION", "").strip()
    if explicit_version:
        return _normalize_version(explicit_version)

    github_ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    github_ref_type = os.environ.get("GITHUB_REF_TYPE", "").strip()
    if github_ref_name and (
        github_ref_type == "tag" or _looks_like_version(github_ref_name)
    ):
        return _normalize_version(github_ref_name)

    return None


def _version_from_bundle() -> Optional[str]:
    executable_path = Path(sys.executable).resolve()
    info_plist_path: Optional[Path] = None

    for parent in executable_path.parents:
        if parent.name == "MacOS" and parent.parent.name == "Contents":
            info_plist_path = parent.parent / "Info.plist"
            break

    if not info_plist_path or not info_plist_path.exists():
        return None

    try:
        with info_plist_path.open("rb") as plist_file:
            info = plistlib.load(plist_file)
    except Exception:
        return None

    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        value = str(info.get(key, "")).strip()
        if value:
            return _normalize_version(value)

    return None


def _version_from_git() -> Optional[str]:
    repo_root = Path(__file__).resolve().parents[1]
    commands = [
        ["git", "describe", "--tags", "--exact-match", "--match", "v*"],
        ["git", "describe", "--tags", "--match", "v*", "--dirty", "--always"],
    ]

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            continue

        version = _normalize_version(completed.stdout)
        if version:
            return version

    return None


def get_app_version() -> str:
    return (
        _version_from_bundle()
        or _version_from_env()
        or _version_from_git()
        or "0.0.0-dev"
    )


def get_build_version() -> str:
    return _version_from_env() or _version_from_git() or "0.0.0-dev"
