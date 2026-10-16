import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


def device_key(screen_id):
    return hashlib.sha256(screen_id.encode()).hexdigest()[:16]


def redact(text, config, panel):
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [已隐藏]", text)
    for secret in [config.apikey, panel.proxy_url, *(d.screen_id for d in config.devices)]:
        if secret:
            text = text.replace(secret, "[已隐藏]")
    text = re.sub(
        r'(?i)((?:lounge.?token|loungeIdToken|screenId|screen_id|pairing_code|pairingCode|authorization|key|token)[\s"\x27:=]+)[^\s,\]}&]+',
        r"\1[已隐藏]",
        text,
    )
    text = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "http://[已隐藏]@", text)
    return text[:12000]


class Supervisor:
    def __init__(self, store):
        self.store = store
        self.process = None
        self.monitor_task = None
        self.reader_task = None
        self.lock = asyncio.Lock()
        self.closed = False
        self.started = None
        self.next_retry = 0
        self.crashes = 0
        self.last_exit = None
        self.devices = {}
        self.logs = deque(maxlen=1000)
        self.sequence = 0
        self.file_handler = RotatingFileHandler(
            store.directory / "service.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
        )
        self.file_handler.setFormatter(logging.Formatter("%(message)s"))
        os.chmod(store.directory / "service.log", 0o600)

    def log(self, message, level="INFO"):
        self.sequence += 1
        entry = {
            "id": self.sequence,
            "time": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": redact(message, self.store.config, self.store.panel),
        }
        self.logs.append(entry)
        line = json.dumps(entry, ensure_ascii=False)
        self.file_handler.emit(logging.LogRecord("worker", logging.INFO, "", 0, line, (), None))
        print(line, flush=True)

    def status(self):
        running = self.process is not None and self.process.returncode is None
        if not self.store.enabled:
            state = "stopped"
        elif not self.store.config.devices:
            state = "waiting"
        elif running:
            state = "running"
        else:
            state = "restarting" if self.crashes else "starting"
        return {
            "state": state,
            "enabled": self.store.enabled,
            "pid": self.process.pid if running else None,
            "uptime": int(time.time() - self.started) if running and self.started else 0,
            "last_exit": self.last_exit,
            "restarts": self.crashes,
            "devices": self.devices if running else {},
        }

    async def start(self):
        self.monitor_task = asyncio.create_task(self.monitor())
        async with self.lock:
            await self.spawn()

    async def spawn(self):
        if self.closed or not self.store.enabled or not self.store.config.devices:
            return
        env = os.environ.copy()
        env["DATA_DIR"] = str(self.store.directory.resolve())
        if self.store.panel.proxy_url and self.store.config.use_proxy:
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = self.store.panel.proxy_url
            env["http_proxy"] = env["https_proxy"] = self.store.panel.proxy_url
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "webapp.worker",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1_048_576,
        )
        self.started = time.time()
        self.devices = {}
        self.reader_task = asyncio.create_task(
            self.read_output(
                self.process, self.store.config.model_copy(deep=True), self.store.panel.model_copy(deep=True)
            )
        )
        self.log("后台服务已启动，正在连接已配置的电视")

    async def read_output(self, process, config, panel):
        while line := await process.stdout.readline():
            text = line.decode("utf-8", errors="replace").strip()
            if text.startswith("@STATUS "):
                try:
                    self.devices = json.loads(text[8:])
                except (ValueError, TypeError):
                    pass
            elif text:
                level = (
                    "ERROR"
                    if "ERROR" in text or "Traceback" in text
                    else "WARNING"
                    if "WARNING" in text
                    else "DEBUG"
                    if "DEBUG" in text
                    else "INFO"
                )
                self.log(redact(text, config, panel), level)

    async def stop_process(self):
        process = self.process
        if process and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 7)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self.reader_task:
            await asyncio.gather(self.reader_task, return_exceptions=True)
        self.process = None
        self.reader_task = None
        self.devices = {}

    async def apply(self):
        async with self.lock:
            await self.stop_process()
            self.crashes = 0
            self.last_exit = None
            self.next_retry = 0
            await self.spawn()

    async def monitor(self):
        while not self.closed:
            await asyncio.sleep(1)
            async with self.lock:
                if self.process and self.process.returncode is not None:
                    self.last_exit = self.process.returncode
                    if self.reader_task:
                        await asyncio.gather(self.reader_task, return_exceptions=True)
                    self.process = None
                    self.crashes += 1
                    delay = min(60, 2 ** min(self.crashes, 6))
                    self.next_retry = time.monotonic() + delay
                    self.log(f"后台服务退出（代码 {self.last_exit}），{delay} 秒后重试", "ERROR")
                if not self.process and time.monotonic() >= self.next_retry:
                    try:
                        await self.spawn()
                    except OSError as error:
                        self.next_retry = time.monotonic() + 30
                        self.log(f"无法启动后台服务：{type(error).__name__}", "ERROR")

    async def close(self):
        self.closed = True
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        async with self.lock:
            await self.stop_process()
        self.file_handler.close()
