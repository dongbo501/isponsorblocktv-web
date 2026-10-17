"""Instrument the pinned upstream without changing its scheduling or commands."""

from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass

from . import backend  # noqa: F401 (register the pinned upstream module path)
from .models import CATEGORIES
from iSponsorBlockTV.api_helpers import ApiHelper
from iSponsorBlockTV.main import DeviceListener
from iSponsorBlockTV.ytlounge import YtLoungeApi

# Upstream spawns tasks for seek/skip commands. Context variables keep the metadata
# with that command even when another playback notification arrives in the meantime.
segment_categories = ContextVar("segment_categories", default=None)
skip_counts = ContextVar("skip_counts", default=None)
ad_episode = ContextVar("ad_episode", default=None)


class TrackingApiHelper(ApiHelper):
    @staticmethod
    def process_segments(response, minimum_skip_length):
        categories = (
            {segment["UUID"]: segment.get("category", "unknown") for segment in response.get("segments", [])}
            if isinstance(response, dict)
            else {}
        )
        segments, ignore_ttl = ApiHelper.process_segments(response, minimum_skip_length)
        for segment in segments:
            segment["categories"] = {uuid: categories.get(uuid, "unknown") for uuid in segment["UUID"]}
        return segments, ignore_ttl


@dataclass
class AdEpisode:
    video_id: str = ""
    content_id: str = ""
    counted: bool = False


class TrackingLoungeApi(YtLoungeApi):
    def __init__(self, *args, record_skip, **kwargs):
        super().__init__(*args, **kwargs)
        self.record_skip = record_skip
        self.ad = None

    async def _process_event(self, event_type, args):
        data = args[0] if args and isinstance(args[0], dict) else {}
        if (event_type in {"onStateChange", "nowPlaying"} and data.get("state") == "1") or (
            event_type == "onAdStateChange"
            and data.get("adState") == "0"
            and data.get("currentTime", "0") != "0"
        ):
            self.ad = None
        elif event_type in {"adPlaying", "onAdStateChange"}:
            video_id = data.get("adVideoId") or ""
            content_id = data.get("contentVideoId") or ""
            if (
                self.ad is None
                or (video_id and self.ad.video_id and video_id != self.ad.video_id)
                or (content_id and self.ad.content_id and content_id != self.ad.content_id)
            ):
                self.ad = AdEpisode()
            self.ad.video_id = video_id or self.ad.video_id
            self.ad.content_id = content_id or self.ad.content_id
        token = ad_episode.set(self.ad)
        try:
            return await super()._process_event(event_type, args)
        finally:
            ad_episode.reset(token)

    async def skip_ad(self):
        episode = ad_episode.get()
        if episode is None:
            if self.ad is None:
                self.ad = AdEpisode()
            episode = self.ad
        result = await super().skip_ad()
        if result and not episode.counted:
            self.record_skip({"youtube_ad": 1})
            episode.counted = True
        return result

    async def seek_to(self, position):
        result = await super().seek_to(position)
        counts = skip_counts.get()
        if result and counts:
            self.record_skip(counts)
        return result


class TrackingDeviceListener(DeviceListener):
    def __init__(self, api_helper, config, device, debug, web_session, record_skip):
        super().__init__(api_helper, config, device, debug, web_session)
        self.lounge_controller = TrackingLoungeApi(
            device.screen_id, config, api_helper, self.logger, record_skip=record_skip
        )

    async def time_to_segment(self, segments, position, time_start):
        categories = {}
        for segment in segments:
            categories.update(segment.get("categories", {}))
        token = segment_categories.set(categories)
        try:
            return await super().time_to_segment(segments, position, time_start)
        finally:
            segment_categories.reset(token)

    async def skip(self, time_to, position, uuids):
        categories = segment_categories.get() or {}
        counts = Counter()
        for uuid in set(uuids):
            category = categories.get(uuid)
            counts[category if category in CATEGORIES else "unknown"] += 1
        token = skip_counts.set(dict(counts))
        try:
            return await super().skip(time_to, position, uuids)
        finally:
            skip_counts.reset(token)
