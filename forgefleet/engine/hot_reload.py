"""Hot Reload — detect fleet.toml changes and propagate without restart."""
import hashlib
import os
import threading
import time
import tomllib
from dataclasses import dataclass, field

from .. import config


@dataclass
class ConfigWatcher:
    """Watch fleet.toml for changes and notify listeners with parsed config."""

    config_path: str = ""
    poll_interval: float = 5.0  # Check every 5 seconds
    callbacks: list = field(default_factory=list)
    _last_hash: str = ""
    _running: bool = False
    _thread: threading.Thread = None

    def __post_init__(self):
        if not self.config_path:
            self.config_path = config.CONFIG_PATH
        if self.config_path and os.path.exists(self.config_path):
            self._last_hash = self._file_hash()

    def on_change(self, callback):
        """Register a callback for config changes. callback(new_config: dict)"""
        self.callbacks.append(callback)

    def start(self):
        """Start watching in background."""
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _file_hash(self) -> str:
        """Get SHA256 of the config file."""
        try:
            content = open(self.config_path, "rb").read()
            return hashlib.sha256(content).hexdigest()
        except Exception:
            return ""

    def _load_config(self) -> dict:
        try:
            with open(self.config_path, "rb") as handle:
                return tomllib.load(handle)
        except Exception:
            return {}

    def _notify_callbacks(self, new_config: dict):
        for cb in self.callbacks:
            try:
                cb(new_config)
            except Exception:
                pass

    def _watch_loop(self):
        while self._running:
            time.sleep(self.poll_interval)
            current_hash = self._file_hash()
            if current_hash and current_hash != self._last_hash:
                self._last_hash = current_hash
                self._notify_callbacks(self._load_config())

    def check_now(self) -> bool:
        """Force an immediate check. Returns True if changed."""
        current_hash = self._file_hash()
        if current_hash != self._last_hash:
            self._last_hash = current_hash
            self._notify_callbacks(self._load_config())
            return True
        return False
