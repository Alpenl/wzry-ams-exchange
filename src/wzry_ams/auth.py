"""Single-administrator authentication for the NAS console."""

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from threading import Lock


class AdminAuth:
    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        self.sessions: dict[str, float] = {}
        self.failures: list[float] = []

    @property
    def configured(self) -> bool:
        return self.path.exists()

    def setup(self, password: str) -> None:
        if not 12 <= len(password) <= 256:
            raise ValueError("密码长度需为 12 至 256 位")
        with self.lock:
            salt = secrets.token_hex(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({"salt": salt, "digest": digest}, handle)
                handle.flush()
                os.fsync(handle.fileno())

    def login(self, password: str) -> str:
        with self.lock:
            now = time.time()
            self.failures = [t for t in self.failures if now - t < 60]
            if len(self.failures) >= 10:
                raise ValueError("尝试次数过多，请一分钟后再试")
            if not self.configured or len(password) > 256:
                raise ValueError("请先设置管理员密码")
            stored = json.loads(self.path.read_text())
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), stored["salt"].encode(), 600000
            ).hex()
            if not secrets.compare_digest(digest, stored["digest"]):
                self.failures.append(now)
                raise ValueError("密码不正确")
            self.sessions = {k: expiry for k, expiry in self.sessions.items() if expiry > now}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 8 * 3600
            return token

    def valid(self, token: str) -> bool:
        with self.lock:
            return self.sessions.get(token, 0) > time.time()

    def logout(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)
