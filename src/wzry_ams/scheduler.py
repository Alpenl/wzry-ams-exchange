"""Persistent daily redemption for a single NAS service."""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, time
from pathlib import Path
from threading import Event, Lock
from zoneinfo import ZoneInfo

from .credentials import CredentialError, Credentials, CredentialStore
from .exchange import ExchangeClient, RedemptionOutcome

logger = logging.getLogger(__name__)


class DailyScheduler:
    def __init__(
        self,
        cookie_file: Path,
        state_file: Path,
        *,
        at: str = "09:17",
        timezone: str = "Asia/Shanghai",
        redeem: Callable[[Credentials, str], RedemptionOutcome] | None = None,
    ):
        self.at = time.fromisoformat(at)
        if self.at.tzinfo is not None:
            raise ValueError("Daily time must not contain a UTC offset")
        self.zone = ZoneInfo(timezone)
        self.store = CredentialStore(cookie_file)
        self.state_file = state_file
        self.redeem = redeem or (
            lambda credentials, reward: ExchangeClient(credentials).redeem(reward)
        )
        self.lock = Lock()
        self.error: str | None = None
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, enabled INTEGER, at TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS results (day TEXT, account TEXT, reward TEXT, token TEXT, attempts INTEGER, updated REAL, satisfied INTEGER, retryable INTEGER, result TEXT, PRIMARY KEY(day, account, reward))"
            )
        self.enabled = True
        with self.connect() as db:
            setting = db.execute("SELECT enabled, at FROM settings WHERE id=1").fetchone()
            if setting:
                self.enabled = bool(setting["enabled"])
                self.at = time.fromisoformat(setting["at"])

    def configure(self, enabled: bool, at: str) -> None:
        parsed = time.fromisoformat(at)
        if parsed.tzinfo or parsed.second or parsed.microsecond:
            raise ValueError("执行时间需为 HH:MM")
        with self.lock:
            with self.connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES (1, ?, ?)",
                    (enabled, parsed.isoformat()),
                )
            self.enabled, self.at = enabled, parsed

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.state_file, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def tick(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(self.zone)).astimezone(self.zone)
        if now.time() < self.at:
            return
        with self.lock:
            if not self.enabled:
                return
            try:
                credentials = self.store.load(required=True).require_exchange_ready()
            except CredentialError:
                self.error = "凭据未配置或不完整"
                return
            self.error = None
            account = hashlib.sha256(credentials.values["openid"].encode()).hexdigest()
            token = hashlib.sha256(
                json.dumps(credentials.to_dict(), sort_keys=True).encode()
            ).hexdigest()
            day = now.date().isoformat()
            for reward in ("3", "4"):
                with self.connect() as db:
                    row = db.execute(
                        "SELECT * FROM results WHERE day=? AND account=? AND reward=?",
                        (day, account, reward),
                    ).fetchone()
                    if row and row["satisfied"]:
                        continue
                    attempts = row["attempts"] if row and row["token"] == token else 0
                    if attempts and (
                        not row["retryable"]
                        or attempts >= 4
                        or now.timestamp() - row["updated"] < 300
                    ):
                        continue
                    # Reserve the attempt before the request; interrupted requests retry after five minutes.
                    db.execute(
                        "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)",
                        (
                            day,
                            account,
                            reward,
                            token,
                            attempts + 1,
                            now.timestamp(),
                            json.dumps({"kind": "running"}),
                        ),
                    )
                outcome = self.redeem(credentials, reward)
                with self.connect() as db:
                    db.execute(
                        "UPDATE results SET satisfied=?, retryable=?, result=? WHERE day=? AND account=? AND reward=?",
                        (
                            outcome.satisfied,
                            outcome.retryable,
                            json.dumps(outcome.to_dict(), ensure_ascii=False),
                            day,
                            account,
                            reward,
                        ),
                    )
                logger.info("Daily %s reward=%s outcome=%s", day, reward, outcome.kind.value)

    def status(self) -> dict:
        with self.connect() as db:
            rows = db.execute(
                "SELECT day, reward, attempts, result FROM results ORDER BY day DESC, reward LIMIT 30"
            ).fetchall()
        return {
            "available": True,
            "enabled": self.enabled,
            "time": self.at.isoformat(timespec="minutes"),
            "timezone": str(self.zone),
            "error": self.error,
            "runs": [
                {
                    "day": r["day"],
                    "reward": r["reward"],
                    "attempts": r["attempts"],
                    "result": json.loads(r["result"]),
                }
                for r in rows
            ],
        }

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                self.error = "调度异常，请查看容器日志"
                logger.exception("Daily scheduler failed")
            stop.wait(30)
