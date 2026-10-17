import asyncio
import json
from types import SimpleNamespace

import pytest

from webapp.models import BackendConfig, CATEGORIES, Device
from webapp.statistics import STAT_CATEGORIES, SkipEvent, device_key
from webapp.storage import Store
from webapp.supervisor import Supervisor
from webapp.worker import report_skip


def test_statistics_start_at_zero_and_include_every_category(admin):
    data = admin.get("/api/statistics").json()
    assert data["devices"] == []
    assert data["global"] == {"counts": dict.fromkeys(STAT_CATEGORIES, 0), "total": 0}
    store = admin.app.state.store
    store.save(BackendConfig(devices=[{"screen_id": "new-tv", "name": "新电视"}]))
    data = admin.get("/api/statistics").json()
    assert data["devices"][0]["total"] == 0
    assert set(data["devices"][0]["counts"]) >= {"youtube_ad", *CATEGORIES}
    assert data["devices"][0]["key"] == device_key("new-tv")
    assert data["devices"][0]["configured"] is True


def test_totals_persist_across_restart_rename_remove_and_readd(admin):
    store = admin.app.state.store
    config = BackendConfig(
        devices=[
            {"screen_id": "living-room", "name": "客厅"},
            {"screen_id": "bedroom", "name": "卧室"},
        ],
        skip_count_tracking=False,
    )
    store.save(config)
    for screen, counts in [
        ("living-room", {"youtube_ad": 3, **dict.fromkeys(CATEGORIES, 2)}),
        ("bedroom", {"youtube_ad": 4, "sponsor": 5}),
    ]:
        store.record_skip(SkipEvent(device_key=device_key(screen), counts=counts), screen)
    data = admin.get("/api/statistics").json()
    assert data["global"]["total"] == 32
    assert data["global"]["counts"]["sponsor"] == 7
    assert data["global"]["counts"]["youtube_ad"] == 7
    for category in STAT_CATEGORIES:
        assert data["global"]["counts"][category] == sum(d["counts"][category] for d in data["devices"])
    for device in data["devices"]:
        assert device["total"] == sum(device["counts"].values())
    assert "living-room" not in json.dumps(data)
    assert "bedroom" not in store.statistics_path.read_text()

    # Configuration changes and service pause never reset local counts.
    config.devices[0].name = "客厅电视（新名称）"
    store.save(config)
    assert admin.post("/api/service", json={"action": "stop"}).status_code == 200
    config.devices.pop(0)
    store.save(config)
    reloaded = Store(store.directory)
    snapshot = reloaded.statistics.snapshot(reloaded.config)
    assert snapshot["since"] == data["since"]
    assert snapshot["global"] == data["global"]
    history = next(d for d in snapshot["devices"] if not d["configured"])
    assert history["name"] == "客厅电视（新名称）"
    assert history["total"] == 23

    config.devices.append(Device(screen_id="living-room", name="再次添加"))
    reloaded.save(BackendConfig.model_validate(config.model_dump()))
    snapshot = reloaded.statistics.snapshot(reloaded.config)
    assert len(snapshot["devices"]) == 2
    assert all(d["configured"] for d in snapshot["devices"])
    assert snapshot["global"] == data["global"]
    # Existing upstream-compatible exports do not include statistics.
    assert "statistics" not in admin.get("/api/config/export").json()


async def test_worker_events_are_validated_and_saved_without_parsing_logs(tmp_path, capsys):
    store = Store(tmp_path)
    store.save(BackendConfig(devices=[{"screen_id": "screen-secret", "name": "电视"}]))
    key = device_key("screen-secret")
    report_skip(key, {"sponsor": 2, "youtube_ad": 1})
    output = capsys.readouterr().out
    assert "screen-secret" not in output
    stream = asyncio.StreamReader()
    stream.feed_data(output.encode())
    for event in [
        {"device_key": key, "counts": {"sponsor": -1}},
        {"device_key": key, "counts": {"sponsor": True}},
        {"device_key": key, "counts": {"sponsor": 1.5}},
        {"device_key": key, "counts": {"unexpected": 1}},
        {"device_key": device_key("unconfigured"), "counts": {"sponsor": 10}},
    ]:
        stream.feed_data(("@SKIP " + json.dumps(event) + "\n").encode())
    stream.feed_data(b"@SKIP invalid-json\nINFO Skipping segment: seeking to 123\n")
    stream.feed_data(b'@STATUS {"tv":{"connected":true}}\n')
    stream.feed_eof()
    service = Supervisor(store)
    try:
        await service.read_output(SimpleNamespace(stdout=stream), store.config, store.panel)
        assert store.statistics.snapshot(store.config)["global"]["total"] == 3
        assert Store(tmp_path).statistics.snapshot(store.config)["global"]["total"] == 3
        assert service.devices["tv"]["connected"] is True
        assert sum(log["level"] == "WARNING" for log in service.logs) == 6
    finally:
        await service.close()


def test_damaged_statistics_fail_without_erasing_history(tmp_path):
    store = Store(tmp_path)
    store.statistics_path.write_text('{"devices": "damaged"}')
    with pytest.raises(ValueError):
        Store(tmp_path)
    assert store.statistics_path.read_text() == '{"devices": "damaged"}'


def test_failed_statistics_write_does_not_advance_in_memory(tmp_path, monkeypatch):
    from webapp import storage

    store = Store(tmp_path)
    before = store.statistics_path.read_bytes()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "atomic_json", fail)
    with pytest.raises(OSError):
        store.record_skip(SkipEvent(device_key=device_key("tv"), counts={"sponsor": 1}), "TV")
    assert store.statistics.snapshot(store.config)["global"]["total"] == 0
    assert store.statistics_path.read_bytes() == before
