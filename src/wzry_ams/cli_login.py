"""Command-line adapter for QQ scan login."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .credentials import CredentialError, Credentials, CredentialStore
from .login import WELCOME, LoginStatus, scan_login

DISPLAY_FIELDS = (
    "openid",
    "access_token",
    "appid",
    "acctype",
    "iegams_milo_proxylogin_qc",
    "a20161115tyf_tyinfo",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="王者荣耀体验服 QQ 扫码登录")
    parser.add_argument("-o", "--output", default="cookies.txt", help="输出文件")
    parser.add_argument("--timeout", type=int, default=180, help="扫码等待秒数")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(WELCOME)
    print("[*] 请在浏览器中扫码登录 (QQ / 王者营地)")
    print(f"[*] 等待登录... (超时 {args.timeout} 秒)\n")

    result = scan_login(timeout=args.timeout)
    if result.status is not LoginStatus.SUCCESS:
        print(f"[FAIL] {result.message}", file=sys.stderr)
        return 1

    try:
        credentials = Credentials.from_mapping(result.cookies).require_exchange_ready()
        saved = CredentialStore(args.output).replace(credentials)
    except CredentialError as error:
        print(f"[FAIL] Credential Bundle 保存失败: {error}", file=sys.stderr)
        return 2

    print(f"\n[OK] 已安全保存 {len(saved.values)} 个 Cookie -> {args.output}")
    for field in DISPLAY_FIELDS:
        marker = "present" if saved.values.get(field) else "missing"
        print(f"  {field}: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
