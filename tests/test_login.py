"""登录 module 的资源生命周期与结果语义测试。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from wzry_ams import login
from wzry_ams.login import ChromeManager, LoginResult, LoginStatus, scan_login


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def exchange_ready_cookies() -> dict[str, str]:
    return {
        "openid": "openid",
        "access_token": "token",
        "appid": "app",
        "acctype": "qc",
        "iegams_milo_proxylogin_qc": "proxy",
        "a20161115tyf_tyinfo": (
            "zf_openid,official@ty_openid,experience@"
            "zf_area,1@zf_partition,1306"
        ),
    }


class FakePage:
    def __init__(
        self,
        responses: list[dict[str, str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = responses or [{}]
        self.error = error
        self.calls = 0

    def get_cookies(self) -> dict[str, str]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        index = min(self.calls - 1, len(self.responses) - 1)
        return dict(self.responses[index])


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.entered = False
        self.exited = False
        self.page_opened = False
        self.page_closed = False

    def __enter__(self) -> FakeBrowser:
        self.entered = True
        return self

    def __exit__(self, *args: object) -> None:
        self.exited = True

    @contextmanager
    def open_login_page(self) -> Iterator[FakePage]:
        self.page_opened = True
        try:
            yield self.page
        finally:
            self.page_closed = True


def assert_resources_closed(browser: FakeBrowser) -> None:
    assert browser.entered is True
    assert browser.exited is True
    assert browser.page_opened is True
    assert browser.page_closed is True


def test_scan_login_success_returns_typed_result_and_closes_resources() -> None:
    clock = FakeClock()
    page = FakePage(
        [
            {"openid": "openid", "access_token": "token"},
            {
                "openid": "openid",
                "access_token": "token",
                "appid": "app",
                "acctype": "qc",
            },
            exchange_ready_cookies(),
        ]
    )
    browser = FakeBrowser(page)

    result = scan_login(browser_factory=lambda: browser, clock=clock)

    assert result == LoginResult(
        status=LoginStatus.SUCCESS,
        cookies=exchange_ready_cookies(),
        message="登录成功，兑换凭据已就绪",
    )
    assert result.ok is True
    assert clock.sleeps == [2.0, 3.0]
    assert_resources_closed(browser)


def test_scan_login_reports_authenticated_but_incomplete_bundle() -> None:
    clock = FakeClock()
    login_only = {
        "openid": "openid",
        "access_token": "token",
        "appid": "app",
        "acctype": "qc",
    }
    browser = FakeBrowser(FakePage([login_only]))

    result = scan_login(
        timeout=5,
        browser_factory=lambda: browser,
        clock=clock,
    )

    assert result.status is LoginStatus.INCOMPLETE
    assert result.cookies == login_only
    assert result.ok is False
    assert "未获取完整兑换凭据" in result.message
    assert clock.sleeps == [3.0, 2.0]
    assert_resources_closed(browser)


def test_scan_login_timeout_returns_last_cookies_and_closes_resources() -> None:
    clock = FakeClock()
    browser = FakeBrowser(FakePage([{"uin": "123"}]))

    result = scan_login(timeout=5, browser_factory=lambda: browser, clock=clock)

    assert result.status is LoginStatus.TIMEOUT
    assert result.cookies == {"uin": "123"}
    assert result.ok is False
    assert clock.sleeps == [2.0, 2.0, 1.0]
    assert_resources_closed(browser)


def test_scan_login_failure_closes_resources() -> None:
    clock = FakeClock()
    browser = FakeBrowser(FakePage(error=RuntimeError("CDP disconnected")))

    result = scan_login(browser_factory=lambda: browser, clock=clock)

    assert result.status is LoginStatus.FAILURE
    assert result.cookies == {}
    assert result.message == "CDP disconnected"
    assert_resources_closed(browser)


class FakeWebSocket:
    def __init__(self) -> None:
        self.message_id = 0
        self.closed = False

    def send(self, payload: str) -> None:
        self.message_id = int(json.loads(payload)["id"])

    def settimeout(self, timeout: float) -> None:
        del timeout

    def recv(self) -> str:
        return json.dumps({"id": self.message_id, "result": {}})

    def close(self) -> None:
        self.closed = True


class FakeWebSocketModule:
    def __init__(self, ws: FakeWebSocket) -> None:
        self.ws = ws

    def create_connection(self, url: str, timeout: int) -> FakeWebSocket:
        assert url == "ws://login-page"
        assert timeout == 10
        return self.ws


def test_cookie_collection_prefers_the_activity_page_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "https://pvp.qq.com": "activity-skey",
        "https://game.qq.com": "game-skey",
        "https://.qq.com": "parent-skey",
        "https://smoba.ams.game.qq.com": "ams-skey",
    }

    def fake_send(
        ws: object,
        method: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del ws
        assert method == "Network.getCookies"
        assert params is not None
        url = str(params["urls"][0])  # type: ignore[index]
        return {"cookies": [{"name": "skey", "value": values[url]}]}

    monkeypatch.setattr(login, "_cdp_ws_send", fake_send)

    cookies = login._CdpLoginPage(object()).get_cookies()

    assert cookies["skey"] == "activity-skey"


def test_login_page_always_closes_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWebSocket()
    monkeypatch.setattr(login, "websocket", FakeWebSocketModule(ws))
    manager = ChromeManager(chrome_path="/fake/chrome", port=45123)
    monkeypatch.setattr(
        manager,
        "_request",
        lambda path: [
            {
                "type": "page",
                "url": "https://pvp.qq.com/login",
                "webSocketDebuggerUrl": "ws://login-page",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="consumer failed"), manager.open_login_page():
        raise RuntimeError("consumer failed")

    assert ws.closed is True


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.signals: list[object] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: int) -> int:
        del timeout
        self.returncode = 0
        return 0

    def send_signal(self, sig: object) -> None:
        self.signals.append(sig)


def test_chrome_manager_uses_dynamic_port_and_removes_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command: list[str] = []
    process = FakeProcess()

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        del kwargs
        command.extend(args)
        return process

    monkeypatch.setattr(login, "_find_free_port", lambda: 45123)
    monkeypatch.setattr(login.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(login.os, "killpg", lambda pid, sig: process.signals.append((pid, sig)))
    monkeypatch.setattr(login.os, "getpgid", lambda pid: pid)

    manager = ChromeManager(chrome_path="/fake/chrome", clock=FakeClock())
    monkeypatch.setattr(manager, "_request", lambda path: {"Browser": "Chrome"})

    with manager:
        profile_argument = next(arg for arg in command if arg.startswith("--user-data-dir="))
        profile_path = Path(profile_argument.removeprefix("--user-data-dir="))
        assert profile_path.exists()
        assert "--remote-debugging-port=45123" in command

    assert profile_path.exists() is False
    assert process.signals


def test_chrome_manager_failed_start_cleans_process_and_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command: list[str] = []
    process = FakeProcess()

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        del kwargs
        command.extend(args)
        return process

    monkeypatch.setattr(login, "_find_free_port", lambda: 45123)
    monkeypatch.setattr(login.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(login.os, "killpg", lambda pid, sig: process.signals.append((pid, sig)))
    monkeypatch.setattr(login.os, "getpgid", lambda pid: pid)

    manager = ChromeManager(chrome_path="/fake/chrome", clock=FakeClock())

    def fail_request(path: str) -> None:
        raise OSError(path)

    monkeypatch.setattr(manager, "_request", fail_request)

    with pytest.raises(RuntimeError, match="Chrome CDP 未就绪"):
        manager.start()

    profile_argument = next(arg for arg in command if arg.startswith("--user-data-dir="))
    profile_path = Path(profile_argument.removeprefix("--user-data-dir="))
    assert profile_path.exists() is False
    assert manager.process is None
    assert manager.profile_dir is None
    assert process.signals
