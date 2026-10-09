#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话已推列表查看 / 重置。

  python seen.py --session default            # 查看
  python seen.py --session default --reset    # 清空
"""
import sys
import json
import argparse

import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="default")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.reset:
        store.seen_reset(args.session)
        print(json.dumps({"ok": True, "data": {"session": args.session, "seen": []}},
                         ensure_ascii=False))
        return 0

    h = store.seen_hashes(args.session)
    print(json.dumps(
        {"ok": True, "data": {"session": args.session, "count": len(h), "seen": h}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
