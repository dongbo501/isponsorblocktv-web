# 维护与上游升级

本项目不安排定时同步上游。只有维护者主动更新源代码、完成测试并推送后，才产生新的镜像。`latest` 不会在已运行容器内自动更新。

## 架构

```text
浏览器 → :8080 FastAPI + 静态中文页面
                   ├─ 配置校验、密码/会话、原子写入、日志
                   ├─ 上游 ApiHelper：配对、发现、频道搜索
                   └─ 独立 Python worker
                         └─ 上游 DeviceListener / YtLoungeApi
                               ├─ YouTube TV 连接、广告静音/跳过
                               └─ SponsorBlock 片段获取/跳过
```

容器由 tini 回收子进程；单个 Uvicorn worker 管理全部设备。后台退出后带退避自动重试，最长间隔 60 秒。停止操作有超时与强制终止兜底。配置与会话服务不会因某台电视离线而退出。

上游源码保持原样；`webapp/worker.py` 负责生命周期与只读状态采集，`webapp/backend.py` 负责网络请求适配。上游重试逻辑可能吞掉取消异常，所以 worker 与父进程都限制关闭等待时间。

## 更新固定的上游源码

要求 Python 3.11+、Git 和网络连接。先阅读上游 release notes，再明确选择版本或完整提交：

```bash
python scripts/vendor_upstream.py v2.11.0
```

脚本使用固定官方仓库，解析所选 Git ref，替换 `vendor/upstream`、`vendor/requirements.txt`、许可文件和 `upstream.json`。不要将浮动下载放进 Dockerfile。执行后检查差异：

```bash
git diff --stat
git diff upstream.json vendor/requirements.txt
```

如果上游增删了配置字段或分类，同步修改：

- `webapp/models.py` 的字段、校验与中文类别。
- `webapp/static/app.js` 的设置表单与说明。
- `webapp/worker.py` 的 listener/session 兼容接口与状态读取。
- `webapp/backend.py` 的配对、发现与频道查询调用。
- 对应测试与 README/API 文档。

## 依赖与版本

`vendor/requirements.txt` 记录上游原始依赖；根目录 `requirements.txt` 固定镜像内全部 Python 运行依赖，`requirements-dev.txt` 增加测试和检查工具。

升级依赖时在干净虚拟环境中安装上游依赖及 FastAPI/Uvicorn/Pydantic，检查所有平台兼容性，再固定全部解析后的版本。不要直接把已有环境里的无关软件冻结进运行依赖。

修改 `webapp/__init__.py`、Dockerfile 默认 VERSION、README 和 CHANGELOG，保持面板和镜像发布版本一致。

## 发布前验证

```bash
ruff check webapp tests
pytest -q
node --check webapp/static/app.js
docker build -t isponsorblocktv-web:check .
```

另外验证：空数据卷首次启动、设置/登录、保存空分类、电视配对错误提示、配置导入导出、容器重建后的持久化、后台停止/重启，以及桌面和移动端页面。真实电视验收需检查 DIAL/Chromecast 搜索、播放片段跳过、原生广告静音和跳过、代理环境与白名单。没有真实设备时，明确记录这一限制，不把模拟测试描述为实机通过。

## GitHub / GHCR

工作流 `.github/workflows/publish.yml`：

- PR 仅运行测试，不推送镜像。
- 推送 `main` 后，测试通过才发布 `latest` 与 `sha-…`。
- 推送 `v1.0.0` 这样的标签时发布 `1.0.0` 与 `sha-…`。
- 使用 GitHub 自带 `GITHUB_TOKEN` 的 `packages:write` 权限，无需保存个人 PAT。
- 构建 `linux/amd64` 和 `linux/arm64`，附带 provenance 与 SBOM。

首次发布后确认仓库和 GHCR package 都为公开。公开仓库不必然意味着镜像也公开；应使用**未登录的客户端**确认 manifest 可以读取，并验证两个架构。

推送新版本示例：

```bash
git push origin main
git tag v1.0.0
git push origin v1.0.0
```

版本号应替换为实际的新版本。不要覆盖已发布的版本标签。发布时说明固定的上游版本、用户可见变化、验证范围与迁移注意事项。
