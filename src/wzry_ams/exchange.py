"""AMS 兑换客户端 — 被 CLI 和 Web 复用."""

from typing import Any

import requests

from .utils import (
    AMS_BASE,
    PVP_PAGE,
    REWARD_MAP,
    UA,
    g_tk,
    load_cookies_file,
    parse_cookies,
    parse_tyinfo,
    sdid,
    ts,
)


class ExchangeClient:
    """王者荣耀体验服 AMS 兑换客户端"""

    def __init__(self, cookies: dict[str, str], skey: str = "a1b2c3"):
        self.cookies = cookies
        self.skey = skey

        tyinfo_cookie = cookies.get("a20161115tyf_tyinfo", "")
        if tyinfo_cookie:
            tyinfo = parse_tyinfo(tyinfo_cookie)
            self.iuin = tyinfo.get("zf_openid", "")
            self.ty_openid = tyinfo.get("ty_openid", "")
            self.area = tyinfo.get("zf_area", "1")
            self.partition = tyinfo.get("zf_partition", "1306")
            self.exp_voucher = tyinfo.get("exp_voucher", "?")
        else:
            self.iuin = ""
            self.ty_openid = ""
            self.area = "1"
            self.partition = "1306"
            self.exp_voucher = "?"

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://pvp.qq.com",
            "Referer": PVP_PAGE,
        })
        for k, v in cookies.items():
            self.session.cookies.set(k, v, domain=".qq.com")

    def exchange(self, flow_id: int, activity_id: int = 126433) -> dict[str, Any]:
        """执行一次兑换 (form-encoded POST)."""
        params = {
            "ameVersion": "0.3",
            "sServiceType": "yxzj",
            "iActivityId": str(activity_id),
            "sServiceDepartment": "group_g",
            "sSDID": sdid(),
            "isXhrPost": "true",
        }
        data = {
            "iActivityId": str(activity_id),
            "iFlowId": str(flow_id),
            "g_tk": str(g_tk(self.skey)),
            "sArea": self.area,
            "sPartition": self.partition,
            "sPlatId": "1",
            "iUin": self.iuin,
            "ty_openid": self.ty_openid,
            "sAMSTimestamp": ts(),
        }
        resp = self.session.post(
            AMS_BASE, params=params, data=data, timeout=30,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()

    def exchange_reward(self, reward_index: str) -> dict[str, Any]:
        """按奖励编号兑换."""
        reward = REWARD_MAP.get(str(reward_index))
        if not reward:
            return {"ok": False, "msg": f"无效奖励: {reward_index}"}

        try:
            resp = self.exchange(flow_id=reward["flowId"])
        except Exception as e:
            return {"ok": False, "msg": f"请求异常: {e}", "raw": str(e)}

        mod = resp.get("modRet", {})
        flow = resp.get("flowRet", {})

        success = (mod.get("iRet") == 0 and flow.get("iRet") == "0")
        msg = mod.get("sMsg", flow.get("sMsg", "未知错误"))
        pkg = mod.get("sPackageName",
                      mod.get("jData", {}).get("sPackageName", ""))

        return {
            "ok": success,
            "msg": msg,
            "package": pkg,
            "reward": reward["name"],
            "cost": reward["cost"],
            "raw": resp,
        }

    @classmethod
    def from_cookie_file(cls, path: str, **kw) -> "ExchangeClient":
        cookies = load_cookies_file(path)
        if not cookies:
            raise FileNotFoundError(f"Cookie 文件为空或不存在: {path}")
        return cls(cookies, **kw)

    @classmethod
    def from_cookie_string(cls, raw: str, **kw) -> "ExchangeClient":
        cookies = parse_cookies(raw)
        return cls(cookies, **kw)
