"""CDP 扫码登录及其浏览器资源生命周期。"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import TracebackType
from typing import Any, Protocol

import requests

from .credentials import Credentials

try:
    import websocket
except ImportError:
    websocket = None

APPID = "101491592"
PVP_PAGE = "https://pvp.qq.com/cp/a20161115tyf/page2.shtml"

CHROME_PATH = (
    shutil.which("google-chrome")
    or shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome-stable")
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

WELCOME = r"""
  ╔══════════════════════════════════════════╗
  ║     王者荣耀体验服 Cookie 登录工具       ║
  ╠══════════════════════════════════════════╣
  ║  即将打开浏览器，请在页面中用             ║
  ║  QQ / 王者营地 扫码登录                  ║
  ║  登录成功后自动提取 Cookie               ║
  ╚══════════════════════════════════════════╝
"""

_COOKIE_URLS = (
    "https://pvp.qq.com",
    "https://game.qq.com",
    "https://.qq.com",
    "https://smoba.ams.game.qq.com",
)
_CDP_MESSAGE_IDS = itertools.count(1)
_CDP_READY_ATTEMPTS = 20
_CDP_READY_INTERVAL_SECONDS = 0.25
_LOGIN_POLL_INTERVAL_SECONDS = 2.0
_COOKIE_SETTLE_SECONDS = 3.0


class LoginStatus(str, Enum):
    """扫码登录的终态。"""

    SUCCESS = "success"
    INCOMPLETE = "incomplete"
    TIMEOUT = "timeout"
    FAILURE = "failure"


@dataclass(frozen=True)
class LoginResult:
    """登录结果；失败和超时通过状态表达，不要求调用方解析日志。"""

    status: LoginStatus
    cookies: dict[str, str] = field(default_factory=dict)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is LoginStatus.SUCCESS


class Clock(Protocol):
    """登录等待使用的时钟 seam。"""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class LoginPage(Protocol):
    """登录流程需要的最小页面 interface。"""

    def get_cookies(self) -> dict[str, str]: ...


class BrowserAdapter(Protocol):
    """可替换的浏览器 adapter，用于隔离 Chrome/CDP 生命周期。"""

    def __enter__(self) -> BrowserAdapter: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def open_login_page(self) -> contextlib.AbstractContextManager[LoginPage]: ...


class _SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


_SYSTEM_CLOCK = _SystemClock()
BrowserFactory = Callable[[], BrowserAdapter]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _cdp_req(
    port: int,
    path: str,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    if method == "GET":
        response = requests.get(url, timeout=5)
    elif method == "PUT":
        response = requests.put(url, json=dict(body or {}), timeout=5)
    else:
        raise ValueError(f"Unsupported: {method}")
    if response.status_code >= 400:
        raise RuntimeError(f"CDP {method} {path}: {response.status_code}")
    return response.json() if response.text else {}


def _cdp_ws_send(ws: Any, method: str, params: Mapping[str, object] | None = None) -> dict:
    message_id = next(_CDP_MESSAGE_IDS)
    ws.send(json.dumps({"id": message_id, "method": method, "params": dict(params or {})}))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            ws.settimeout(min(1.0, remaining))
            response = json.loads(ws.recv())
        except Exception:
            continue
        if response.get("id") != message_id:
            continue
        if "error" in response:
            raise RuntimeError(f"CDP {method} failed: {response['error']}")
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}
    raise TimeoutError(f"CDP {method} 响应超时")


class _CdpLoginPage:
    def __init__(self, ws: Any):
        self._ws = ws

    def enable_network(self) -> None:
        _cdp_ws_send(self._ws, "Network.enable")

    def get_cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        successful_requests = 0
        last_error: Exception | None = None
        for url in _COOKIE_URLS:
            try:
                result = _cdp_ws_send(self._ws, "Network.getCookies", {"urls": [url]})
                successful_requests += 1
            except Exception as exc:
                last_error = exc
                continue
            raw_cookies = result.get("cookies", [])
            if not isinstance(raw_cookies, list):
                continue
            for cookie in raw_cookies:
                if not isinstance(cookie, dict):
                    continue
                name = cookie.get("name")
                value = cookie.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    # The activity page is queried first, so keep its cookie when
                    # another QQ host exposes a same-name cookie with another scope.
                    cookies.setdefault(name, value)
        if successful_requests == 0 and last_error is not None:
            raise RuntimeError("无法从 CDP 获取 Cookie") from last_error
        return cookies


class ChromeManager:
    """拥有 Chrome 进程、临时 profile 和页面 WebSocket 的 adapter。"""

    def __init__(
        self,
        *,
        chrome_path: str | None = None,
        port: int | None = None,
        clock: Clock = _SYSTEM_CLOCK,
    ):
        self.chrome_path = CHROME_PATH if chrome_path is None else chrome_path
        self._configured_port = port
        self.port = port
        self.clock = clock
        self.process: subprocess.Popen[bytes] | None = None
        self.profile_dir: str | None = None

    def __enter__(self) -> ChromeManager:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _request(
        self,
        path: str,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        if self.port is None:
            raise RuntimeError("Chrome CDP 端口尚未分配")
        return _cdp_req(self.port, path, method, body)

    def start(self) -> None:
        if not self.chrome_path:
            raise RuntimeError("未找到 Chrome/Chromium 浏览器")
        if self.process is not None:
            raise RuntimeError("Chrome 已启动")

        try:
            self.port = self.port if self.port is not None else _find_free_port()
            self.profile_dir = tempfile.mkdtemp(prefix="wzry_chrome_")
            os.chmod(self.profile_dir, 0o700)

            command = [
                self.chrome_path,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--no-sandbox",
                "--disable-gpu",
                PVP_PAGE,
            ]
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            for _ in range(_CDP_READY_ATTEMPTS):
                if self.process.poll() is not None:
                    raise RuntimeError("Chrome 在 CDP 就绪前退出")
                try:
                    self._request("/json/version")
                except Exception:
                    self.clock.sleep(_CDP_READY_INTERVAL_SECONDS)
                else:
                    return
            raise RuntimeError("Chrome CDP 未就绪")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            self._signal_process(process, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._signal_process(process, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    process.wait(timeout=1)

        profile_dir = self.profile_dir
        self.profile_dir = None
        self.port = self._configured_port
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)

    @staticmethod
    def _signal_process(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except Exception:
            with contextlib.suppress(Exception):
                process.send_signal(sig)

    @contextlib.contextmanager
    def open_login_page(self) -> Iterator[LoginPage]:
        if websocket is None:
            raise RuntimeError("需要 websocket-client: pip install websocket-client")

        tabs = self._request("/json/list")
        ws_url = self._find_page_websocket_url(tabs)
        if ws_url is None:
            raise RuntimeError("无法连接浏览器页面")

        ws = websocket.create_connection(ws_url, timeout=10)
        try:
            page = _CdpLoginPage(ws)
            page.enable_network()
            yield page
        finally:
            with contextlib.suppress(Exception):
                ws.close()

    @staticmethod
    def _find_page_websocket_url(tabs: object) -> str | None:
        if not isinstance(tabs, list):
            return None
        pages = [tab for tab in tabs if isinstance(tab, dict) and tab.get("type") == "page"]
        preferred = next((tab for tab in pages if "pvp.qq.com" in str(tab.get("url", ""))), None)
        selected = preferred or next(iter(pages), None)
        if selected is None:
            return None
        ws_url = selected.get("webSocketDebuggerUrl")
        return ws_url if isinstance(ws_url, str) and ws_url else None


def _default_browser_factory(clock: Clock) -> BrowserAdapter:
    return ChromeManager(clock=clock)


def scan_login(
    timeout: int = 180,
    *,
    browser_factory: BrowserFactory | None = None,
    clock: Clock = _SYSTEM_CLOCK,
) -> LoginResult:
    """执行扫码登录，并将所有完成路径收敛为一个类型化结果。"""
    factory = browser_factory or (lambda: _default_browser_factory(clock))
    latest_cookies: dict[str, str] = {}
    login_detected = False

    try:
        with factory() as browser, browser.open_login_page() as page:
            deadline = clock.monotonic() + max(timeout, 0)
            while True:
                latest_cookies = page.get_cookies()
                credentials = Credentials.from_mapping(latest_cookies)
                if credentials.is_exchange_ready:
                    return LoginResult(
                        status=LoginStatus.SUCCESS,
                        cookies=latest_cookies,
                        message="登录成功，兑换凭据已就绪",
                    )

                remaining = deadline - clock.monotonic()
                if remaining <= 0:
                    if credentials.is_login_ready:
                        return LoginResult(
                            status=LoginStatus.INCOMPLETE,
                            cookies=latest_cookies,
                            message="登录成功，但未获取完整兑换凭据，请重试扫码登录",
                        )
                    return LoginResult(
                        status=LoginStatus.TIMEOUT,
                        cookies=latest_cookies,
                        message=f"等待扫码登录超时（{max(timeout, 0)} 秒）",
                    )
                delay = (
                    _COOKIE_SETTLE_SECONDS
                    if credentials.is_login_ready and not login_detected
                    else _LOGIN_POLL_INTERVAL_SECONDS
                )
                login_detected = login_detected or credentials.is_login_ready
                clock.sleep(min(delay, remaining))
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        return LoginResult(
            status=LoginStatus.FAILURE,
            cookies=latest_cookies,
            message=message,
        )
