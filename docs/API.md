# 管理 API

API 与网页使用同一个容器、同一个端口，路径前缀 `/api`。登录后访问 `/api/openapi.json` 获取机器可读 schema。所有数据使用 UTF-8 JSON。

## 认证

- `GET /api/auth/status`：公开，返回是否需要首次设置、当前是否登录及当前会话 CSRF token。
- `POST /api/auth/setup`：仅在尚未设置密码时可用，提交 `{"password":"至少10个字符"}`。
- `POST /api/auth/login`：提交 `{"password":"管理密码"}`。
- 设置或登录成功后返回 `{"csrf":"..."}`，同时设置 HttpOnly、SameSite=Strict 的会话 Cookie。
- 后续请求携带 Cookie；修改操作还需请求头 `X-CSRF-Token: ...` 和 `Content-Type: application/json`。
- `POST /api/auth/logout`：退出当前会话，请求体 `{}`。
- `POST /api/auth/password`：提交 `current_password` 和新的 `password`，使旧会话全部失效，返回当前新会话的 CSRF token。

会话最长有效期 7 天，重启管理容器会使所有会话失效。跨站写入被拒绝。登录和修改密码每来源地址 5 分钟最多尝试 10 次。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 无需登录，仅返回管理服务存活和后台状态 |
| GET | `/api/meta` | 面板/上游版本、提交及中文分类说明 |
| GET | `/api/status` | 后台进程状态、运行秒数、退出码、重试次数、各设备实际状态 |
| POST | `/api/service` | `{"action":"start"}` / `stop` / `restart` |
| GET | `/api/config` | 返回 `config`、`revision` 及设备状态索引 `device_keys` |
| PUT | `/api/config` | `{"revision":"当前版本值","config":{完整配置}}`，保存后自动应用 |
| GET | `/api/config/export` | 下载原版兼容配置 JSON |
| GET / PUT | `/api/panel` | 读取或完整保存 `proxy_url`、`debug`、`http_tracing` |
| POST | `/api/devices/pair` | `{"revision":"当前版本值","code":"12位数字","name":"可选名称"}`，配对并保存 |
| POST | `/api/devices/discover` | `{"active":false}` 被动搜索；`true` 可唤起电视 YouTube |
| GET | `/api/channels/search?q=名称` | 返回频道 ID、名称与订阅人数，需要已保存 API 密钥 |
| GET | `/api/logs?after=0&level=ALL` | 返回日志 `entries` 和最新 `cursor`；支持 INFO/WARNING/ERROR/DEBUG |
| GET | `/api/logs/export` | 下载当前磁盘日志文件 |
| GET | `/api/openapi.json` | 完整 OpenAPI schema |

设备编辑/删除、分类、频道白名单、导入等通过读取配置、修改相应数组或字段，再 PUT 完整配置完成。写入前必须提供读取时的 `revision`，防止覆盖其他页面的修改。保存失败不会覆盖原配置。

`GET /api/config`、`GET /api/panel` 和导出接口包含管理所需的凭据，仅供已认证管理员使用。不要把响应贴到公开 issue。

## 配置字段

| 字段 | 类型 / 默认值 | 说明 |
|---|---|---|
| `devices` | 数组 / `[]` | 每项 `screen_id`、`name`、`offset`；offset 单位毫秒，范围 -60000～60000 |
| `skip_categories` | 数组 / `["sponsor"]` | 允许 10 个上游分类，空数组表示关闭片段跳过 |
| `skip_count_tracking` | bool / true | 回报片段 UUID 的跳过次数 |
| `mute_ads` | bool / true | 原生广告静音 |
| `skip_ads` | bool / true | 自动点击可用的跳过广告按钮 |
| `minimum_skip_length` | number / 1 | 0～3600 秒，仅跳过长于此值的片段 |
| `auto_play` | bool / true | 自动播放下一条 |
| `join_name` | string / `iSponsorBlockTV` | 连接电视时显示的客户端名称 |
| `apikey` | string / 空 | YouTube Data API v3 密钥 |
| `channel_whitelist` | 数组 / `[]` | 每项 `id`、`name`；有白名单时 apikey 必须非空 |
| `use_proxy` | bool / false | 是否启用代理 |
| `sponsorblock_api_url` | string | 默认 `https://sponsor.ajay.app/api/`，自动补尾部斜线 |

请求最多 1 MB；最多 100 台设备、500 个白名单频道。未知配置字段被拒绝；`config_file`、`data_dir` 不能通过 API 指定文件路径。

面板专用配置单独保存在 `panel.json`：`proxy_url` 默认空字符串；`debug`、`http_tracing` 默认 false。后台启用状态通过 `/api/service` 保存。

## Python 调用示例

以下示例登录已有管理员账户，读取配置并修改最短跳过长度，不会打印密码或设备凭据。

```python
import getpass
import httpx

with httpx.Client(base_url="http://127.0.0.1:8080", timeout=30) as client:
    login = client.post("/api/auth/login", json={"password": getpass.getpass("管理密码：")})
    login.raise_for_status()
    client.headers["X-CSRF-Token"] = login.json()["csrf"]

    current = client.get("/api/config")
    current.raise_for_status()
    payload = current.json()
    payload["config"]["minimum_skip_length"] = 2
    saved = client.put("/api/config", json={
        "revision": payload["revision"],
        "config": payload["config"],
    })
    saved.raise_for_status()
    print("配置已保存")
```

## 状态与错误

服务状态可能为 `waiting`（无设备）、`running`、`starting`、`restarting`、`stopped`。`running` 表示进程存在；在线情况请检查 `devices` 内各项的 `connected`。状态索引是 Screen ID 的 SHA-256 前 16 位，可以从配置接口的 `device_keys` 获取。

| HTTP 状态码 | 含义 |
|---|---|
| 400 | 配对失败、缺少白名单 API 密钥等业务错误 |
| 401 | 未登录、会话过期或密码错误 |
| 403 | CSRF token 错误或跨站请求 |
| 409 | 配置版本冲突、重复配对或发现任务进行中 |
| 413 | 请求体超过大小限制 |
| 415 | 修改请求不是 JSON |
| 422 | 字段验证失败，返回 `errors`，不回显敏感输入 |
| 429 | 登录/密码操作超过频率限制 |
| 502 | YouTube、Google API 或网络请求失败 |

日志内存缓存最多 1,000 条，进程重启后 ID 从头开始；轮询客户端发现服务器 `cursor` 小于本地 cursor 时，应清空本地缓存并重新获取。磁盘日志每份约 2 MB，保留当前文件及两份轮转文件。HTTP 诊断仅包含方法、URL 路径和状态，避免记录含令牌的响应正文。
