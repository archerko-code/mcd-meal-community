#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热榜算法：hot_score = (net_votes + 1)^0.8 / (hours_since_post + 2)^0.5

设计说明：
  - 指数 0.8 < 1：高赞组合权重更高但不垄断
  - 时间衰减 0.5 次方：新组合有机会曝光
  - net_votes < 0 时底座取 0.05（不归零，但权重显著降低）
可作为模块 import，也可 CLI 直接算：
  python hot_score.py --net 11 --hours 3
"""
import sys
import json
import argparse
from datetime import datetime

POWER_VOTES = 0.8
POWER_TIME = 0.5
FLOOR = 0.05


def hours_since(created_at, now=None):
    """created_at 为 SQLite datetime('now') 的 UTC 字符串 'YYYY-MM-DD HH:MM:SS'。"""
    now = now or datetime.utcnow()
    if not created_at:
        return 0.0
    try:
        ts = datetime.strptime(str(created_at)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return 0.0
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def hot_score(net_votes, hours):
    base = max(float(net_votes) + 1.0, FLOOR)
    return round((base ** POWER_VOTES) / ((max(float(hours), 0.0) + 2.0) ** POWER_TIME), 6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", type=int, required=True)
    ap.add_argument("--hours", type=float, required=True)
    args = ap.parse_args()
    print(json.dumps({"ok": True, "score": hot_score(args.net, args.hours)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
