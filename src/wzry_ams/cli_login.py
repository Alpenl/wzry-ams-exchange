"""CLI: wzry-login — CDP 扫码登录获取 Cookie."""

import argparse
import sys
import os

from wzry_ams.login import qq_scan_login
from wzry_ams.utils import save_cookies_file, load_cookies_file


def main():
    ap = argparse.ArgumentParser(description="王者荣耀体验服 QQ 扫码登录")
    ap.add_argument("-o", "--output", default="cookies.txt", help="输出文件")
    args = ap.parse_args()

    cookies = qq_scan_login(args.output)

    if not cookies or not cookies.get("openid"):
        print("[!] 登录失败")
        sys.exit(1)

    # 保留旧 tyinfo
    old = load_cookies_file(args.output)
    if "a20161115tyf_tyinfo" not in cookies and "a20161115tyf_tyinfo" in old:
        cookies["a20161115tyf_tyinfo"] = old["a20161115tyf_tyinfo"]

    save_cookies_file(cookies, args.output)
    print(f"\n[OK] {len(cookies)} 个 Cookie → {args.output}")
    for k in ["openid", "access_token", "appid", "acctype", "a20161115tyf_tyinfo"]:
        v = cookies.get(k, "")
        ok = "✓" if v else "✗"
        print(f"  {ok} {k}: {v[:35]}{'...' if len(v)>35 else ''}")


if __name__ == "__main__":
    main()
