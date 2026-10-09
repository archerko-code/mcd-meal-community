#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写留言 / 读留言（comments 表）。

写：
  python write_comment.py --hash <h> --user u_001 --content "这个饱腹感很强"
读：
  python query_comments.py --hash <h> --limit 3
"""
import sys
import json
import argparse

import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", dest="h", default=None)
    ap.add_argument("--ids", default=None)
    ap.add_argument("--user", default="anon")
    ap.add_argument("--content", required=True)
    args = ap.parse_args()

    if not args.h and not args.ids:
        print(json.dumps({"ok": False, "error": "需提供 --hash 或 --ids"},
                         ensure_ascii=False))
        return 1
    h = args.h or store.combo_hash(
        [x.strip() for x in args.ids.split(",") if x.strip()])

    content = args.content.strip()
    if not content:
        print(json.dumps({"ok": False, "error": "留言内容为空"}, ensure_ascii=False))
        return 1

    store.init_db()
    conn = store.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO comments (combo_hash, user_id, content) VALUES (?,?,?)",
            (h, args.user, content),
        )
        conn.commit()
        cid = int(cur.lastrowid)
        stats = store.combo_stats(conn, h)
    finally:
        conn.close()

    print(json.dumps(
        {"ok": True, "data": {"combo_hash": h, "comment_id": cid, "stats": stats}},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
