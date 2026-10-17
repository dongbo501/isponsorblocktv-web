import json

from webapp.app import create_app
from webapp.models import BackendConfig
from fastapi.testclient import TestClient
from conftest import FakeSupervisor


def test_auth_protects_all_secrets_and_actions(client):
    assert client.get("/healthz").status_code == 200
    for path in [
        "/config",
        "/panel",
        "/status",
        "/statistics",
        "/logs",
        "/config/export",
        "/logs/export",
        "/meta",
        "/openapi.json",
    ]:
        assert client.get("/api" + path).status_code == 401
    assert client.post("/api/service", json={"action": "start"}).status_code == 401
    assert client.get("/").headers["x-frame-options"] == "DENY"


def test_setup_login_logout_password_and_session_invalidation(admin):
    assert admin.post("/api/auth/setup", json={"password": "second-password"}).status_code == 409
    old_cookie = admin.cookies.get("isbtv_session")
    response = admin.post(
        "/api/auth/password", json={"current_password": "test-password-123", "password": "new-password-123"}
    )
    assert response.status_code == 200
    new_csrf = response.json()["csrf"]
    assert old_cookie not in admin.app.state.auth.sessions
    admin.headers["X-CSRF-Token"] = new_csrf
    assert admin.post("/api/auth/logout", json={}).status_code == 200
    assert admin.get("/api/config").status_code == 401
    assert admin.post("/api/auth/login", json={"password": "test-password-123"}).status_code == 401
    response = admin.post("/api/auth/login", json={"password": "new-password-123"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


def test_csrf_origin_and_json_required(admin):
    assert (
        admin.post("/api/service", json={"action": "start"}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        admin.post(
            "/api/service", json={"action": "start"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert admin.post("/api/service", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert (
        admin.post(
            "/api/service", content="x" * 1_048_577, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )


def test_login_rate_limited(client):
    for _ in range(10):
        assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 429


def test_validation_never_echoes_password(client):
    response = client.post("/api/auth/setup", json={"password": "secret"})
    assert response.status_code == 422
    assert "secret" not in response.text


def test_config_round_trip_conflicts_and_empty_categories(admin):
    original = admin.get("/api/config").json()
    config = original["config"]
    config.update(
        devices=[{"name": "客厅", "screen_id": "new-valid-device-id", "offset": 250}], skip_categories=[]
    )
    response = admin.put("/api/config", json={"revision": original["revision"], "config": config})
    assert response.status_code == 200
    assert response.json()["config"]["skip_categories"] == []
    assert response.json()["device_keys"]["new-valid-device-id"]
    assert admin.app.state.supervisor.applies == 1
    assert (
        admin.put("/api/config", json={"revision": original["revision"], "config": config}).status_code == 409
    )
    exported = admin.get("/api/config/export")
    assert exported.json() == response.json()["config"]
    assert "attachment" in exported.headers["content-disposition"]
    stored = json.loads(admin.app.state.store.config_path.read_text())
    assert stored["devices"][0]["offset"] == 250
    assert admin.app.state.store.config_path.stat().st_mode & 0o777 == 0o600


def test_invalid_config_does_not_replace_good_config(admin):
    original = admin.get("/api/config").json()
    bad = original["config"].copy()
    for update in [
        {"skip_categories": ["unknown"]},
        {"minimum_skip_length": -1},
        {"sponsorblock_api_url": "file:///etc/passwd"},
        {"devices": [{"name": "x", "screen_id": "", "offset": 0}]},
        {"channel_whitelist": [{"name": "x", "id": "UC" + "a" * 22}]},
        {"devices": [{"name": "x", "screen_id": "same"}] * 2},
        {"atvs": []},
        {"config_file": "/etc/passwd"},
    ]:
        response = admin.put("/api/config", json={"revision": original["revision"], "config": bad | update})
        assert response.status_code == 422, response.text
        assert admin.get("/api/config").json() == original


def test_pairing_normalizes_code_and_preserves_credentials(admin, monkeypatch):
    from webapp import backend

    async def pair(config, panel, code):
        assert code == "123456789012"
        return {"screen_id": "opaque-new-format-id", "name": "YouTube TV", "lounge_token": "not-stored"}

    monkeypatch.setattr(backend, "pair", pair)
    revision = admin.get("/api/config").json()["revision"]
    result = admin.post(
        "/api/devices/pair", json={"code": "123-456 789 012", "name": "卧室", "revision": revision}
    )
    assert result.status_code == 200
    device = result.json()["config"]["devices"][0]
    assert device == {"name": "卧室", "screen_id": "opaque-new-format-id", "offset": 0}
    assert "not-stored" not in admin.app.state.store.config_path.read_text()
    duplicate = admin.post(
        "/api/devices/pair", json={"code": "123456789012", "revision": result.json()["revision"]}
    )
    assert duplicate.status_code == 409


def test_pair_failure_network_error_and_search(admin, monkeypatch):
    from webapp import backend

    async def failed(*args):
        return None

    monkeypatch.setattr(backend, "pair", failed)
    revision = admin.get("/api/config").json()["revision"]
    body = {"code": "123456789012", "revision": revision}
    assert admin.post("/api/devices/pair", json=body).status_code == 400

    async def offline(*args):
        raise TimeoutError()

    monkeypatch.setattr(backend, "pair", offline)
    assert admin.post("/api/devices/pair", json=body).status_code == 502
    assert admin.get("/api/channels/search?q=test").status_code == 400


def test_settings_and_paused_service_survive_restart(admin, tmp_path):
    panel = {"debug": True, "http_tracing": True, "proxy_url": "http://proxy.internal:7890"}
    assert admin.put("/api/panel", json=panel).json() == panel
    assert admin.post("/api/service", json={"action": "stop"}).json()["state"] == "stopped"
    with TestClient(create_app(tmp_path, FakeSupervisor)) as restarted:
        assert restarted.get("/api/auth/status").json()["setup_required"] is False
        login = restarted.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        assert restarted.get("/api/status").json()["state"] == "stopped"
        assert restarted.get("/api/panel").json() == panel


def test_openapi_and_categories_match_upstream(admin):
    from iSponsorBlockTV.constants import skip_categories

    expected = {category[1] for category in skip_categories}
    assert {category["id"] for category in admin.get("/api/meta").json()["categories"]} == expected
    assert "/api/devices/pair" in admin.get("/api/openapi.json").json()["paths"]
    from iSponsorBlockTV.helpers import Config

    config = Config(str(admin.app.state.store.directory))
    defaults = BackendConfig().model_dump()
    assert set(defaults) == set(vars(config)) - {"data_dir", "config_file"}
