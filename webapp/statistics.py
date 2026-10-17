"""Persistent local counters, independent of SponsorBlock's view reporting."""

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, field_validator

from .models import CATEGORIES, Model

STAT_CATEGORIES = {
    "youtube_ad": ("YouTube 原生广告", "自动点击「跳过广告」"),
    **CATEGORIES,
    "unknown": ("其他片段", "未提供或无法识别类别的片段"),
}
DeviceKey = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$")]
Count = Annotated[int, Field(strict=True, ge=0)]


def device_key(screen_id):
    return hashlib.sha256(screen_id.encode()).hexdigest()[:16]


class Counts(Model):
    counts: dict[str, Count] = Field(default_factory=dict, max_length=len(STAT_CATEGORIES))

    @field_validator("counts")
    @classmethod
    def known_categories(cls, value):
        if value.keys() - STAT_CATEGORIES.keys():
            raise ValueError("不支持的统计分类")
        return value


class SkipEvent(Counts):
    device_key: DeviceKey


class DeviceStatistics(Counts):
    name: str = Field(min_length=1, max_length=100)


class Statistics(Model):
    version: Literal[1] = 1
    since: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    devices: dict[DeviceKey, DeviceStatistics] = Field(default_factory=dict)

    def record(self, event, name):
        device = self.devices.setdefault(event.device_key, DeviceStatistics(name=name))
        device.name = name
        for category, count in event.counts.items():
            device.counts[category] = device.counts.get(category, 0) + count

    def snapshot(self, config):
        current = {device_key(device.screen_id): device.name for device in config.devices}
        totals = dict.fromkeys(STAT_CATEGORIES, 0)
        devices = []
        # Current devices appear first, including devices with no skips yet.
        for key in dict.fromkeys([*current, *self.devices]):
            saved = self.devices.get(key)
            counts = dict.fromkeys(STAT_CATEGORIES, 0)
            if saved:
                counts.update(saved.counts)
            for category, count in counts.items():
                totals[category] += count
            devices.append(
                {
                    "key": key,
                    "name": current[key] if key in current else saved.name,
                    "configured": key in current,
                    "counts": counts,
                    "total": sum(counts.values()),
                }
            )
        return {
            "since": self.since.isoformat(),
            "categories": [{"id": key, "name": value[0]} for key, value in STAT_CATEGORIES.items()],
            "global": {"counts": totals, "total": sum(totals.values())},
            "devices": devices,
        }
