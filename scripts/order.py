#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下单闭环：准备 create-order 载荷 / 记录订单 / 查看与同步状态。

准备载荷（交给 MCP create-order）：
  python order.py --hash <h> --store-code S0001 --order-type takeaway \\
      [--address-id 1] [--user u_1] [--coupon C-20-5] [--confirmed-price 15.5] \\
      [--out order_req.json]

记录订单（把 MCP create-order 的返回回填本地 orders 表）：
  python order.py --record <create_order_result.json> [--hash <h>] [--user u_1]

查看订单：
  python order.py --list [--user u_1] [--limit 10]

同步状态（把 MCP query-order 的返回回填）：
  python order.py --sync --order-no <订单号> --result <query_order_result.json>

虚拟商品（免费冰水）不是真实 SKU，下单前已剥离，改走订单备注。
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


def _post(combo_hash):
    store.init_db()
    conn = store.get_conn()
    try:
        row = conn.execute("SELECT * FROM posts WHERE combo_hash = ?",
                           (combo_hash,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _ids(row):
    try:
        return json.loads(row.get("product_ids") or "[]")
    except Exception:
        return []


def _resolve_post(args):
    """定位组合数据：优先读 posts 表；查不到且给了 --ids 时用命令行现造一条。

    与 checkout.py 同理：combo_search 的结果不落库，下单不该被「先发帖」绑架。
    """
    row = _post(args.hash) if args.hash else None
    if row:
        return row, None
    if not args.ids:
        return None, (
            "posts 表中未找到该组合，且未提供 --ids。二选一：\n"
            "  1) 先发布到搭配区：python3 write_post.py --ids <餐品ID逗号分隔> "
            "--title ... --summary ... --calories ... --price ... --protein ...\n"
            "  2) 直接用 --ids 下单（推荐，不产生社区数据）：\n"
            "     python3 order.py --ids <餐品ID逗号分隔> --store-code ... --user ..."
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
        "total_price": args.price,
    }
    return synthetic, None


def prepare(args):
    row, err = _resolve_post(args)
    if err:
        return _fail(err)
    h = row["combo_hash"]
    ids = _ids(row)
    if not ids:
        return _fail("该组合缺少 product_ids，请改用 --ids 直接指定餐品编码")

    real, virt = store.split_virtual(ids)
    if not real:
        return _fail("剥离虚拟商品后没有真实餐品，无法下单")

    req = {
        "storeCode": args.store_code,
        "orderType": args.order_type,
        "products": [{"productCode": p, "quantity": 1} for p in real],
    }
    if args.address_id:
        req["addressId"] = args.address_id
    if args.coupon:
        req["couponId"] = args.coupon
    if virt:
        req["remark"] = store.VIRTUAL_REMARK

    data = {
        "combo_hash": h,
        "title": row.get("title"),
        "summary": row.get("combo_summary"),
        "real_product_ids": real,
        "stripped_virtual": virt,
        "remark": store.VIRTUAL_REMARK if virt else None,
        "local_estimate": row.get("total_price"),
        "confirmed_price": args.confirmed_price,
        "price_to_charge": (args.confirmed_price
                            if args.confirmed_price is not None
                            else row.get("total_price")),
        "create_order_request": req,
        "next_step": "把 create_order_request 交给 MCP create-order，"
                     "返回存成 json 后用 --record 回填订单号与支付链接",
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        data["written_to"] = args.out
    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
    return 0


def record(args):
    try:
        obj = load_json(args.record)
    except Exception as e:
        return _fail(f"下单结果读取失败：{e}")
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        return _fail("下单结果结构异常，期望一个对象")

    order_no = pick(obj, "orderNo", "orderNumber", "orderId", "orderCode", "id")
    pay_url = pick(obj, "payUrl", "paymentUrl", "payLink", "paymentLink",
                   "url", "link")
    paid = to_num(pick(obj, "payAmount", "actualAmount", "totalAmount",
                       "amount", "totalPrice"))
    status = str(pick(obj, "orderStatus", "status", "state", default="created"))

    row = _post(args.hash) if args.hash else None
    store.init_db()
    conn = store.get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO orders
               (combo_hash, title, user_id, store_code, order_type, product_ids,
                stripped_virtual, remark, coupon_id, local_estimate,
                confirmed_price, paid_amount, order_no, pay_url, status,
                raw_response, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (args.hash, (row or {}).get("title"), args.user, args.store_code,
             args.order_type,
             json.dumps(_ids(row) if row else [], ensure_ascii=False),
             json.dumps(store.split_virtual(_ids(row))[1] if row else [],
                        ensure_ascii=False),
             store.VIRTUAL_REMARK if row and store.split_virtual(_ids(row))[1] else None,
             args.coupon,
             (row or {}).get("total_price"), args.confirmed_price, paid,
             str(order_no) if order_no is not None else None,
             str(pay_url) if pay_url else None, status,
             json.dumps(obj, ensure_ascii=False)[:4000]),
        )
        conn.commit()
        oid = int(cur.lastrowid)
    finally:
        conn.close()

    # ⚠️ 下单前用户确认过金额，这里必须核对：真实扣款价与确认价对不上时
    #    必须显式告警，否则用户会在不知情下多付钱。
    warnings = []
    if order_no is None:
        warnings.append("未解析到订单号，请人工核对 MCP 返回")
    baseline = args.confirmed_price if args.confirmed_price is not None \
        else (row or {}).get("total_price")
    if paid is not None and baseline is not None:
        delta = round(paid - float(baseline), 2)
        if abs(delta) >= 0.01:
            warnings.append(
                f"实付 ¥{paid} 与确认价 ¥{baseline} 相差 ¥{delta}，"
                f"必须先向用户说明差额再让其支付")
    print(json.dumps(
        {"ok": True,
         "data": {"order_id": oid, "order_no": str(order_no) if order_no else None,
                  "pay_url": pay_url, "paid_amount": paid, "status": status,
                  "confirmed_price": baseline,
                  "price_delta": (round(paid - float(baseline), 2)
                                  if (paid is not None and baseline is not None) else None),
                  "warning": ("；".join(warnings) if warnings else None)}},
        ensure_ascii=False, indent=2))
    return 0


def list_orders(args):
    store.init_db()
    conn = store.get_conn()
    try:
        sql = "SELECT * FROM orders"
        params = []
        if args.user:
            sql += " WHERE user_id = ?"
            params.append(args.user)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, args.limit))
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
    print(json.dumps({"ok": True, "data": {"total": len(rows), "orders": rows}},
                     ensure_ascii=False, indent=2))
    return 0


def sync(args):
    if not args.order_no:
        return _fail("--sync 需要 --order-no")
    try:
        obj = load_json(args.result)
    except Exception as e:
        return _fail(f"订单查询结果读取失败：{e}")

    status = str(pick(obj, "orderStatus", "status", "state", default="unknown"))
    paid = to_num(pick(obj, "payAmount", "actualAmount", "totalAmount",
                       "amount", "totalPrice"))

    store.init_db()
    conn = store.get_conn()
    try:
        cur = conn.execute(
            """UPDATE orders SET status = ?, paid_amount = COALESCE(?, paid_amount),
               raw_response = ?, updated_at = datetime('now')
               WHERE order_no = ?""",
            (status, paid, json.dumps(obj, ensure_ascii=False)[:4000],
             str(args.order_no)),
        )
        conn.commit()
        changed = cur.rowcount
    finally:
        conn.close()

    print(json.dumps(
        {"ok": True, "data": {"order_no": str(args.order_no),
                              "status": status, "paid_amount": paid,
                              "rows_updated": changed}},
        ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", default=None,
                    help="组合哈希（与 --ids 二选一）")
    ap.add_argument("--ids", default=None,
                    help="餐品编码逗号分隔（与 --hash 二选一；无需先发布到搭配区）")
    ap.add_argument("--title", default=None, help="仅 --ids 模式下用于展示")
    ap.add_argument("--summary", default=None, help="仅 --ids 模式下用于展示")
    ap.add_argument("--price", type=float, default=None,
                    help="仅 --ids 模式下的本地估算价格，用于与实付对账")
    ap.add_argument("--store-code", default=None)
    ap.add_argument("--order-type", default=DEFAULT_ORDER_TYPE)
    ap.add_argument("--address-id", default=None, help="外送场景的地址 ID")
    ap.add_argument("--coupon", default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--confirmed-price", type=float, default=None,
                    help="checkout --confirm 得到的官方核价，会带入下单请求对照")
    ap.add_argument("--out", default=None)
    ap.add_argument("--record", default=None, help="create-order 返回的 json 文件")
    ap.add_argument("--sync", action="store_true", help="同步订单状态")
    ap.add_argument("--order-no", default=None)
    ap.add_argument("--result", default=None, help="query-order 返回的 json 文件")
    ap.add_argument("--list", action="store_true", help="列出本地订单")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    if args.list:
        return list_orders(args)
    if args.sync:
        return sync(args)
    if args.record:
        if not args.hash and not args.ids:
            return _fail("--record 需要同时提供 --hash 或 --ids")
        if args.ids and not args.hash:
            args.hash = store.combo_hash(
                [x.strip() for x in args.ids.split(",") if x.strip()])
        return record(args)
    if not args.hash and not args.ids:
        return _fail("需要 --hash 或 --ids 来准备下单载荷")
    if args.ids and not args.hash:
        args.hash = store.combo_hash(
            [x.strip() for x in args.ids.split(",") if x.strip()])
    return prepare(args)


if __name__ == "__main__":
    sys.exit(main())
