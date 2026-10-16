import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict

from fastapi import HTTPException

from .storage import atomic_json

COOKIE = "isbtv_session"
SESSION_TTL = 7 * 24 * 3600


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()


class Auth:
    def __init__(self, directory):
        self.path = directory / "auth.json"
        self.credential = json.loads(self.path.read_text()) if self.path.exists() else None
        self.sessions = {}
        self.attempts = OrderedDict()

    def set_password(self, password):
        salt = secrets.token_bytes(32)
        credential = {"salt": base64.b64encode(salt).decode(), "hash": password_hash(password, salt)}
        atomic_json(self.path, credential)
        self.credential = credential
        self.sessions.clear()

    def verify(self, password):
        if not self.credential:
            return False
        salt = base64.b64decode(self.credential["salt"])
        return hmac.compare_digest(password_hash(password, salt), self.credential["hash"])

    def rate_limit(self, ip):
        now = time.monotonic()
        recent = [t for t in self.attempts.get(ip, []) if now - t < 300]
        if len(recent) >= 10:
            raise HTTPException(429, "尝试次数过多，请 5 分钟后重试")
        self.attempts[ip] = recent + [now]
        self.attempts.move_to_end(ip)
        while len(self.attempts) > 2048:
            self.attempts.popitem(last=False)

    def create_session(self, response, secure=False):
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
        if len(self.sessions) >= 100:
            self.sessions.pop(next(iter(self.sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[token] = {"csrf": csrf, "expires": now + SESSION_TTL}
        response.set_cookie(
            COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=secure, samesite="strict", path="/"
        )
        return csrf

    def session(self, request):
        session = self.sessions.get(request.cookies.get(COOKIE, ""))
        if session and session["expires"] > time.time():
            return session
        return None

    def require(self, request):
        session = self.session(request)
        if not session:
            raise HTTPException(401, "请先登录管理面板")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(token, session["csrf"]):
                raise HTTPException(403, "请求验证失败，请刷新页面后重试")
        return session
