"""FastAPI adapter for the reward exchange modules."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .credentials import CredentialError, Credentials, CredentialStore
from .exchange import (
    ICON_BASE,
    PVP_PAGE,
    REWARD_MAP,
    ExchangeClient,
    OutcomeKind,
    RedemptionOutcome,
)

DEFAULT_COOKIE_FILE = Path(os.environ.get("WZRY_COOKIE_FILE", Path.cwd() / "cookies.txt"))
DEFAULT_LOG_FILE = Path(os.environ.get("WZRY_LOG_FILE", Path.cwd() / "exchange.log"))


class RedemptionPort(Protocol):
    def redeem(self, reward_id: str) -> RedemptionOutcome: ...


ClientFactory = Callable[[Credentials], RedemptionPort]


# ── 日志 ──

def _log(log_file: Path, outcome: RedemptionOutcome) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "OK" if outcome.satisfied else "FAIL"
    reward_name = outcome.reward.name if outcome.reward else outcome.reward_id
    message = outcome.message.replace("\n", " ")
    line = f"[{ts}] {status} {reward_name} - {message}\n"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _read_logs(log_file: Path, limit: int = 30) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    if log_file.exists():
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m = re.match(r"\[(.+?)\] (\w+) (.+?) - (.+)", line)
                if m:
                    logs.append({
                        "time": m.group(1), "status": m.group(2),
                        "name": m.group(3), "msg": m.group(4),
                    })
    return logs[-limit:]


# ── HTML 页面 ──

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>王者荣耀体验服兑换</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#0a0e27;color:#e3e9f3;min-height:100vh}}
.container{{max-width:900px;margin:0 auto;padding:20px}}
.header{{text-align:center;padding:30px 0 20px}}
.header h1{{font-size:24px;color:#ffd700;margin-bottom:4px}}
.header p{{color:#8892b0;font-size:13px}}
.card{{background:linear-gradient(135deg,#162044,#1a2755);border-radius:12px;padding:18px;margin-bottom:16px;border:1px solid #2a3560}}
.card-title{{font-size:15px;font-weight:bold;color:#a8b2d9;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.card-title .dot{{width:8px;height:8px;background:#ffd700;border-radius:50%}}
.btn{{display:inline-flex;align-items:center;justify-content:center;padding:10px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;border:none;transition:all .2s;text-decoration:none}}
.btn-gold{{background:linear-gradient(135deg,#f0a500,#d49400);color:#1a1a2e}}
.btn-gold:hover{{transform:translateY(-1px);box-shadow:0 4px 15px rgba(240,165,0,.4)}}
.btn-sm{{padding:6px 14px;font-size:12px}}
.btn:disabled{{opacity:.5;cursor:not-allowed}}
input,textarea{{width:100%;padding:10px;border-radius:8px;border:1px solid #2a3560;background:#0d1230;color:#e3e9f3;font-size:13px;font-family:monospace}}
textarea{{resize:vertical;min-height:80px}}
input:focus,textarea:focus{{outline:none;border-color:#f0a500}}
.reward-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}}
.reward-item{{background:linear-gradient(135deg,#0d1230,#162044);border-radius:10px;padding:14px;border:1px solid #2a3560;transition:all .2s}}
.reward-item:hover{{border-color:#f0a500}}
.reward-icon{{width:56px;height:56px;margin:0 auto 8px;display:block;object-fit:contain}}
.reward-name{{text-align:center;font-size:14px;font-weight:bold;margin-bottom:4px}}
.reward-cost{{text-align:center;font-size:12px;color:#8892b0;margin-bottom:10px}}
.reward-cost span{{color:#ffd700;font-weight:bold}}
.status-bar{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.status-item{{font-size:13px;color:#8892b0}}
.status-item strong{{color:#ffd700}}
.flash{{position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:999;padding:10px 20px;border-radius:8px;font-size:13px;animation:fadeIn .3s;max-width:90vw;text-align:center}}
.flash-success{{background:#27ae60;color:#fff}}
.flash-error{{background:#c0392b;color:#fff}}
@keyframes fadeIn{{from{{opacity:0;transform:translateX(-50%) translateY(-8px)}}to{{opacity:1;transform:translateX(-50%) translateY(0)}}}}
.log-line{{font-size:12px;font-family:monospace;padding:3px 0;border-bottom:1px solid #1a2755;color:#8892b0}}
.log-ok{{color:#27ae60}}
.log-fail{{color:#e74c3c}}
.tabs{{display:flex;gap:4px;margin-bottom:12px}}
.tab{{padding:6px 16px;border-radius:8px 8px 0 0;cursor:pointer;font-size:13px;background:#0d1230;color:#8892b0;border:1px solid transparent}}
.tab.active{{background:#162044;color:#ffd700;border-color:#2a3560;border-bottom-color:#162044}}
.tab-content{{display:none}}
.tab-content.active{{display:block}}
.code-block{{background:#0d1230;border:1px solid #2a3560;border-radius:8px;padding:12px;font-family:monospace;font-size:12px;overflow-x:auto;word-break:break-all;color:#a8b2d9}}
.help-steps{{list-style:decimal;padding-left:20px;line-height:1.8;font-size:13px;color:#a8b2d9}}
.help-steps a{{color:#f0a500}}
.hidden{{display:none!important}}
.footer{{text-align:center;padding:16px;color:#4a5580;font-size:11px}}
</style>
</head>
<body>
<div class="container">
<div class="header">
    <h1>王者荣耀体验服兑换</h1>
    <p>扫码登录 · 一键领取 · 24h到账</p>
</div>

<div class="card status-bar" id="status-bar">
    <div class="status-item">状态: <strong id="status-text">未登录</strong></div>
    <div class="status-item">体验币: <strong id="balance-text">---</strong></div>
    <div class="status-item">分区: <strong id="area-text">---</strong></div>
</div>

<div class="card hidden" id="cookie-form-card">
    <div class="card-title"><span class="dot"></span>设置 Cookie</div>
    <div class="tabs">
        <div class="tab active" onclick="switchTab('paste')">粘贴</div>
        <div class="tab" onclick="switchTab('upload')">上传</div>
        <div class="tab" onclick="switchTab('help')">帮助</div>
    </div>
    <div class="tab-content active" id="tab-paste">
        <textarea id="cookie-input" placeholder="openid=xxx&#10;access_token=xxx&#10;appid=101491592&#10;acctype=qc&#10;a20161115tyf_tyinfo=..."></textarea>
        <button class="btn btn-gold" style="margin-top:10px" onclick="saveCookies()">保存 Cookie</button>
    </div>
    <div class="tab-content" id="tab-upload">
        <input type="file" id="cookie-file" accept=".txt,.json" onchange="uploadFile(this)">
        <p style="font-size:12px;color:#8892b0;margin-top:6px">支持 .txt 或 .json</p>
    </div>
    <div class="tab-content" id="tab-help">
        <ol class="help-steps">
            <li>打开 <a href="{PVP_PAGE}" target="_blank">活动页</a> 登录</li>
            <li>F12 → Application → Cookies → pvp.qq.com</li>
            <li>复制 <b>openid</b>, <b>access_token</b>, <b>appid</b>, <b>acctype</b>, <b>a20161115tyf_tyinfo</b></li>
            <li>粘贴到上方 (每行 name=value)</li>
        </ol>
        <div class="code-block" style="margin-top:10px">
# 或者本地运行 CDP 扫码
cd wzry-ams-exchange && uv run wzry-login
        </div>
    </div>
</div>

<div class="card">
    <div class="card-title"><span class="dot"></span>奖励列表</div>
    <div class="reward-grid" id="reward-grid"></div>
</div>

<div class="card">
    <div class="card-title"><span class="dot"></span>兑换日志</div>
    <div id="log-container"><div class="log-line" style="color:#4a5580">暂无记录</div></div>
</div>

<div class="footer">仅供学习交流 · 接口逆向分析</div>
</div>

<div id="flash-container"></div>

<script>
const ICON_BASE = "{ICON_BASE}";

async function init() {{
  await checkStatus();
  renderRewards();
  loadLog();
}}

async function checkStatus() {{
  const r = await fetch('/api/status');
  const d = await r.json();
  if (d.exchange_ready) {{
    document.getElementById('status-text').textContent = '已登录';
    document.getElementById('status-text').style.color = '#27ae60';
    document.getElementById('balance-text').textContent = d.exp_voucher || '?';
    document.getElementById('area-text').textContent = (d.area||'?')+'/'+(d.partition||'?');
  }} else if (d.has_cookie) {{
    document.getElementById('status-text').textContent = '凭据不完整';
    document.getElementById('status-text').style.color = '#e67e22';
    document.getElementById('balance-text').textContent = d.exp_voucher || '?';
    document.getElementById('area-text').textContent = '---';
    document.getElementById('cookie-form-card').classList.remove('hidden');
  }} else {{
    document.getElementById('status-text').textContent = '未登录';
    document.getElementById('status-text').style.color = '#e74c3c';
    document.getElementById('balance-text').textContent = '---';
    document.getElementById('area-text').textContent = '---';
    document.getElementById('cookie-form-card').classList.remove('hidden');
  }}
}}

async function saveCookies() {{
  const raw = document.getElementById('cookie-input').value.trim();
  if (!raw) return flash('请输入 Cookie', 'error');
  const r = await fetch('/api/cookies', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{raw}})
  }});
  const d = await r.json();
  if (d.ok) {{
    flash('Cookie 已保存', 'success');
    document.getElementById('cookie-form-card').classList.add('hidden');
    document.getElementById('cookie-input').value = '';
    await checkStatus();
  }} else {{ flash(d.msg||'失败', 'error'); }}
}}

async function uploadFile(input) {{
  const f = input.files[0];
  if (!f) return;
  const text = await f.text();
  const r = await fetch('/api/cookies', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{raw:text}})
  }});
  const d = await r.json();
  if (d.ok) {{
    flash('Cookie 已上传', 'success');
    document.getElementById('cookie-form-card').classList.add('hidden');
    await checkStatus();
  }} else {{ flash(d.msg||'失败', 'error'); }}
}}

function renderRewards() {{
  const rewards = {REWARD_JSON};
  const grid = document.getElementById('reward-grid');
  grid.innerHTML = '';
  for (const [id, r] of Object.entries(rewards)) {{
    const div = document.createElement('div');
    div.className = 'reward-item';
    div.innerHTML = '<img src="'+ICON_BASE+r.icon+'" class="reward-icon" onerror="this.style.display=\\'none\\'">'
      + '<div class="reward-name">'+r.name+'</div>'
      + '<div class="reward-cost">体验币: <span>'+r.cost+'</span></div>'
      + '<button class="btn btn-gold btn-sm" style="width:100%" onclick="exchange(\\''+id+'\\', this)">兑换</button>';
    grid.appendChild(div);
  }}
}}

async function exchange(id, btn) {{
  btn.disabled = true;
  btn.textContent = '…';
  try {{
    const r = await fetch('/api/exchange', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{reward:id}})
    }});
    const d = await r.json();
    if (d.ok) {{
      flash(d.msg + (d.package ? ' — '+d.package : ''), 'success');
      await checkStatus();
    }} else {{ flash(d.msg, 'error'); }}
    loadLog();
  }} catch(e) {{ flash('错误: '+e.message, 'error'); }}
  setTimeout(() => {{ btn.disabled = false; btn.textContent = '兑换'; }}, 2000);
}}

async function loadLog() {{
  const r = await fetch('/api/log');
  const d = await r.json();
  const c = document.getElementById('log-container');
  c.replaceChildren();
  if (!d.logs.length) {{
    const empty = document.createElement('div');
    empty.className = 'log-line';
    empty.style.color = '#4a5580';
    empty.textContent = '暂无记录';
    c.appendChild(empty);
    return;
  }}
  for (const l of [...d.logs].reverse()) {{
    const row = document.createElement('div');
    row.className = 'log-line '+(l.status==='OK'?'log-ok':'log-fail');
    row.textContent = l.time+' '+l.status+' '+l.name+' — '+l.msg;
    c.appendChild(row);
  }}
}}

function switchTab(name) {{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.querySelector('.tab[onclick="switchTab(\\''+name+'\\')"]').classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
}}

function flash(msg, type) {{
  const c = document.getElementById('flash-container');
  const d = document.createElement('div');
  d.className = 'flash flash-'+type;
  d.textContent = msg;
  c.appendChild(d);
  setTimeout(() => d.remove(), 2800);
}}

init();
</script>
</body>
</html>"""


class CookiePayload(BaseModel):
    raw: str


class ExchangePayload(BaseModel):
    reward: str


def _credential_status(credentials: Credentials | None) -> dict[str, object]:
    if credentials is None:
        return {
            "has_cookie": False,
            "exchange_ready": False,
            "openid": "",
            "acctype": "?",
            "exp_voucher": "?",
            "area": "?",
            "partition": "?",
        }
    identity = credentials.activity_identity
    openid = credentials.values.get("openid", "")
    redacted_openid = f"{openid[:8]}..." if openid else ""
    return {
        "has_cookie": credentials.is_login_ready,
        "exchange_ready": credentials.is_exchange_ready,
        "openid": redacted_openid,
        "acctype": credentials.values.get("acctype", "?"),
        "exp_voucher": credentials.experience_voucher,
        "area": identity.area if identity else "?",
        "partition": identity.partition if identity else "?",
    }


def _outcome_status(kind: OutcomeKind) -> int:
    if kind is OutcomeKind.INVALID_REWARD:
        return 400
    if kind is OutcomeKind.AUTHENTICATION_FAILED:
        return 401
    if kind is OutcomeKind.REJECTED:
        return 409
    if kind is OutcomeKind.PROTOCOL_FAILURE:
        return 502
    if kind is OutcomeKind.TRANSIENT_FAILURE:
        return 503
    return 200


def create_app(
    *,
    cookie_file: str | os.PathLike[str] = DEFAULT_COOKIE_FILE,
    log_file: str | os.PathLike[str] = DEFAULT_LOG_FILE,
    client_factory: ClientFactory = ExchangeClient,
) -> FastAPI:
    application = FastAPI(title="王者荣耀体验服兑换", version="2.0.0", docs_url=None)
    store = CredentialStore(cookie_file)
    log_path = Path(log_file)

    @application.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE.format(
            PVP_PAGE=PVP_PAGE,
            ICON_BASE=ICON_BASE,
            REWARD_JSON=json.dumps(REWARD_MAP, ensure_ascii=False),
        )

    @application.get("/api/status")
    def api_status() -> dict[str, object]:
        try:
            return _credential_status(store.load())
        except CredentialError as error:
            return {**_credential_status(None), "credential_error": str(error)}

    @application.post("/api/cookies")
    def api_save(data: CookiePayload) -> JSONResponse:
        try:
            credentials = Credentials.parse(data.raw).require_login_ready()
            saved = store.replace(credentials)
        except CredentialError as error:
            return JSONResponse({"ok": False, "msg": str(error)}, status_code=400)
        return JSONResponse(
            {
                "ok": True,
                "msg": f"已安全保存 {len(saved.values)} 个 Cookie",
                "exchange_ready": saved.is_exchange_ready,
            }
        )

    @application.post("/api/exchange")
    def api_exchange(data: ExchangePayload) -> JSONResponse:
        try:
            credentials = store.load(required=True).require_exchange_ready()
        except CredentialError as error:
            return JSONResponse({"satisfied": False, "msg": str(error)}, status_code=401)

        outcome = client_factory(credentials).redeem(data.reward)
        _log(log_path, outcome)
        payload = outcome.to_dict()
        payload["ok"] = outcome.satisfied
        payload["msg"] = outcome.message
        return JSONResponse(payload, status_code=_outcome_status(outcome.kind))

    @application.get("/api/log")
    def api_log() -> dict[str, list[dict[str, str]]]:
        return {"logs": _read_logs(log_path)}

    @application.delete("/api/cookies")
    def api_clear() -> dict[str, bool]:
        store.clear()
        return {"ok": True}

    return application


app = create_app()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="王者荣耀体验服兑换 Web 应用")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("WZRY_PORT", 8080)))
    parser.add_argument("--host", default=os.environ.get("WZRY_HOST", "127.0.0.1"))
    parser.add_argument("--cookies", help="启动时导入的 Credential Bundle 文件")
    args = parser.parse_args()

    if args.cookies:
        try:
            imported = CredentialStore(args.cookies).load(required=True)
            CredentialStore(DEFAULT_COOKIE_FILE).replace(imported)
        except CredentialError as error:
            raise SystemExit(f"Credential Bundle 导入失败: {error}") from error
        print(f"[*] 已导入 {len(imported.values)} 个 Cookie")

    print("  王者荣耀体验服兑换 Web 应用")
    print(f"  http://{args.host}:{args.port}")
    uvicorn.run(
        "wzry_ams.web:app",
        host=args.host,
        port=args.port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
