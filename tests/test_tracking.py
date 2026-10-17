import asyncio
import time
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import aiohttp
from aiohttp import web

from webapp.models import BackendConfig, CATEGORIES
from webapp.tracking import TrackingApiHelper, TrackingDeviceListener
from iSponsorBlockTV.api_helpers import ApiHelper


def make_listener(config=None):
    config = config or BackendConfig(devices=[{"screen_id": "tv", "name": "TV"}], skip_count_tracking=False)
    helper = TrackingApiHelper(config, None)
    events = []
    listener = TrackingDeviceListener(helper, config, config.devices[0], False, None, events.append)
    listener.lounge_controller._command = AsyncMock(return_value=True)
    listener.lounge_controller.mute = AsyncMock()
    return listener, events


def segment(uuid, category, start=5, end=10):
    return {"UUID": uuid, "category": category, "segment": [start, end], "locked": 1}


async def test_real_segment_fetch_preserves_categories_through_upstream_cache():
    requests = 0

    async def get_segments(request):
        nonlocal requests
        requests += 1
        return web.json_response(
            [
                {
                    "videoID": "video",
                    "segments": [
                        segment("sponsor-uuid", "sponsor", 5, 10),
                        segment("intro-uuid", "intro", 9, 15),
                    ],
                }
            ]
        )

    app = web.Application()
    app.router.add_get("/skipSegments/{hash}", get_segments)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            config = BackendConfig(
                skip_categories=["sponsor", "intro"], sponsorblock_api_url=f"http://127.0.0.1:{port}/"
            )
            helper = TrackingApiHelper(config, session)
            first = await helper.get_segments("video")
            second = await helper.get_segments("video")
            assert requests == 1
            assert first == second
            assert first[0]["categories"] == {"sponsor-uuid": "sponsor", "intro-uuid": "intro"}
            assert first[0]["start"] == 5 and first[0]["end"] == 15
            disabled = TrackingApiHelper(BackendConfig(skip_categories=[]), None)
            assert await disabled.get_segments("no-network") == []
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("category", CATEGORIES)
async def test_each_sponsorblock_category_counts_after_success_without_remote_reporting(category):
    listener, events = make_listener()
    segments, _ = listener.api_helper.process_segments({"segments": [segment("uuid", category)]}, 1)
    # Use upstream scheduling; offset avoids a real five-second wait.
    listener.offset = 10
    await listener.time_to_segment(segments, 1, time.monotonic())
    listener.lounge_controller._command.assert_awaited_once_with("seekTo", {"newTime": 10})
    assert events == [{category: 1}]


async def test_merged_segments_keep_categories_and_count_unique_uuids():
    listener, events = make_listener()
    response = {
        "segments": [
            segment("s1", "sponsor", 5, 10),
            segment("s2", "sponsor", 9, 15),
            segment("intro", "intro", 15.5, 20),
            segment("intro", "intro", 15.5, 20),
            segment("too-short", "outro", 30, 30.5),
        ]
    }
    upstream, _ = ApiHelper.process_segments(deepcopy(response), 1)
    segments, _ = listener.api_helper.process_segments(response, 1)
    assert [{k: v for k, v in s.items() if k != "categories"} for s in segments] == upstream
    listener.offset = 30
    await listener.time_to_segment(segments, 1, time.monotonic())
    assert events == [{"sponsor": 2, "intro": 1}]


@pytest.mark.parametrize("result", [False, RuntimeError("network failed")])
async def test_failed_seek_never_counts(result):
    listener, events = make_listener()
    if isinstance(result, Exception):
        listener.lounge_controller._command.side_effect = result
        with pytest.raises(RuntimeError):
            await listener.skip(0, 10, ["uuid"])
    else:
        listener.lounge_controller._command.return_value = result
        await listener.skip(0, 10, ["uuid"])
    assert events == []


async def test_cancelled_scheduled_skip_and_manual_seek_do_not_count():
    listener, events = make_listener()
    task = asyncio.create_task(listener.skip(60, 10, ["uuid"]))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    listener.lounge_controller._command.assert_not_awaited()
    await listener.lounge_controller.seek_to(30)
    assert events == []


async def test_reporting_failure_does_not_erase_a_successful_skip():
    listener, events = make_listener()
    listener.api_helper.mark_viewed_segments = AsyncMock(side_effect=RuntimeError("report failed"))
    with pytest.raises(RuntimeError):
        await listener.skip(0, 10, ["uuid"])
    assert events == [{"unknown": 1}]


async def test_concurrent_devices_keep_their_segment_categories():
    first, first_events = make_listener()
    second, second_events = make_listener()
    first.offset = second.offset = 10
    sponsor, _ = TrackingApiHelper.process_segments({"segments": [segment("same", "sponsor")]}, 1)
    intro, _ = TrackingApiHelper.process_segments({"segments": [segment("same", "intro")]}, 1)
    await asyncio.gather(
        first.time_to_segment(sponsor, 1, time.monotonic()),
        second.time_to_segment(intro, 1, time.monotonic()),
    )
    assert first_events == [{"sponsor": 1}]
    assert second_events == [{"intro": 1}]


def ad_data(video_id="ad-1", enabled="true"):
    return {
        "adVideoId": video_id,
        "contentVideoId": "",
        "adTitle": "Ad",
        "isBumper": "false",
        "isSkippable": "true",
        "isSkipEnabled": enabled,
        "clickThroughUrl": "",
        "adSystem": "",
        "adNextParams": "",
        "adState": "1",
        "duration": "30",
        "currentTime": "5",
    }


async def drain_events():
    # Commands are scheduled by the real upstream event handler.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_native_ad_duplicate_notifications_next_ad_and_replay():
    listener, events = make_listener()
    lounge = listener.lounge_controller
    await lounge._process_event("onAdStateChange", [ad_data()])
    await lounge._process_event("adPlaying", [ad_data()])
    await drain_events()
    assert events == [{"youtube_ad": 1}]
    # A consecutive ad without returning to content is a separate skip.
    await lounge._process_event("adPlaying", [ad_data("ad-2")])
    await drain_events()
    assert len(events) == 2
    # Returning to content ends the episode; replaying the same ad counts again.
    await lounge._process_event("nowPlaying", [{"state": "1", "videoId": "content"}])
    await lounge._process_event("adPlaying", [ad_data("ad-2")])
    await drain_events()
    assert events == [{"youtube_ad": 1}] * 3


async def test_native_ad_without_id_deduplicates_until_ad_ends():
    listener, events = make_listener()
    lounge = listener.lounge_controller
    await lounge._process_event("onAdStateChange", [ad_data("")])
    await drain_events()
    await lounge._process_event("adPlaying", [ad_data("late-id")])
    await drain_events()
    assert len(events) == 1
    await lounge._process_event("onAdStateChange", [{**ad_data(), "adState": "0"}])
    await lounge._process_event("onAdStateChange", [ad_data("")])
    await drain_events()
    assert len(events) == 2


async def test_native_ad_late_commands_keep_their_own_episode():
    listener, events = make_listener()
    lounge = listener.lounge_controller
    ready = asyncio.Event()

    async def command(*args):
        await ready.wait()
        return True

    lounge._command.side_effect = command
    await lounge._process_event("adPlaying", [ad_data("ad-1")])
    await lounge._process_event("adPlaying", [ad_data("ad-1")])
    await lounge._process_event("adPlaying", [ad_data("ad-2")])
    ready.set()
    await drain_events()
    assert events == [{"youtube_ad": 1}] * 2


async def test_native_ad_failed_command_retries_and_muting_does_not_count():
    listener, events = make_listener()
    lounge = listener.lounge_controller
    await lounge._process_event("adPlaying", [ad_data(enabled="false")])
    await drain_events()
    assert events == []
    lounge._command.assert_not_awaited()
    lounge._command.return_value = False
    assert await lounge.skip_ad() is False
    assert events == []
    lounge._command.side_effect = RuntimeError("network failed")
    with pytest.raises(RuntimeError):
        await lounge.skip_ad()
    assert events == []
    lounge._command.side_effect = None
    lounge._command.return_value = True
    await asyncio.gather(lounge.skip_ad(), lounge.skip_ad())
    assert events == [{"youtube_ad": 1}]
