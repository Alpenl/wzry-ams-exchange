"""Command-line adapter for truthful reward redemption."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .credentials import Credentials, CredentialStore, CredentialStoreError
from .exchange import ExchangeClient, ExchangeReport, OutcomeKind, reward_catalog

ClientFactory = Callable[[Credentials], ExchangeClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="王者荣耀体验服 AMS 兑换")
    parser.add_argument("-c", "--cookies", default="cookies.txt", help="Cookie 文件")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--reward",
        "-r",
        action="append",
        help="奖励编号 (1-6)，可重复指定",
    )
    action.add_argument("--all", action="store_true", help="依次兑换全部奖励")
    action.add_argument("--list", action="store_true", help="列出奖励")
    parser.add_argument("--loop", type=int, default=1, help="重复兑换同一奖励")
    parser.add_argument("--interval", "-i", type=float, default=2.0)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def _print_catalog() -> None:
    print(f"{'编号':<5}{'名称':<16}{'体验币':<8}{'flowId'}")
    print("-" * 45)
    for reward in reward_catalog():
        print(f"  {reward.id:<3}  {reward.name:<14}  {reward.cost:<6}  {reward.flow_id}")


def _marker(kind: OutcomeKind) -> str:
    if kind is OutcomeKind.REDEEMED:
        return "[OK]"
    if kind is OutcomeKind.ALREADY_SATISFIED:
        return "[SATISFIED]"
    return "[FAIL]"


def _print_report(report: ExchangeReport) -> None:
    for outcome in report.outcomes:
        reward_name = outcome.reward.name if outcome.reward else outcome.reward_id
        print(f"{_marker(outcome.kind)} {reward_name}: {outcome.message}")
        if outcome.package:
            print(f"      礼包: {outcome.package}")
    status = "SATISFIED" if report.satisfied else "FAILED"
    print(f"\nPlan: {status} ({len(report.outcomes) - report.failed_count}/{len(report.outcomes)})")


def _append_github_summary(report: ExchangeReport) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(report.to_markdown())
    except OSError as error:
        print(f"[WARN] 无法写入 GitHub Step Summary: {error}", file=sys.stderr)


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        _print_catalog()
        return 0
    if args.loop < 1:
        parser.error("--loop 必须大于 0")
    if args.all and args.loop != 1:
        parser.error("--all 不能与 --loop 一起使用")
    if args.reward and len(args.reward) > 1 and args.loop != 1:
        parser.error("多个 --reward 不能与 --loop 一起使用")

    try:
        credentials = CredentialStore(args.cookies).load(required=True)
        if credentials is None:  # Narrow the optional return for type checkers.
            raise CredentialStoreError(f"Credential file is empty: {args.cookies}")
        credentials.require_exchange_ready()
    except (CredentialStoreError, ValueError) as error:
        print(f"[FAIL] Credential Bundle 不可用: {error}", file=sys.stderr)
        return 2

    factory = client_factory or ExchangeClient
    client = factory(credentials)
    if args.all:
        report = client.redeem_all()
    elif len(args.reward) > 1:
        report = client.redeem_many(args.reward)
    else:
        outcomes = []
        for index in range(args.loop):
            outcomes.append(client.redeem(str(args.reward[0])))
            if index < args.loop - 1:
                time.sleep(args.interval)
        report = ExchangeReport(tuple(outcomes))

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        _print_report(report)
    _append_github_summary(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
