import hashlib
import json
import os
import tempfile
from pathlib import Path

from .models import BackendConfig, PanelSettings
from .statistics import Statistics, device_key


def atomic_json(path: Path, value):
    """Replace, never truncate. Secrets on disk are owner-readable only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.config_path = directory / "config.json"
        if not self.config_path.exists():
            atomic_json(self.config_path, BackendConfig().model_dump())
        # Fail visibly on a damaged configuration; do not silently overwrite it.
        self.config = BackendConfig.model_validate_json(self.config_path.read_text())
        self.panel_path = directory / "panel.json"
        self.panel = (
            PanelSettings.model_validate_json(self.panel_path.read_text())
            if self.panel_path.exists()
            else PanelSettings()
        )
        self.service_path = directory / "service.json"
        self.enabled = (
            json.loads(self.service_path.read_text())["enabled"] if self.service_path.exists() else True
        )
        self.statistics_path = directory / "statistics.json"
        self.statistics = (
            Statistics.model_validate_json(self.statistics_path.read_text())
            if self.statistics_path.exists()
            else Statistics()
        )
        if not self.statistics_path.exists():
            atomic_json(self.statistics_path, self.statistics.model_dump(mode="json"))

    @property
    def revision(self):
        return hashlib.sha256(self.config.model_dump_json().encode()).hexdigest()[:20]

    def save(self, config):
        # Keep the latest display names even after a device is later removed.
        statistics = self.statistics.model_copy(deep=True)
        for device in config.devices:
            saved = statistics.devices.get(device_key(device.screen_id))
            if saved:
                saved.name = device.name
        if statistics != self.statistics:
            self.save_statistics(statistics)
        atomic_json(self.config_path, config.model_dump())
        self.config = config

    def save_statistics(self, statistics):
        atomic_json(self.statistics_path, statistics.model_dump(mode="json"))
        self.statistics = statistics

    def record_skip(self, event, name):
        name = next(
            (device.name for device in self.config.devices if device_key(device.screen_id) == event.device_key),
            name,
        )
        statistics = self.statistics.model_copy(deep=True)
        statistics.record(event, name)
        self.save_statistics(statistics)

    def save_panel(self, panel):
        atomic_json(self.panel_path, panel.model_dump())
        self.panel = panel

    def set_enabled(self, enabled):
        atomic_json(self.service_path, {"enabled": enabled})
        self.enabled = enabled
