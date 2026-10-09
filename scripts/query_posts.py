#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询用户搭配区（读 posts + 聚合 votes/comments）。

用法：
  python query_posts.py --sort hot|new|price|calories [--limit 10]
                        [--max-calories 600] [--max-price 30] [--min-protein 25]
                        [--with-comments]
输出：{"ok": true, "data": {"total", "sort", "posts": [...]}}
"""
import sys
import json
import argparse

import store
from hot_score import hot_score, hours_since

SORTS = ("hot", "new", "price", "calories")


def fetch_posts(max_calories=None, max_price=None, min_protein=None):
    conn = store.get_conn()
    try:
        sql = "SELECT * FROM posts WHERE 1=1"
        params = []
        # 严格过滤：缺少对应营养/价格数据的组合不能算「满足约束」，
        # 否则 NULL 会绕过热量上限，把未知热量的组合推给减脂用户。
        if max_calories is not None:
            sql += " AND total_calories IS NOT NULL AND total_calories <= ?"
            params.append(max_calories)
        if max_price is not None:
            sql += " AND total_price IS NOT NULL AND total_price <= ?"
            params.append(max_price)
        if min_protein is not None:
            sql += " AND total_protein IS NOT NULL AND total_protein >= ?"
            params.append(min_protein)
        rows = conn.execute(sql, params).fetchall()

        out = []
        for r in rows:
            st = store.combo_stats(conn, r["combo_hash"])
            item = dict(r)
            item.update({
                "up": st["up"], "down": st["down"],
                "net_votes": st["net"], "comment_count": st["comments"],
                "hours": round(hours_since(r["created_at"]), 3),
            })
            item["hot_score"] = hot_score(st["net"], item["hours"])
            out.append(item)
        return out
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sort", default="hot", choices=SORTS)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--max-calories", type=float, default=None)
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--min-protein", type=float, default=None)
    ap.add_argument("--with-comments", action="store_true")
    args = ap.parse_args()

    store.init_db()
    posts = fetch_posts(args.max_calories, args.max_price, args.min_protein)

    key = {
        "hot": lambda p: (-p["hot_score"],),
        "new": lambda p: (p["created_at"],),
        "price": lambda p: (p["total_price"] if p["total_price"] is not None else 1e9,),
        "calories": lambda p: (p["total_calories"] if p["total_calories"] is not None else 1e9,),
    }[args.sort]
    posts.sort(key=key, reverse=(args.sort == "new"))

    total = len(posts)          # 过滤后的总数（翻页判断依据）
    posts = posts[: max(1, args.limit)]

    if args.with_comments:
        conn = store.get_conn()
        try:
            for p in posts:
                rows = conn.execute(
                    """SELECT user_id, content, created_at FROM comments
                       WHERE combo_hash=? ORDER BY id DESC LIMIT 3""",
                    (p["combo_hash"],),
                ).fetchall()
                p["top_comments"] = [dict(x) for x in rows]
        finally:
            conn.close()

    print(json.dumps(
        {"ok": True, "data": {"total": total, "returned": len(posts),
                              "sort": args.sort, "posts": posts}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
