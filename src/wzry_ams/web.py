"""FastAPI console and API for standalone reward redemption."""

from __future__ import annotations

import base64
import os
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import AdminAuth
from .credentials import CredentialError, Credentials, CredentialStore
from .exchange import ICON_BASE, REWARD_MAP, ExchangeClient, OutcomeKind, RedemptionOutcome
from .scheduler import DailyScheduler

DEFAULT_COOKIE_FILE = Path(os.environ.get("WZRY_COOKIE_FILE", Path.cwd() / "cookies.txt"))
DEFAULT_LOG_FILE = Path(os.environ.get("WZRY_LOG_FILE", Path.cwd() / "exchange.log"))
STATIC = Path(__file__).parent / "static"


class RedemptionPort(Protocol):
    def redeem(self, reward_id: str) -> RedemptionOutcome: ...


ClientFactory = Callable[[Credentials], RedemptionPort]


def _log(log_file: Path, outcome: RedemptionOutcome) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "OK" if outcome.satisfied else "FAIL"
    name = outcome.reward.name if outcome.reward else outcome.reward_id
    message = outcome.message.replace("\n", " ")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"[{ts}] {status} {name} - {message}\n")
    except OSError:
        pass


def _read_logs(log_file: Path, limit: int = 30) -> list[dict[str, str]]:
    from collections import deque

    logs = []
    if log_file.exists():
        with log_file.open(encoding="utf-8") as handle:
            for line in deque(handle, maxlen=limit):
                match = re.match(r"\[(.+?)\] (\w+) (.+?) - (.+)", line.strip())
                if match:
                    logs.append(
                        dict(zip(("time", "status", "name", "msg"), match.groups(), strict=True))
                    )
    return logs


class CookiePayload(BaseModel):
    raw: str


class ExchangePayload(BaseModel):
    reward: str


class PasswordPayload(BaseModel):
    password: str


class SchedulePayload(BaseModel):
    enabled: bool
    time: str


def _credential_status(credentials: Credentials | None) -> dict[str, object]:
    if credentials is None:
        return dict(
            has_cookie=False,
            exchange_ready=False,
            openid="",
            acctype="?",
            exp_voucher="?",
            area="?",
            partition="?",
        )
    identity = credentials.activity_identity
    openid = credentials.values.get("openid", "")
    return dict(
        has_cookie=credentials.is_login_ready,
        exchange_ready=credentials.is_exchange_ready,
        openid=f"{openid[:8]}..." if openid else "",
        acctype=credentials.values.get("acctype", "?"),
        exp_voucher=credentials.experience_voucher,
        area=identity.area if identity else "?",
        partition=identity.partition if identity else "?",
    )


def _outcome_status(kind: OutcomeKind) -> int:
    return {
        OutcomeKind.INVALID_REWARD: 400,
        OutcomeKind.AUTHENTICATION_FAILED: 401,
        OutcomeKind.REJECTED: 409,
        OutcomeKind.PROTOCOL_FAILURE: 502,
        OutcomeKind.TRANSIENT_FAILURE: 503,
    }.get(kind, 200)


def create_app(
    *,
    cookie_file: str | os.PathLike[str] = DEFAULT_COOKIE_FILE,
    log_file: str | os.PathLike[str] = DEFAULT_LOG_FILE,
    client_factory: ClientFactory = ExchangeClient,
    scheduler: DailyScheduler | None = None,
    password: str | None = None,
    auth: AdminAuth | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        stop = Event()
        thread = Thread(target=scheduler.run, args=(stop,), daemon=True) if scheduler else None
        if thread:
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread:
                import asyncio

                await asyncio.to_thread(thread.join, 65)

    application = FastAPI(
        title="王者荣耀体验服兑换",
        version="2.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if password == "":
        raise ValueError("Web password must not be empty")

    @application.middleware("http")
    async def authenticate(request: Request, call_next):
        path = request.url.path
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            from urllib.parse import urlsplit

            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"msg": "跨站请求被拒绝"}, status_code=403)
        public = path in {
            "/",
            "/healthz",
            "/api/auth/status",
            "/api/auth/setup",
            "/api/auth/login",
        } or path.startswith("/static/")
        if auth and not public and not auth.valid(request.cookies.get("wzry_session", "")):
            return JSONResponse({"msg": "请先登录", "auth_required": True}, status_code=401)
        if password and path != "/healthz":
            expected = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
            if not secrets.compare_digest(
                request.headers.get("authorization", "").encode(), expected.encode()
            ):
                return JSONResponse(
                    {"msg": "Authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="wzry", charset="UTF-8"'},
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.mount("/static", StaticFiles(directory=STATIC), name="static")
    store, log_path = CredentialStore(cookie_file), Path(log_file)

    @application.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @application.get("/healthz")
    def health():
        return {"ok": True}

    @application.get("/api/auth/status")
    def auth_status(request: Request):
        return {
            "enabled": auth is not None,
            "configured": auth.configured if auth else True,
            "authenticated": auth.valid(request.cookies.get("wzry_session", "")) if auth else True,
        }

    @application.post("/api/auth/setup")
    def setup(data: PasswordPayload, request: Request):
        if not auth:
            return JSONResponse({"msg": "认证未启用"}, status_code=409)
        try:
            auth.setup(data.password)
        except FileExistsError:
            return JSONResponse({"msg": "管理员已设置"}, status_code=409)
        except ValueError as error:
            return JSONResponse({"msg": str(error)}, status_code=400)
        return session_response(auth.login(data.password), request)

    def session_response(token: str, request: Request):
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "wzry_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=8 * 3600,
        )
        return response

    @application.post("/api/auth/login")
    def login(data: PasswordPayload, request: Request):
        if not auth:
            return JSONResponse({"msg": "认证未启用"}, status_code=409)
        try:
            return session_response(auth.login(data.password), request)
        except ValueError as error:
            return JSONResponse({"msg": str(error)}, status_code=401)

    @application.post("/api/auth/logout")
    def logout(request: Request):
        if auth:
            auth.logout(request.cookies.get("wzry_session", ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie("wzry_session")
        return response

    @application.get("/api/rewards")
    def rewards():
        return {"rewards": REWARD_MAP, "icon_base": "https:" + ICON_BASE}

    @application.get("/api/schedule")
    def schedule_status():
        return (
            scheduler.status() if scheduler else {"available": False, "enabled": False, "runs": []}
        )

    @application.put("/api/schedule")
    def configure_schedule(data: SchedulePayload):
        if not scheduler:
            return JSONResponse({"msg": "调度器未启用"}, status_code=409)
        try:
            scheduler.configure(data.enabled, data.time)
        except ValueError as error:
            return JSONResponse({"msg": str(error)}, status_code=400)
        return scheduler.status()

    @application.get("/api/status")
    def api_status():
        try:
            return _credential_status(store.load())
        except CredentialError as error:
            return {**_credential_status(None), "credential_error": str(error)}

    @application.post("/api/cookies")
    def api_save(data: CookiePayload):
        try:
            credentials = Credentials.parse(data.raw).require_login_ready()
            saved = store.replace(credentials)
        except CredentialError as error:
            return JSONResponse({"ok": False, "msg": str(error)}, status_code=400)
        return {"ok": True, "msg": "凭据已保存", "exchange_ready": saved.is_exchange_ready}

    @application.delete("/api/cookies")
    def api_clear():
        store.clear()
        return {"ok": True}

    @application.post("/api/exchange")
    def api_exchange(data: ExchangePayload):
        try:
            credentials = store.load(required=True).require_exchange_ready()
        except CredentialError as error:
            return JSONResponse({"satisfied": False, "msg": str(error)}, status_code=401)
        outcome = client_factory(credentials).redeem(data.reward)
        _log(log_path, outcome)
        return JSONResponse(
            {**outcome.to_dict(), "ok": outcome.satisfied, "msg": outcome.message},
            status_code=_outcome_status(outcome.kind),
        )

    @application.get("/api/log")
    def api_log():
        return {"logs": _read_logs(log_path)}

    return application


def configured_app() -> FastAPI:
    password_file = os.environ.get("WZRY_WEB_PASSWORD_FILE")
    password = Path(password_file).read_text().strip() if password_file else None
    auth = (
        AdminAuth(DEFAULT_COOKIE_FILE.parent / "auth.json")
        if os.environ.get("WZRY_AUTH_ENABLED", "false").lower() == "true"
        else None
    )
    scheduler = None
    if os.environ.get("WZRY_SCHEDULE_ENABLED", "false").lower() == "true":
        scheduler = DailyScheduler(
            DEFAULT_COOKIE_FILE,
            Path(
                os.environ.get(
                    "WZRY_STATE_FILE", str(DEFAULT_COOKIE_FILE.parent / "schedule.sqlite3")
                )
            ),
            at=os.environ.get("WZRY_DAILY_TIME", "09:17"),
            timezone=os.environ.get("TZ", "Asia/Shanghai"),
        )
    return create_app(scheduler=scheduler, password=password, auth=auth)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="王者荣耀体验服兑换 Web 应用")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("WZRY_PORT", 8080)))
    parser.add_argument("--host", default=os.environ.get("WZRY_HOST", "127.0.0.1"))
    parser.add_argument("--cookies", help="启动时导入的 Credential Bundle 文件")
    args = parser.parse_args()
    if args.cookies:
        CredentialStore(DEFAULT_COOKIE_FILE).replace(
            CredentialStore(args.cookies).load(required=True)
        )
    uvicorn.run(configured_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
