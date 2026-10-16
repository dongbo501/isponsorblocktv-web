import asyncio
import json
import sys

import aiohttp
from aiohttp import web

from webapp import backend
from webapp.models import BackendConfig, PanelSettings
from webapp.storage import Store
from webapp.supervisor import Supervisor, redact


async def test_real_upstream_pairing_adapter(monkeypatch):
    from iSponsorBlockTV import api_helpers

    async def pair(request):
        assert (await request.post())["pairing_code"] == "123456789012"
        return web.json_response({"screen": {"screenId": "new-screen-id", "name": "测试电视"}})

    app = web.Application()
    app.router.add_post("/pairing/get_screen", pair)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(api_helpers, "api_base", f"http://127.0.0.1:{port}")
    try:
        result = await backend.pair(BackendConfig(), PanelSettings(), "123456789012")
        assert result["screen_id"] == "new-screen-id"
        assert result["name"] == "测试电视"
    finally:
        await runner.cleanup()


async def test_upstream_categories_offset_and_segment_processing(tmp_path):
    from iSponsorBlockTV.api_helpers import ApiHelper
    from iSponsorBlockTV.helpers import Config

    store = Store(tmp_path)
    store.save(
        BackendConfig(devices=[{"name": "TV", "screen_id": "valid-id", "offset": 350}], skip_categories=[])
    )
    config = Config(str(tmp_path))
    config.validate()
    assert config.devices[0].offset == 0.35
    async with aiohttp.ClientSession() as session:
        helper = ApiHelper(config, session)
        assert await helper.get_segments("no-network-call") == []
        segments, _ = helper.process_segments(
            {
                "segments": [
                    {"segment": [5, 10], "UUID": "a", "locked": 1},
                    {"segment": [9, 15], "UUID": "b", "locked": 1},
                ]
            },
            1,
        )
        assert segments[0]["start"] == 5
        assert segments[0]["end"] == 15


def test_redaction():
    config = BackendConfig(apikey="my-google-secret", devices=[{"name": "TV", "screen_id": "device-secret"}])
    panel = PanelSettings(proxy_url="http://username:password@proxy.example:7890")
    message = 'device-secret my-google-secret https://host/?key=my-google-secret&x=1 {"loungeToken": "dynamic-secret"} http://username:password@proxy.example:7890'
    result = redact(message, config, panel)
    for secret in ["device-secret", "my-google-secret", "dynamic-secret", "username:password"]:
        assert secret not in result


async def test_supervisor_restarts_crashed_worker_and_stops_cleanly(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save(BackendConfig(devices=[{"name": "TV", "screen_id": "fake"}]))
    original = asyncio.create_subprocess_exec
    calls = 0

    async def spawn(*args, **kwargs):
        nonlocal calls
        calls += 1
        # Crash once, then act as a healthy worker publishing real stdout.
        code = (
            "raise SystemExit(3)"
            if calls == 1
            else 'import time;print(\'@STATUS {"tv":{"connected":true}}\',flush=True);time.sleep(60)'
        )
        return await original(sys.executable, "-u", "-c", code, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    service = Supervisor(store)
    await service.start()
    try:
        async with asyncio.timeout(10):
            while not service.devices:
                await asyncio.sleep(0.1)
        assert calls == 2
        assert service.status()["state"] == "running"
        assert service.status()["last_exit"] == 3
        assert service.devices["tv"]["connected"] is True
        store.set_enabled(False)
        await service.apply()
        assert service.status()["state"] == "stopped"
        assert service.process is None
        assert "后台服务退出" in (tmp_path / "service.log").read_text()
    finally:
        await service.close()


def test_corrupted_config_fails_without_data_loss(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{bad json")
    try:
        Store(tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("Corrupted config should stop startup")
    assert path.read_text() == "{bad json"


def test_atomic_write_keeps_complete_json(tmp_path):
    store = Store(tmp_path)
    store.save(BackendConfig(join_name="中文电视遥控"))
    assert json.loads(store.config_path.read_text())["join_name"] == "中文电视遥控"
    assert not list(tmp_path.glob(".write-*"))


def test_proxy_keeps_local_network_direct(monkeypatch):
    monkeypatch.setattr(backend, "proxy_bypass", lambda host: False)
    router = backend.RoutedSession(
        None, BackendConfig(use_proxy=True), PanelSettings(proxy_url="http://proxy.example:7890")
    )
    assert router.proxy_for("https://www.youtube.com/api/lounge/") == "http://proxy.example:7890"
    for url in [
        "http://192.168.1.2:8008/apps/YouTube",
        "http://127.0.0.1:8000",
        "http://[::1]/",
        "http://tv.local/",
    ]:
        assert router.proxy_for(url) is None
    direct = backend.RoutedSession(
        None, BackendConfig(use_proxy=False), PanelSettings(proxy_url="http://proxy.example:7890")
    )
    assert direct.proxy_for("https://www.youtube.com/") is None
