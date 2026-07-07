"""王者荣耀体验服兑换 Web 应用 (FastAPI 单文件)."""

import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from .utils import (
    REWARD_MAP, REQUIRED_COOKIES, ICON_BASE, PVP_PAGE,
    parse_cookies, parse_tyinfo, load_cookies_file,
    save_cookies_file, get_user_info,
)
from .exchange import ExchangeClient


# ── 应用 ──

app = FastAPI(title="王者荣耀体验服兑换", version="1.0.0", docs_url=None)

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_FILE = os.path.join(SCRIPT_DIR, "cookies.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "exchange.log")


# ── 日志 ──

def _log(reward_name: str, result: dict):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "OK" if result.get("ok") else "FAIL"
    line = f"[{ts}] {status} {reward_name} - {result.get('msg', '')}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass


def _read_logs(limit: int = 30) -> list:
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
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
  if (d.has_cookie) {{
    document.getElementById('status-text').textContent = '已登录';
    document.getElementById('status-text').style.color = '#27ae60';
    document.getElementById('balance-text').textContent = d.exp_voucher || '?';
    document.getElementById('area-text').textContent = (d.area||'?')+'/'+(d.partition||'?');
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
      + '<button class="btn btn-gold btn-sm" style="width:100%" onclick="exchange(\\''+id+'\\')">兑换</button>';
    grid.appendChild(div);
  }}
}}

async function exchange(id) {{
  const btn = event.target;
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
  if (!d.logs.length) {{ c.innerHTML = '<div class="log-line" style="color:#4a5580">暂无记录</div>'; return; }}
  c.innerHTML = [...d.logs].reverse().map(l =>
    '<div class="log-line '+(l.status==='OK'?'log-ok':'log-fail')+'">'+l.time+' '+l.status+' '+l.name+' — '+l.msg+'</div>'
  ).join('');
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


# ── 路由 ──

@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE.replace("{PVP_PAGE}", PVP_PAGE)\
               .replace("{ICON_BASE}", ICON_BASE)\
               .replace("{REWARD_JSON}", json.dumps(REWARD_MAP, ensure_ascii=False))


@app.get("/api/status")
async def api_status():
    cookies = load_cookies_file(COOKIE_FILE)
    has = bool(cookies.get("openid") and cookies.get("access_token"))
    info = get_user_info(cookies)
    info["has_cookie"] = has
    return info


@app.post("/api/cookies")
async def api_save(data: dict):
    raw = data.get("raw", "")
    if not raw:
        return {"ok": False, "msg": "空内容"}

    cookies = parse_cookies(raw)
    missing = REQUIRED_COOKIES - set(cookies.keys())
    if missing:
        return {"ok": False, "msg": f"缺少: {', '.join(missing)}"}

    # 合并已存在的 tyinfo (如果新提交中没有)
    old = load_cookies_file(COOKIE_FILE)
    if "a20161115tyf_tyinfo" not in cookies and "a20161115tyf_tyinfo" in old:
        cookies["a20161115tyf_tyinfo"] = old["a20161115tyf_tyinfo"]

    save_cookies_file(cookies, COOKIE_FILE)
    return {"ok": True, "msg": f"已保存 {len(cookies)} 个 Cookie"}


@app.post("/api/exchange")
async def api_exchange(data: dict):
    rid = str(data.get("reward", ""))
    if rid not in REWARD_MAP:
        return {"ok": False, "msg": "无效奖励"}

    cookies = load_cookies_file(COOKIE_FILE)
    if not cookies.get("openid"):
        return {"ok": False, "msg": "请先设置 Cookie"}

    client = ExchangeClient(cookies)
    result = client.exchange_reward(rid)
    _log(REWARD_MAP[rid]["name"], result)
    return result


@app.get("/api/log")
async def api_log():
    return {"logs": _read_logs()}


@app.delete("/api/cookies")
async def api_clear():
    if os.path.exists(COOKIE_FILE):
        os.remove(COOKIE_FILE)
    return {"ok": True}


# ── 启动入口 ──

def main():
    import argparse
    ap = argparse.ArgumentParser(description="王者荣耀体验服兑换 Web 应用")
    ap.add_argument("--port", "-p", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--cookies", help="启动时加载的 Cookie 文件")
    args = ap.parse_args()

    if args.cookies and os.path.exists(args.cookies):
        cookies = parse_cookies(open(args.cookies).read())
        if cookies:
            save_cookies_file(cookies, COOKIE_FILE)
            print(f"[*] 已加载 {len(cookies)} 个 Cookie")

    print(f"  王者荣耀体验服兑换 Web 应用")
    print(f"  http://{args.host}:{args.port}")
    uvicorn.run("wzry_ams.web:app", host=args.host, port=args.port,
                log_level="info", reload=False)


if __name__ == "__main__":
    main()
