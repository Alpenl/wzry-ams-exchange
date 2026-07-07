"""CLI: wzry-exchange — 命令行兑换."""

import argparse
import time

from wzry_ams import REWARD_MAP, ExchangeClient


def main():
    ap = argparse.ArgumentParser(description="王者荣耀体验服 AMS 兑换")
    ap.add_argument("-c", "--cookies", default="cookies.txt", help="Cookie 文件")
    ap.add_argument("--reward", "-r", help="奖励编号 (1-6)")
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--interval", "-i", type=float, default=2.0)
    ap.add_argument("--list", action="store_true", help="列出奖励")
    args = ap.parse_args()

    if args.list:
        print(f"{'编号':<5}{'名称':<16}{'体验币':<8}{'flowId'}")
        print("-" * 45)
        for idx, info in REWARD_MAP.items():
            print(f"  {idx:<3}  {info['name']:<14}  {info['cost']:<6}  {info['flowId']}")
        return

    if not args.reward:
        ap.error("需要 --reward/-r 或 --list")
        return

    client = ExchangeClient.from_cookie_file(args.cookies)

    for i in range(args.loop):
        if args.loop > 1:
            print(f"\n── 第 {i+1}/{args.loop} 次 ──")
        result = client.exchange_reward(args.reward)
        status = "[OK]" if result["ok"] else "[FAIL]"
        print(f"{status} {result['msg']}")
        if result.get("package"):
            print(f"      礼包: {result['package']}")
        if i < args.loop - 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
