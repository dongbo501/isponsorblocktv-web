import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, backend
from .models import (
    CATEGORIES,
    ConfigUpdate,
    Discovery,
    Login,
    Pair,
    PanelSettings,
    Password,
    PasswordChange,
    ServiceAction,
)
from .security import COOKIE, Auth
from .storage import Store
from .supervisor import Supervisor, device_key

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir=None, supervisor_factory=Supervisor):
    @asynccontextmanager
    async def lifespan(app):
        directory = Path(data_dir or os.environ.get("DATA_DIR", "/data"))
        app.state.store = Store(directory)
        app.state.auth = Auth(directory)
        app.state.lock = asyncio.Lock()
        app.state.discovering = False
        app.state.supervisor = supervisor_factory(app.state.store)
        await app.state.supervisor.start()
        yield
        await app.state.supervisor.close()

    app = FastAPI(
        title="iSponsorBlockTV 中文管理 API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response includes submitted values (possibly secrets).
        messages = {
            "missing": "此项不能为空",
            "extra_forbidden": "不支持此配置字段",
            "string_pattern_mismatch": "格式不正确",
            "string_too_short": "内容太短",
            "string_too_long": "内容太长",
            "greater_than_equal": "数值低于允许范围",
            "less_than_equal": "数值超出允许范围",
            "float_parsing": "请输入有效数字",
            "int_parsing": "请输入有效整数",
            "finite_number": "请输入有限数值",
        }
        errors = [
            {
                "field": ".".join(map(str, e["loc"][1:])),
                "message": e["msg"].removeprefix("Value error, ")
                if e["type"] == "value_error"
                else messages.get(e["type"], "输入格式不正确"),
            }
            for e in exc.errors()
        ]
        return JSONResponse({"detail": "输入格式有误，请检查字段", "errors": errors}, status_code=422)

    @app.middleware("http")
    async def safety(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and urlsplit(origin).netloc != request.headers.get("host")
            ):
                return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "请求必须使用 application/json"}, status_code=415)
            # Enforce the actual streamed size, including chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1_048_576:
                    return JSONResponse({"detail": "请求不能超过 1 MB"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
        return response

    def auth(request: Request):
        return request.app.state.auth.require(request)

    def check_revision(revision):
        if revision != app.state.store.revision:
            raise HTTPException(409, "配置已在另一页面更新，请刷新后重试")

    def config_response():
        store = app.state.store
        return {
            "config": store.config.model_dump(),
            "revision": store.revision,
            "device_keys": {d.screen_id: device_key(d.screen_id) for d in store.config.devices},
        }

    async def save(config):
        app.state.store.save(config)
        await app.state.supervisor.apply()
        app.state.supervisor.log("配置已保存并应用")
        return config_response()

    def issue_session(request, response):
        return {"csrf": app.state.auth.create_session(response, secure=request.url.scheme == "https")}

    @app.get("/healthz", include_in_schema=False)
    async def health():
        return {"ok": True, "service": app.state.supervisor.status()["state"]}

    @app.get("/api/auth/status", tags=["登录"])
    async def auth_status(request: Request):
        session = app.state.auth.session(request)
        return {
            "setup_required": app.state.auth.credential is None,
            "authenticated": bool(session),
            "csrf": session["csrf"] if session else None,
        }

    @app.post("/api/auth/setup", tags=["登录"])
    async def setup(body: Password, request: Request, response: Response):
        async with app.state.lock:
            if app.state.auth.credential:
                raise HTTPException(409, "管理员密码已经设置，请登录")
            await asyncio.to_thread(app.state.auth.set_password, body.password)
        return issue_session(request, response)

    @app.post("/api/auth/login", tags=["登录"])
    async def login(body: Login, request: Request, response: Response):
        app.state.auth.rate_limit(request.client.host)
        async with app.state.lock:
            if not await asyncio.to_thread(app.state.auth.verify, body.password):
                raise HTTPException(401, "密码不正确")
            return issue_session(request, response)

    api = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    @api.post("/auth/logout", tags=["登录"])
    async def logout(request: Request, response: Response):
        app.state.auth.sessions.pop(request.cookies.get(COOKIE), None)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.post("/auth/password", tags=["登录"])
    async def change_password(body: PasswordChange, request: Request, response: Response):
        app.state.auth.rate_limit(request.client.host)
        async with app.state.lock:
            if not await asyncio.to_thread(app.state.auth.verify, body.current_password):
                raise HTTPException(400, "当前密码不正确")
            await asyncio.to_thread(app.state.auth.set_password, body.password)
            return issue_session(request, response)

    @api.get("/meta", tags=["系统"])
    async def meta():
        return {
            "version": __version__,
            "upstream": json.loads((ROOT / "upstream.json").read_text()),
            "categories": [
                {"id": key, "name": value[0], "description": value[1]} for key, value in CATEGORIES.items()
            ],
        }

    @api.get("/status", tags=["服务"])
    async def status():
        return app.state.supervisor.status()

    @api.get("/statistics", tags=["统计"])
    async def statistics():
        store = app.state.store
        return store.statistics.snapshot(store.config)

    @api.post("/service", tags=["服务"])
    async def service(body: ServiceAction):
        async with app.state.lock:
            app.state.store.set_enabled(body.action != "stop")
            await app.state.supervisor.apply()
            app.state.supervisor.log(
                {"stop": "后台服务已暂停", "start": "已开启后台服务", "restart": "已重新启动后台服务"}[
                    body.action
                ]
            )
        return app.state.supervisor.status()

    @api.get("/config", tags=["配置"])
    async def get_config():
        return config_response()

    @api.put("/config", tags=["配置"])
    async def update_config(body: ConfigUpdate):
        async with app.state.lock:
            check_revision(body.revision)
            return await save(body.config)

    @api.get("/config/export", tags=["配置"])
    async def export():
        return JSONResponse(
            app.state.store.config.model_dump(),
            headers={"Content-Disposition": 'attachment; filename="isponsorblocktv-config.json"'},
        )

    @api.get("/panel", tags=["系统"])
    async def panel():
        return app.state.store.panel.model_dump()

    @api.put("/panel", tags=["系统"])
    async def update_panel(body: PanelSettings):
        async with app.state.lock:
            app.state.store.save_panel(body)
            await app.state.supervisor.apply()
        return body.model_dump()

    @api.post("/devices/pair", tags=["设备"])
    async def pair_device(body: Pair):
        check_revision(body.revision)
        store = app.state.store
        try:
            result = await backend.pair(store.config, store.panel, body.code)
        except (aiohttp.ClientError, TimeoutError):
            raise HTTPException(502, "无法连接 YouTube，请检查服务器网络或代理设置") from None
        if not result:
            raise HTTPException(400, "配对失败，请重新打开电视上的 YouTube 获取新配对码")
        async with app.state.lock:
            check_revision(body.revision)
            config = store.config.model_dump()
            device = {
                "screen_id": result["screen_id"],
                "name": body.name or result.get("name") or "YouTube on TV",
                "offset": 0,
            }
            if any(d["screen_id"] == device["screen_id"] for d in config["devices"]):
                raise HTTPException(409, "这台电视已经添加，请在设备卡片中编辑")
            config["devices"].append(device)
            return await save(type(store.config).model_validate(config))

    @api.post("/devices/discover", tags=["设备"])
    async def discover(body: Discovery):
        if app.state.discovering:
            raise HTTPException(409, "正在搜索设备，请稍后再试")
        app.state.discovering = True
        try:
            store = app.state.store
            return {"devices": await backend.discover(store.config, store.panel, body.active)}
        except (aiohttp.ClientError, OSError, TimeoutError):
            raise HTTPException(502, "局域网搜索失败，请检查网络模式，或使用电视配对码") from None
        finally:
            app.state.discovering = False

    @api.get("/channels/search", tags=["白名单"])
    async def channels(q: str = Query(min_length=1, max_length=100)):
        store = app.state.store
        if not store.config.apikey:
            raise HTTPException(400, "请先在高级设置中保存 YouTube Data API 密钥")
        try:
            results = await backend.search(store.config, store.panel, q)
            return {"channels": [{"id": c[0], "name": c[1], "subscribers": c[2]} for c in results]}
        except (aiohttp.ClientError, TimeoutError, KeyError, IndexError):
            raise HTTPException(502, "频道查询失败，请检查 API 密钥、配额和网络连接") from None

    @api.get("/logs", tags=["服务"])
    async def logs(after: int = Query(default=0, ge=0), level: str = "ALL"):
        entries = [
            entry
            for entry in app.state.supervisor.logs
            if entry["id"] > after and (level == "ALL" or entry["level"] == level)
        ]
        return {"entries": entries, "cursor": app.state.supervisor.sequence}

    @api.get("/logs/export", tags=["服务"])
    async def export_logs():
        path = app.state.store.directory / "service.log"
        if not path.exists():
            return Response("", media_type="text/plain")
        return FileResponse(path, media_type="text/plain", filename="isponsorblocktv.log")

    @api.get("/openapi.json", tags=["系统"])
    async def openapi():
        return app.openapi()

    app.include_router(api)
    app.mount("/static", StaticFiles(directory=ROOT / "webapp" / "static"), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(ROOT / "webapp" / "static" / "index.html")

    return app


app = create_app()
