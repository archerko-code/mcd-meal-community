#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""顶 / 踩（写 votes 表，UNIQUE(combo_hash,user_id) 防重复）。

用法：
  python vote.py --hash <combo_hash> --user u_001 --rating 1     # 顶
  python vote.py --hash <combo_hash> --user u_001 --rating -1    # 踩
  python vote.py --ids "1001,2002,3003" --user u_001 --rating 1  # 用 ids 也行

重复投票行为：覆盖旧票（同一用户改票），而非报错。
输出：{"ok": true, "data": {"combo_hash","rating","action","stats":{...}}}
"""
import sys
import json
import argparse

import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", dest="h", default=None)
    ap.add_argument("--ids", default=None, help="餐品 ID，逗号分隔（与 --hash 二选一）")
    ap.add_argument("--user", required=True, help="用户标识（会话内稳定即可）")
    ap.add_argument("--rating", type=int, required=True, choices=[1, -1])
    args = ap.parse_args()

    if not args.h and not args.ids:
        print(json.dumps({"ok": False, "error": "需提供 --hash 或 --ids"},
                         ensure_ascii=False))
        return 1
    h = args.h or store.combo_hash(
        [x.strip() for x in args.ids.split(",") if x.strip()])

    store.init_db()
    conn = store.get_conn()
    try:
        existed = conn.execute(
            "SELECT id, rating FROM votes WHERE combo_hash=? AND user_id=?",
            (h, args.user),
        ).fetchone()
        if existed:
            if int(existed["rating"]) == args.rating:
                action = "unchanged"
            else:
                conn.execute(
                    "UPDATE votes SET rating=?, created_at=datetime('now') WHERE id=?",
                    (args.rating, int(existed["id"])),
                )
                action = "changed"
        else:
            conn.execute(
                "INSERT INTO votes (combo_hash, user_id, rating) VALUES (?,?,?)",
                (h, args.user, args.rating),
            )
            action = "inserted"
        conn.commit()
        stats = store.combo_stats(conn, h)
    finally:
        conn.close()

    print(json.dumps(
        {"ok": True,
         "data": {"combo_hash": h, "rating": args.rating,
                  "action": action, "stats": stats}},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
