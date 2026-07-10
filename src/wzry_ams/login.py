"""CDP 扫码登录 — 启动 Chrome 获取 Cookie."""

import contextlib
import json
import os
import random
import shutil
import signal
import subprocess
import tempfile
import time

import requests

try:
    import websocket
except ImportError:
    websocket = None

APPID = "101491592"
PVP_PAGE = "https://pvp.qq.com/cp/a20161115tyf/page2.shtml"
CDP_PORT = 9222

CHROME_PATH = (shutil.which("google-chrome") or shutil.which("chromium") or
               shutil.which("chromium-browser") or
               shutil.which("google-chrome-stable"))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/149.0.0.0 Safari/537.36")

WELCOME = r"""
  ╔══════════════════════════════════════════╗
  ║     王者荣耀体验服 Cookie 登录工具       ║
  ╠══════════════════════════════════════════╣
  ║  即将打开浏览器，请在页面中用             ║
  ║  QQ / 王者营地 扫码登录                  ║
  ║  登录成功后自动提取 Cookie               ║
  ╚══════════════════════════════════════════╝
"""


# ── CDP 通信 ──

def _cdp_req(path: str, method: str = "GET", body: dict | None = None) -> dict:
    url = f"http://localhost:{CDP_PORT}{path}"
    if method == "GET":
        r = requests.get(url, timeout=5)
    elif method == "PUT":
        r = requests.put(url, json=body or {}, timeout=5)
    else:
        raise ValueError(f"Unsupported: {method}")
    if r.status_code >= 400:
        raise RuntimeError(f"CDP {method} {path}: {r.status_code}")
    return r.json() if r.text else {}


def _cdp_ws_send(ws, method: str, params: dict | None = None) -> dict:
    msg_id = random.randint(1, 999999)
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    timeout = time.time() + 10
    while time.time() < timeout:
        try:
            ws.settimeout(1)
            data = ws.recv()
            resp = json.loads(data)
            if resp.get("id") == msg_id:
                return resp.get("result", {})
        except Exception:
            continue
    return {}


# ── Chrome 管理 ──

class ChromeManager:
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.profile_dir: str | None = None

    def start(self):
        if not CHROME_PATH:
            raise RuntimeError("未找到 Chrome/Chromium 浏览器")

        self.profile_dir = tempfile.mkdtemp(prefix="wzry_chrome_")
        os.chmod(self.profile_dir, 0o700)

        subprocess.run(["pkill", "-f", f"remote-debugging-port={CDP_PORT}"],
                       capture_output=True)
        time.sleep(0.5)

        cmd = [
            CHROME_PATH,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking",
            "--disable-sync", "--no-sandbox", "--disable-gpu",
            PVP_PAGE,
        ]
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1.5)

        for _ in range(10):
            try:
                _cdp_req("/json/version")
                return True
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("Chrome CDP 未就绪")

    def stop(self):
        if self.process:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process = None
            time.sleep(0.5)
        if self.profile_dir and os.path.exists(self.profile_dir):
            with contextlib.suppress(Exception):
                shutil.rmtree(self.profile_dir, ignore_errors=True)

    def get_cookies(self, ws, urls: list[str] | None = None) -> dict[str, str]:
        if urls is None:
            urls = [
                "https://pvp.qq.com", "https://game.qq.com",
                "https://.qq.com", "https://smoba.ams.game.qq.com",
            ]
        all_cookies = {}
        for url in urls:
            try:
                result = _cdp_ws_send(ws, "Network.getCookies", {"urls": [url]})
                for c in result.get("cookies", []):
                    all_cookies[c["name"]] = c["value"]
            except Exception:
                pass
        return all_cookies


# ── 登录流程 ──

def qq_scan_login(output_path: str = "cookies.txt", timeout: int = 180) -> dict[str, str]:
    """CDP 扫码登录流程。返回 Cookie 字典。"""
    if websocket is None:
        raise ImportError("需要 websocket-client: pip install websocket-client")

    print(WELCOME)

    cm = ChromeManager()
    try:
        cm.start()
    except RuntimeError as e:
        print(f"[!] {e}")
        return {}

    tabs = _cdp_req("/json/list")
    ws_url = None
    for tab in tabs:
        if tab.get("type") == "page" and "pvp.qq.com" in tab.get("url", ""):
            ws_url = tab.get("webSocketDebuggerUrl")
            break
    if not ws_url and tabs:
        for tab in tabs:
            if tab.get("type") == "page":
                ws_url = tab.get("webSocketDebuggerUrl")
                break
    if not ws_url:
        print("[!] 无法连接浏览器页面")
        cm.stop()
        return {}

    ws = websocket.create_connection(ws_url, timeout=10)
    _cdp_ws_send(ws, "Network.enable")

    print("[*] 请在浏览器中扫码登录 (QQ / 王者营地)")
    print(f"[*] 等待登录... (超时 {timeout} 秒)\n")

    cookies = {}
    start = time.time()
    while time.time() - start < timeout:
        cookies = cm.get_cookies(ws)
        if cookies.get("openid") and cookies.get("access_token"):
            break
        time.sleep(2)

    if cookies.get("openid"):
        time.sleep(3)
        cookies = cm.get_cookies(ws)

    cm.stop()
    return cookies
