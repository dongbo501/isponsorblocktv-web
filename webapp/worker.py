"""Run upstream listeners in an isolated process; expose only read-only telemetry."""

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

import aiohttp

from . import backend  # noqa: F401 (register the pinned upstream module path)
from .models import PanelSettings
from .supervisor import device_key
from .tracking import TrackingApiHelper, TrackingDeviceListener
from iSponsorBlockTV.helpers import Config


def report_skip(key, counts):
    print("@SKIP " + json.dumps({"device_key": key, "counts": counts}), flush=True)


async def run():
    directory = Path(os.environ["DATA_DIR"])
    config = Config(str(directory))
    config.validate()
    panel_path = directory / "panel.json"
    panel = (
        PanelSettings.model_validate_json(panel_path.read_text()) if panel_path.exists() else PanelSettings()
    )
    logging.basicConfig(
        level=logging.DEBUG if panel.debug else logging.INFO, format="%(levelname)s %(name)s · %(message)s"
    )
    # HTTP diagnostics intentionally omit response bodies, cookies and URL queries.
    traces = []
    if panel.http_tracing:
        trace = aiohttp.TraceConfig()

        async def request_end(session, context, params):
            logging.info(
                "HTTP %s %s://%s%s → %s",
                params.method,
                params.url.scheme,
                params.url.host,
                params.url.path,
                params.response.status,
            )

        trace.on_request_end.append(request_end)
        traces.append(trace)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    async with aiohttp.ClientSession(
        trust_env=False, trace_configs=traces, connector=aiohttp.TCPConnector(ttl_dns_cache=300)
    ) as raw_session:
        session = backend.RoutedSession(raw_session, config, panel)
        helper = TrackingApiHelper(config, session)
        devices, tasks = [], []
        for device in config.devices:
            key = device_key(device.screen_id)
            listener = TrackingDeviceListener(
                helper, config, device, panel.debug, session,
                record_skip=lambda counts, key=key: report_skip(key, counts),
            )
            await listener.initialize_web_session()
            devices.append((device, listener))
            tasks += [asyncio.create_task(listener.loop()), asyncio.create_task(listener.refresh_auth_loop())]
        while not stop.is_set():
            telemetry = {}
            for device, listener in devices:
                lounge = listener.lounge_controller
                state = lounge._playback_state
                telemetry[device_key(device.screen_id)] = {
                    "connected": lounge.connected(),
                    "linked": lounge.linked(),
                    "video_id": state.videoId,
                    "playing": state.state.value == 1,
                    "position": state.currentTime,
                }
            print("@STATUS " + json.dumps(telemetry), flush=True)
            for task in tasks:
                if task.done():
                    task.result()
                    raise RuntimeError("Upstream listener unexpectedly stopped")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
        for _, listener in devices:
            listener.cancelled = True
        cleanup = [asyncio.create_task(listener.cancel()) for _, listener in devices]
        await asyncio.wait(cleanup, timeout=2)
        for task in tasks + cleanup:
            task.cancel()
        await asyncio.wait(tasks + cleanup, timeout=1)


if __name__ == "__main__":
    # Upstream catches BaseException in retry loops. Bound shutdown even if one of
    # those loops suppresses cancellation; the parent also enforces a hard timeout.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    finally:
        loop.close()
