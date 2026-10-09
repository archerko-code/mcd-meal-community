#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布组合到用户搭配区（写 posts 表）。

用法：
  python write_post.py --ids "1001,2002,3003" --title "低热量且最好吃" \
      --summary "麦香鸡 + 玉米杯 + 零度可乐" \
      --calories 420 --price 16.5 --protein 22 --source user

输出：{"ok": true, "data": {"combo_hash": "...", "action": "inserted|updated", "id": N}}
"""
import sys
import json
import argparse

import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="餐品 ID，逗号分隔（顺序无关）")
    ap.add_argument("--title", required=True)
    ap.add_argument("--summary", required=True, help="组合文字描述")
    ap.add_argument("--calories", type=float, default=None)
    ap.add_argument("--price", type=float, default=None)
    ap.add_argument("--protein", type=float, default=None)
    ap.add_argument("--source", default="user", choices=["user", "system"])
    args = ap.parse_args()

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not ids:
        print(json.dumps({"ok": False, "error": "餐品 ID 为空"}, ensure_ascii=False))
        return 1

    h = store.combo_hash(ids)
    store.init_db()
    conn = store.get_conn()
    try:
        existed = conn.execute(
            "SELECT id FROM posts WHERE combo_hash = ?", (h,)
        ).fetchone()
        ids_json = json.dumps(ids, ensure_ascii=False)
        if existed:
            # 同一组合已存在：更新元信息，不重复插入，保证评论数据天然打通
            conn.execute(
                """UPDATE posts SET title=?, combo_summary=?, product_ids=?,
                   total_calories=?, total_price=?, total_protein=?
                   WHERE combo_hash=?""",
                (args.title, args.summary, ids_json, args.calories, args.price,
                 args.protein, h),
            )
            pid, action = int(existed["id"]), "updated"
        else:
            cur = conn.execute(
                """INSERT INTO posts
                   (combo_hash, title, combo_summary, product_ids, total_calories,
                    total_price, total_protein, source)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (h, args.title, args.summary, ids_json, args.calories, args.price,
                 args.protein, args.source),
            )
            pid, action = int(cur.lastrowid), "inserted"
        conn.commit()
    finally:
        conn.close()

    print(json.dumps(
        {"ok": True, "data": {"combo_hash": h, "action": action, "id": pid}},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
