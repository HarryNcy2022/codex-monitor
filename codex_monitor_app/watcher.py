import os
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class AuthFileHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[], None], target_file: str):
        self.callback = callback
        self.target_file = os.path.abspath(target_file)

    def _matches(self, path: Optional[str]) -> bool:
        return bool(path) and os.path.abspath(path) == self.target_file

    def on_modified(self, event) -> None:
        if not event.is_directory and self._matches(event.src_path):
            self.callback()

    def on_created(self, event) -> None:
        if not event.is_directory and self._matches(event.src_path):
            self.callback()

    def on_deleted(self, event) -> None:
        if not event.is_directory and self._matches(event.src_path):
            self.callback()

    def on_moved(self, event) -> None:
        if not event.is_directory and (
            self._matches(event.src_path) or self._matches(event.dest_path)
        ):
            self.callback()


class AuthFileWatcher:
    def __init__(self, watch_dir: str, target_file: str, callback: Callable[[], None]):
        self.watch_dir = watch_dir
        self.target_file = target_file
        self.callback = callback
        self.observer: Optional[Observer] = None

    def start(self) -> None:
        os.makedirs(self.watch_dir, exist_ok=True)
        event_handler = AuthFileHandler(self.callback, self.target_file)
        self.observer = Observer()
        self.observer.schedule(event_handler, self.watch_dir, recursive=False)
        self.observer.start()

    def stop(self) -> None:
        if self.observer is None:
            return

        self.observer.stop()
        self.observer.join(timeout=2)
        self.observer = None
