#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读留言（comments 表）。

用法：
  python query_comments.py --hash <h> [--limit 3]
  python query_comments.py --ids "1001,2002" [--limit 3]
输出：{"ok": true, "data": {"combo_hash","total","stats","comments":[{user_id,content,created_at}]}}
"""
import sys
import json
import argparse

import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", dest="h", default=None)
    ap.add_argument("--ids", default=None)
    ap.add_argument("--limit", type=int, default=3)
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
        rows = conn.execute(
            """SELECT user_id, content, created_at FROM comments
               WHERE combo_hash = ? ORDER BY id DESC LIMIT ?""",
            (h, max(1, args.limit)),
        ).fetchall()
        stats = store.combo_stats(conn, h)
    finally:
        conn.close()

    print(json.dumps(
        {"ok": True,
         "data": {"combo_hash": h, "total": stats["comments"], "stats": stats,
                  "comments": [dict(r) for r in rows]}},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
