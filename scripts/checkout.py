#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核价闭环：把推荐/社区里的组合推到 MCP 精确核价。

本脚本不直接调 MCP，负责「准备请求」和「应用结果」两端：

Step 1  准备核价载荷
  python checkout.py --hash <combo_hash> [--store-code S0001] \\
      [--order-type takeaway] [--coupon C-20-5] [--out price_req.json]

  输出里的 calculate_price_request 直接交给 MCP calculate-price。

Step 2  应用官方核价结果
  python checkout.py --hash <combo_hash> --confirm <calculate_price_result.json>

  对比本地估算与官方核价、输出差额，并声明以官方为准。

虚拟商品（免费冰水）不是真实 SKU，已在请求里剥离，改走订单备注。
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402
from store import pick, to_num, load_json  # noqa: E402

DEFAULT_ORDER_TYPE = "takeaway"


def _fail(msg):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    return 1


def _load_post(combo_hash):
    store.init_db()
    conn = store.get_conn()
    try:
        row = conn.execute("SELECT * FROM posts WHERE combo_hash = ?",
                           (combo_hash,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _product_ids(row):
    try:
        return json.loads(row.get("product_ids") or "[]")
    except Exception:
        return []


def _resolve_post(args):
    """定位组合数据：优先读 posts 表；查不到且给了 --ids 时用命令行现造一条。

    根因说明：combo_search.py 只做计算、不落库，它返回的 combo_hash 在 posts 表
    里并不存在。如果 checkout 强制要求先发布到搭配区，用户说「就买方案1」就会
    在下单链路上撞墙。核价只需要商品编码，不需要发帖——所以 --ids 直通。
    """
    row = _load_post(args.hash) if args.hash else None
    if row:
        return row, None
    if not args.ids:
        return None, (
            "posts 表中未找到该组合，且未提供 --ids。二选一：\n"
            "  1) 先发布到搭配区：python3 write_post.py --ids <餐品ID逗号分隔> "
            "--title ... --summary ... --calories ... --price ... --protein ...\n"
            "  2) 直接用 --ids 核价（推荐，不产生社区数据）：\n"
            "     python3 checkout.py --ids <餐品ID逗号分隔> --price <估算价> "
            "--store-code ... --out ..."
        )
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not ids:
        return None, "--ids 解析后为空"
    h = store.combo_hash(ids)
    synthetic = {
        "combo_hash": h,
        "title": args.title or "、".join(ids),
        "combo_summary": args.summary or " + ".join(ids),
        "product_ids": json.dumps(ids, ensure_ascii=False),
        "total_calories": args.calories,
        "total_price": args.price,
        "total_protein": args.protein,
        "source": "adhoc",
    }
    return synthetic, None


def prepare(args):
    row, err = _resolve_post(args)
    if err:
        return _fail(err)
    h = row["combo_hash"]
    ids = _product_ids(row)
    if not ids:
        return _fail("该组合缺少 product_ids，请改用 --ids 直接指定餐品编码")

    real, virt = store.split_virtual(ids)
    if not real:
        return _fail("该组合剥离虚拟商品后没有真实餐品，无法核价")

    req = {
        "storeCode": args.store_code,
        "orderType": args.order_type,
        "products": [{"productCode": p, "quantity": 1} for p in real],
    }
    if args.coupon:
        req["couponId"] = args.coupon

    data = {
        "combo_hash": h,
        "title": row.get("title"),
        "summary": row.get("combo_summary"),
        "real_product_ids": real,
        "stripped_virtual": virt,
        "remark": store.VIRTUAL_REMARK if virt else None,
        "remark_reason": ("免费冰水不是真实 SKU，进不了订单商品列表，"
                          "必须改走订单备注" if virt else None),
        "local_estimate": row.get("total_price"),
        "local_estimate_note": "发布者当时算出的价格，仅供参考",
        "calculate_price_request": req,
        "next_step": "把 calculate_price_request 交给 MCP calculate-price，"
                     "返回存成 json 后用 --confirm 回填",
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        data["written_to"] = args.out
    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
    return 0


def confirm(args):
    row, err = _resolve_post(args)
    if err:
        return _fail(err)
    h = row["combo_hash"]
    try:
        obj = load_json(args.confirm)
    except Exception as e:
        return _fail(f"核价结果读取失败：{e}")

    pay = to_num(pick(obj, "payAmount", "actualAmount", "payPrice",
                      "totalAmount", "amount", "totalPrice"))
    discount = to_num(pick(obj, "discountAmount", "couponAmount",
                           "preferentialAmount", "discount"))
    # ⚠️ 原价只能从专用键取，绝不能落到 totalAmount / amount 这类歧义键——
    #    很多接口的 totalAmount 是「应付价」，拿它当原价会得出假的优惠金额。
    origin = to_num(pick(obj, "originAmount", "totalOriginAmount",
                         "originalAmount", "listPrice", "originalPrice"))
    if origin is None and pay is not None and discount is not None:
        origin = round(pay + discount, 2)   # 用应付价 + 优惠额反推，至少数量关系自洽
    if pay is None:
        return _fail("无法从核价结果解析出应付金额，请核对 MCP 返回结构")

    local = row.get("total_price")
    diff = round(pay - local, 2) if local is not None else None
    if diff is None:
        verdict = "无本地估算可比"
    elif abs(diff) < 0.005:
        verdict = "一致"
    elif diff < 0:
        verdict = "官方更低"
    else:
        verdict = "官方更高"

    data = {
        "combo_hash": h,
        "summary": row.get("combo_summary"),
        "local_estimate": local,
        "official": {"pay_amount": pay, "origin_amount": origin,
                     "discount": discount},
        "diff": diff,
        "verdict": verdict,
        "price_is_estimate": False,
        "advice": "本地估算与官方核价一致，直接展示官方价"
                  if verdict == "一致" else
                  "以官方核价为准，展示时用 official.pay_amount 覆盖本地估算",
    }
    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", default=None,
                    help="组合哈希（与 --ids 二选一；posts 表里已存在时用这个）")
    ap.add_argument("--ids", default=None,
                    help="餐品编码逗号分隔（与 --hash 二选一；无需先发布到搭配区）")
    ap.add_argument("--title", default=None, help="仅 --ids 模式下用于展示")
    ap.add_argument("--summary", default=None, help="仅 --ids 模式下用于展示")
    ap.add_argument("--calories", type=float, default=None, help="仅 --ids 模式下的本地估算热量")
    ap.add_argument("--price", type=float, default=None, help="仅 --ids 模式下的本地估算价格")
    ap.add_argument("--protein", type=float, default=None, help="仅 --ids 模式下的本地估算蛋白质")
    ap.add_argument("--store-code", default=None)
    ap.add_argument("--order-type", default=DEFAULT_ORDER_TYPE,
                    help="就餐方式，取值以 MCP 实际定义为准")
    ap.add_argument("--coupon", default=None, help="指定券 couponId，留空则不带券核价")
    ap.add_argument("--out", default=None, help="把载荷写到文件")
    ap.add_argument("--confirm", default=None,
                    help="calculate-price 返回的 json 文件，用于回填官方价")
    args = ap.parse_args()
    if not args.hash and not args.ids:
        return _fail("需要 --hash 或 --ids 之一")
    if args.hash and args.ids:
        # 两者都给时以 --ids 为准（搜索结果直接下单的场景）
        args.hash = store.combo_hash(
            [x.strip() for x in args.ids.split(",") if x.strip()])
    return confirm(args) if args.confirm else prepare(args)


if __name__ == "__main__":
    sys.exit(main())
