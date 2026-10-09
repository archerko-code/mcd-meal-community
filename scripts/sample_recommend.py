#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今日推荐：分层降级 + 加权随机采样 + 20% 探索 + session_seen 去重。

用法：
  python sample_recommend.py [--max-calories 600] [--max-price 30] [--min-protein 25]
                             [--session default] [--exclude]
                             [--reset-seen]

  --exclude   采样后把命中的 hash 写入 session_seen（「换一个」时用）
  --reset-seen 先清空该会话已推列表（新会话开始时用）

输出：
  {"ok": true, "data": {"tier": 1|2|3, "notice": "...", "picked": {...} | null,
                        "candidates": N, "explore": true|false}}
"""
import sys
import json
import random
import argparse

import store
from query_posts import fetch_posts

EXPLORE_RATE = 0.20          # 探索因子
EXPLORE_COMMENT_MAX = 3      # 冷门定义：评论数 < 3
TIER2_RELAX = 1.20           # 第二层：放宽至 120% 热量上限
TOP_K = 12                   # 加权采样池大小


def weighted_pick(items):
    weights = [max(it["hot_score"], 1e-6) for it in items]
    total = sum(weights)
    if total <= 0:
        return random.choice(items)
    r = random.uniform(0, total)
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if acc >= r:
            return it
    return items[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calories", type=float, default=None)
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--min-protein", type=float, default=None)
    ap.add_argument("--session", default="default")
    ap.add_argument("--exclude", action="store_true")
    ap.add_argument("--reset-seen", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.reset_seen:
        store.seen_reset(args.session)
    seen = set(store.seen_hashes(args.session))

    # 分层降级：tier1 严格 -> tier2 放宽 20% -> tier3 忽略全部约束
    #
    # ⚠️ tier2 不能只在「给了热量上限」时才走。用户可能只给 --max-price，
    #    这时 tier1 抽空后直接跳到 tier3，播报「放宽后仍无新组合」——
    #    但 tier2 压根没试过，属于对用户撒谎。这里按「实际给了哪个约束」放宽。
    tier, notice = 1, ""
    pool = [p for p in fetch_posts(args.max_calories, args.max_price, args.min_protein)
            if p["combo_hash"] not in seen]

    relax_cal = round(args.max_calories * TIER2_RELAX, 1) if args.max_calories else None
    relax_price = round(args.max_price * TIER2_RELAX, 1) if args.max_price else None

    def _fmt(x):
        return str(int(x)) if float(x).is_integer() else str(x)

    if not pool and (relax_cal is not None or relax_price is not None):
        tier = 2
        what = []
        if relax_cal is not None:
            what.append(f"热量上限至 {_fmt(relax_cal)}kcal（120%）")
        if relax_price is not None:
            what.append(f"价格上限至 ¥{_fmt(relax_price)}（120%）")
        notice = "严格约束下无新组合，已放宽" + "、".join(what)
        pool = [p for p in fetch_posts(relax_cal, relax_price, args.min_protein)
                if p["combo_hash"] not in seen]

    if not pool and (args.max_calories or args.max_price or args.min_protein):
        tier = 3
        notice = "放宽 20% 后仍无新组合，已忽略全部约束，从全部社区组合中采样"
        pool = [p for p in fetch_posts() if p["combo_hash"] not in seen]

    if not pool:
        if tier >= 2:
            final_notice = "社区组合本轮已全部推完，可回复「重置推荐」重新开始"
        else:
            final_notice = "社区暂无可用组合，可先去搭配区发布一个"
        print(json.dumps(
            {"ok": True,
             "data": {"tier": tier, "notice": final_notice,
                      "picked": None, "candidates": 0, "explore": False}},
            ensure_ascii=False))
        return 0

    # 20% 探索：从冷门组合里随机抽
    explore = False
    cold = [p for p in pool if p["comment_count"] < EXPLORE_COMMENT_MAX
            and p["net_votes"] >= 0]
    if cold and random.random() < EXPLORE_RATE:
        picked = random.choice(cold)
        explore = True
    else:
        pool.sort(key=lambda p: -p["hot_score"])
        picked = weighted_pick(pool[:TOP_K])

    if args.exclude:
        store.seen_add(args.session, [picked["combo_hash"]])

    # 附 Top3 评论
    conn = store.get_conn()
    try:
        rows = conn.execute(
            """SELECT user_id, content, created_at FROM comments
               WHERE combo_hash=? ORDER BY id DESC LIMIT 3""",
            (picked["combo_hash"],),
        ).fetchall()
        picked["top_comments"] = [dict(x) for x in rows]
    finally:
        conn.close()

    print(json.dumps(
        {"ok": True,
         "data": {"tier": tier, "notice": notice, "picked": picked,
                  "candidates": len(pool), "explore": explore}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
