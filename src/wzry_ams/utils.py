"""王者荣耀体验服 AMS 兑换 - 共享工具模块."""

import hashlib
import os
import random
import string
import time
from datetime import datetime
from urllib.parse import unquote

# ── 常量 ──

REWARD_MAP = {
    "1": {"name": "亲密玫瑰",         "flowId": 407551, "cost": 40,  "icon": "pic_dj4.png"},
    "2": {"name": "大型钻石福袋",     "flowId": 407552, "cost": 50,  "icon": "pic_dj5.png"},
    "3": {"name": "星币福袋",         "flowId": 407553, "cost": 60,  "icon": "pic_dj7.png"},
    "4": {"name": "碎片福袋",         "flowId": 407554, "cost": 80,  "icon": "pic_dj6.png"},
    "5": {"name": "浓情玫瑰",         "flowId": 407555, "cost": 80,  "icon": "pic_dj8.png"},
    "6": {"name": "体验服专属头像框", "flowId": 407556, "cost": 900, "icon": "pic_dj9.png"},
}

AMS_BASE = "https://smoba.ams.game.qq.com/ams/ame/amesvr"
PVP_PAGE = "https://pvp.qq.com/cp/a20161115tyf/page2.shtml"
ICON_BASE = "//game.gtimg.cn/images/yxzj/cp/a20161115tyf/"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/149.0.0.0 Safari/537.36")

REQUIRED_COOKIES = {"openid", "access_token", "appid", "acctype"}


# ── 哈希 / 签名 ──

def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


def g_tk(skey: str = "a1b2c3") -> int:
    """AME CSRF token — flowengine.js ameCSRFToken (DJB hash33)."""
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def ts() -> str:
    return str(int(time.time()))


def sdid() -> str:
    return md5(str(random.random()))


def rand_str(n: int = 6) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits)
                   for _ in range(n))


def milo_tag(activity_id: int, flow_id: int, openid: str) -> str:
    from time import time as t
    return (f"AMS-MILO-{activity_id}-{flow_id}-{openid}"
            f"-{int(t() * 1000)}-{rand_str()}")


def ams_serial(activity_id: int, flow_id: int) -> str:
    t = datetime.now().strftime("%m%d%H%M%S")
    return f"AMS-YXZJ-{t}-{rand_str()}-{activity_id}-{flow_id}"


# ── Cookie 解析 ──

def parse_tyinfo(cookie_value: str) -> dict[str, str]:
    """解析 a20161115tyf_tyinfo cookie."""
    result = {}
    for pair in unquote(cookie_value).split("@"):
        if "," in pair:
            k, v = pair.split(",", 1)
            result[k] = v
    return result


def parse_cookies(source: str) -> dict[str, str]:
    """解析多种 Cookie 格式 (JSON / Netscape / key=value)."""
    s = source.strip()
    if s.startswith("{"):
        import json
        return json.loads(s)

    cookies: dict[str, str] = {}
    if "\t" not in s and "\n" not in s and ";" in s:
        s = s.replace(";", "\n")

    for line in s.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
        elif "=" in line:
            k, _, v = line.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def load_cookies_file(path: str) -> dict[str, str]:
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        return parse_cookies(f.read())


def save_cookies_file(cookies: dict[str, str], path: str):
    with open(path, "w") as f:
        for k, v in sorted(cookies.items()):
            if v:
                f.write(f"{k}={v}\n")


def get_user_info(cookies: dict[str, str]) -> dict[str, str]:
    info = {
        "openid": cookies.get("openid", "")[:20] + "...",
        "acctype": cookies.get("acctype", "?"),
    }
    tyinfo_cookie = cookies.get("a20161115tyf_tyinfo", "")
    if tyinfo_cookie:
        tyinfo = parse_tyinfo(tyinfo_cookie)
        info["exp_voucher"] = tyinfo.get("exp_voucher", "?")
        info["area"] = tyinfo.get("zf_area", "?")
        info["partition"] = tyinfo.get("zf_partition", "?")
        info["has_tyinfo"] = True
    else:
        info["exp_voucher"] = "?"
        info["has_tyinfo"] = False
    return info
