#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""帕累托组合搜索：热量 + 预算 + 蛋白质三约束，券后价排序，输出 Top3。

数据来源（由 Agent 调 MCP 后落盘，再喂给本脚本）：
  --nutrition  list-nutrition-foods 返回内容（TOON 文本或 JSON 均可）
  --menu       query-meals 返回内容（JSON）
  --coupons    query-store-coupons 返回内容（JSON，可选）

用法：
  python combo_search.py --nutrition n.json --menu m.json [--coupons c.json] \
      --calories 500 --price 30 --protein 25

输出 JSON：
  {ok, data:{constraints, stats, relaxed, plans:[{rank,label,items,nutrition,
   original_price, final_price, price_is_estimate, coupon, protein_ok}]}}

⚠️ 价格说明：券后价为本地启发式估算，标记 price_is_estimate=true。
   最终以 MCP calculate-price 返回为准（Agent 需对 Top3 复核一次）。
"""
import io
import os
import re
import sys
import json
import argparse
import itertools

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402  （复用 combo_hash，保证与社区区 hash 打通）
from store import pick, to_num, unwrap  # noqa: E402  共享字段探测

# ---------------- TOON 解析（仅营养数据用 TOON 格式） ----------------
def _split_csv(line):
    out, cur, q, i = [], "", False, 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if q and i + 1 < len(line) and line[i + 1] == '"':
                cur += '"'
                i += 2
                continue
            q = not q
        elif ch == "," and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return [x.strip() for x in out]


def parse_toon(text):
    """解析 TOON 表格：以 '{h1,h2,...}:' 开头，逐行一条记录。"""
    header, rows = None, []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("{") and s.endswith("}:"):
            header = [h.strip() for h in s[1:-2].split(",")]
            continue
        if header:
            vals = _split_csv(s)
            if len(vals) < len(header):
                vals += [""] * (len(header) - len(vals))
            rows.append(dict(zip(header, vals[: len(header)])))
    return rows


def load_payload(path):
    raw = sys.stdin.read() if path == "-" else io.open(path, "r", encoding="utf-8").read()
    txt = raw.strip()
    try:
        return json.loads(txt)
    except Exception:
        return txt


def parse_nutrition(path):
    obj = load_payload(path)
    if isinstance(obj, str):
        return parse_toon(obj)
    obj = unwrap(obj)
    if isinstance(obj, str):
        return parse_toon(obj)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        # {产品名: {..}} 形态
        if all(isinstance(v, dict) for v in obj.values()):
            return [dict(v, productName=k) for k, v in obj.items()]
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


# ---------------- 餐品归一化 ----------------
MAIN_KW = ["堡", "汉堡", "卷", "饭", "面", "鸡腿", "鸡排", "麦辣", "巨无霸", "吉士",
           "板烧", "麦香鱼", "麦香鸡", "双吉", "麦满分", "安格斯", "笋卷", "培根"]
SIDE_KW = ["薯条", "薯饼", "沙拉", "玉米杯", "鸡块", "派", "圣代", "麦旋风",
           "洋葱圈", "摇摇薯条", "鸡翅", "苹果片"]
DRINK_KW = ["可乐", "雪碧", "咖啡", "茶", "牛奶", "果汁", "水", "拿铁", "美式",
            "橙汁", "芬达", "柠檬茶", "豆奶", "苏打"]


def classify(name, hint=None):
    n = str(name or "")
    h = str(hint or "")
    for kw in DRINK_KW:
        if kw in n:
            return "drink"
    for kw in SIDE_KW:
        if kw in n:
            return "side"
    for kw in MAIN_KW:
        if kw in n:
            return "main"
    low = h.lower()
    if any(k in low for k in ("drink", "beverage", "饮")):
        return "drink"
    if any(k in low for k in ("side", "snack", "配", "小食")):
        return "side"
    return "main"


def collect_menu_products(obj):
    found = []

    def is_product(d):
        has_name = any(k in d for k in ("productName", "mealName", "skuName", "name"))
        has_id = any(k in d for k in ("productCode", "mealCode", "skuId", "productId", "code", "id"))
        has_money = any(k in d for k in ("price", "currentPrice", "salePrice", "originalPrice", "amount"))
        return has_name and (has_id or has_money)

    def walk(o, ctx=None):
        if isinstance(o, dict):
            cat = pick(o, "categoryName", "category", "categoryId", "classification", default=ctx)
            if is_product(o):
                d = dict(o)
                d["_cat_hint"] = cat
                found.append(d)
                return
            for v in o.values():
                walk(v, cat)
        elif isinstance(o, list):
            for v in o:
                walk(v, ctx)

    walk(unwrap(obj))
    return found


def load_menu(path):
    obj = load_payload(path)
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return []
    products = collect_menu_products(obj)
    out = []
    for p in products:
        name = pick(p, "productName", "mealName", "skuName", "name")
        pid = pick(p, "productCode", "mealCode", "skuId", "productId", "code", "id")
        if not name or pid is None:
            continue
        src = p.get("_cat_hint")
        out.append({
            "id": str(pid),
            "name": str(name),
            "category": classify(name, src),
            "category_raw": str(src) if src else "",
            "price": to_num(pick(p, "price", "currentPrice", "salePrice",
                                 "originalPrice", "amount")),
        })
    return out


# ---------------- 优惠券启发式 ----------------
def _load_coupons_file(path):
    obj = load_payload(path)
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return []
    obj = unwrap(obj)
    raw = []
    if isinstance(obj, list):
        raw = [x for x in obj if isinstance(x, dict)]
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                raw = v
                break

    out = []
    for c in raw:
        cid = pick(c, "couponId", "id", "code", "couponCode")
        name = str(pick(c, "couponName", "name", "title", "couponTitle",
                        default="优惠券"))
        amount = to_num(pick(c, "discountAmount", "amount", "value",
                             "couponAmount", "faceValue", "parValue"))
        discount = to_num(pick(c, "discount", "discountRate", "rate", "zhe"))
        threshold = to_num(pick(c, "threshold", "minAmount", "minSpend",
                                "conditionAmount", "useCondition", "limitAmount")) or 0.0
        cap = to_num(pick(c, "maxDiscount", "discountLimit", "cap"))
        scope_raw = pick(c, "applicableProducts", "productScope", "scope",
                         "applicableScope", "applicableProductCodes", "limitProducts")
        scope_ids = set()
        if isinstance(scope_raw, list):
            for s in scope_raw:
                if isinstance(s, dict):
                    v = pick(s, "productCode", "id", "code")
                else:
                    v = s
                if v is not None:
                    scope_ids.add(str(v))
        elif scope_raw:
            scope_ids = {x.strip() for x in re.split(r"[,，\s]+", str(scope_raw)) if x.strip()}

        desc = str(pick(c, "description", "remark", "ruleDesc", "couponDesc", default=""))
        universal = (not scope_ids) or any(k in (name + desc) for k in
                                           ("全场", "通用", "所有商品", "无门槛通用"))
        out.append({
            "coupon_id": str(cid) if cid is not None else None,
            "name": name,
            "amount": amount,          # 满减面额
            "discount": discount,      # 折扣（7.5 折 / 0.75 两种写法都兼容）
            "threshold": threshold,
            "cap": cap,
            "scope_ids": scope_ids,
            "universal": universal,
        })
    return out


def load_coupons(paths):
    """合并多个券来源（门店券 query-store-coupons / 麦麦省即时券 available-coupons
    / 我的券 query-my-coupons），按 coupon_id 去重。"""
    merged, seen = [], set()
    for p in (paths or []):
        for c in _load_coupons_file(p):
            key = c["coupon_id"] or c["name"]
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)
    return merged


def coupon_final_price(c, price, item_ids):
    """券对某个组合的最终价格；返回 None 表示该券不适用。"""
    if price < c["threshold"] - 1e-6:
        return None
    if not c["universal"] and not (c["scope_ids"] & set(item_ids)):
        return None
    final = price
    if c["amount"]:
        final = price - c["amount"]
    elif c["discount"]:
        d = c["discount"]
        if d > 1:                      # 7.5 折 -> 0.75
            d = d / 10.0 if d <= 10 else 1.0
        final = price * d
        if c["cap"]:
            final = max(final, price - c["cap"])
    else:
        return None
    return max(final, 0.0)


def best_coupon(price, item_ids, coupons):
    best = (price, None)
    for c in coupons:
        fp = coupon_final_price(c, price, item_ids)
        if fp is not None and fp < best[0] - 1e-9:
            best = (fp, c)
    return best


# ---------------- 主流程 ----------------
CAT_CAP = 12          # 每分类剪枝保留的候选数
MAX_COMBOS = 60000    # 组合枚举硬上限

# 内置虚拟赠饮：免费冰水（0 元 0 卡），让「单品 + 冰水」成为最划算的完整一餐。
# 固定 ID 保证跨会话哈希稳定；组合中至少需含一件真实餐品，冰水不会单独成餐。
ICE_WATER_ID = "__ice_water__"
ICE_WATER = {
    "id": ICE_WATER_ID, "name": "冰水", "category": "drink",
    "price": 0.0, "kcal": 0.0, "protein": 0.0, "virtual": True,
}


def build_candidates(products, max_cal, max_price, nutrition, per_cat=CAT_CAP):
    by_name = {}
    for n in nutrition:
        nm = pick(n, "productName", "name")
        if not nm:
            continue
        by_name[str(nm).strip()] = n

    merged, missing = [], []
    for p in products:
        n = by_name.get(p["name"].strip())
        if not n:
            missing.append(p["name"])
            continue
        kcal = to_num(pick(n, "energyKcal", "energy", "calories", "kcal"))
        prot = to_num(pick(n, "protein", "proteinG"))
        if kcal is None or p["price"] is None:
            missing.append(p["name"])
            continue
        if kcal > max_cal:
            continue
        if p["price"] > max_price:
            continue
        merged.append({
            "id": p["id"], "name": p["name"], "category": p["category"],
            "price": p["price"], "kcal": kcal, "protein": prot or 0.0,
        })

    # 每分类剪枝：按价格升序取前 per_cat，并按热量升序取前 per_cat，取并集
    bucket = {}
    for m in merged:
        bucket.setdefault(m["category"], []).append(m)
    kept = {}
    for cat, items in bucket.items():
        by_price = sorted(items, key=lambda x: x["price"])[:per_cat]
        by_kcal = sorted(items, key=lambda x: -x["kcal"])[:per_cat]  # 热量高的通常更饱腹
        seen_ids, union = set(), []
        for it in by_price + by_kcal:
            if it["id"] not in seen_ids:
                seen_ids.add(it["id"])
                union.append(it)
        kept[cat] = union
    return kept, missing


def enumerate_combos(kept, max_cal, max_price, min_items=2):
    mains = kept.get("main", [])
    sides = [None] + kept.get("side", [])
    drinks = [None] + kept.get("drink", [])

    combos = []
    # 结构：主餐1~2 + 配餐0~1 + 饮品0~1，总数 1~3
    main_pairs = [(m,) for m in mains]
    main_pairs += [(a, b) for a, b in itertools.combinations(mains, 2)]

    for mp in main_pairs:
        for s in sides:
            for d in drinks:
                items = list(mp) + ([s] if s else []) + ([d] if d else [])
                if not (min_items <= len(items) <= 3):
                    continue
                if not any(not it.get("virtual") for it in items):
                    continue  # 至少一件真实餐品，避免「冰水」单独成餐
                cal = sum(i["kcal"] for i in items)
                if cal > max_cal + 1e-6:
                    continue
                price = round(sum(i["price"] for i in items), 2)
                if price > max_price + 1e-6:
                    continue
                combos.append({
                    "items": items,
                    "kcal": round(cal, 1),
                    "protein": round(sum(i["protein"] for i in items), 1),
                    "price": price,
                })
                if len(combos) >= MAX_COMBOS:
                    return combos
    return combos


def attach_ice_water(combos, ice_item, max_items=3):
    """给不含饮品的组合自动补上免费冰水（0 元 0 卡，热量与价格不变）。

    这样每个方案都是一份完整的「一餐」，也避免「单品」与「单品+冰水」
    同时出现在推荐里。件数已达上限时不补。
    """
    out = []
    for c in combos:
        has_drink = any(i["category"] == "drink" for i in c["items"])
        if has_drink or len(c["items"]) >= max_items:
            out.append(c)
            continue
        out.append({**c, "items": list(c["items"]) + [dict(ice_item)]})
    return out


def dedupe(combos):
    seen, out = set(), []
    for c in combos:
        key = tuple(sorted(i["id"] for i in c["items"]))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nutrition", required=True)
    ap.add_argument("--menu", required=True)
    ap.add_argument("--coupons", nargs="*", default=None,
                    help="券来源文件，可传多个（门店券 / 麦麦省即时券 / 我的券）")
    ap.add_argument("--calories", type=float, default=600.0)
    ap.add_argument("--price", type=float, default=30.0)
    ap.add_argument("--protein", type=float, default=None)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--min-items", type=int, default=1,
                    help="组合最少件数，默认 1（允许单品）")
    ap.add_argument("--no-ice-water", action="store_true",
                    help="关闭内置的免费「冰水」赠饮选项")
    args = ap.parse_args()

    nutrition = parse_nutrition(args.nutrition)
    products = load_menu(args.menu)
    coupons = load_coupons(args.coupons)
    ice_water = [] if args.no_ice_water else [dict(ICE_WATER)]

    if not products:
        print(json.dumps({"ok": False, "error": "菜单解析为空，请检查 query-meals 返回结构"},
                         ensure_ascii=False))
        return 1

    # 分层降级：严格约束 -> 放宽 10% 热量
    attempts = [
        (args.calories, False, ""),
        (round(args.calories * 1.10, 1), True,
         f"严格约束下无可行组合，已放宽热量上限至 "
         f"{round(args.calories * 1.10, 1)}kcal（110%）"),
    ]
    combos, kept, missing = [], {}, []
    max_cal, relaxed, notice = args.calories, False, ""
    for cap, rlx, nt in attempts:
        kept, missing = build_candidates(products, cap, args.price, nutrition)
        raw = enumerate_combos(kept, cap, args.price, args.min_items)
        if ice_water:
            raw = attach_ice_water(raw, ICE_WATER, 3)
        combos = dedupe(raw)
        if combos:
            max_cal, relaxed, notice = cap, rlx, (nt if rlx else "")
            break

    if not combos:
        print(json.dumps(
            {"ok": True,
             "data": {"constraints": {"calories": args.calories, "price": args.price,
                                      "protein": args.protein,
                                      "min_items": args.min_items},
                      "relaxed": relaxed, "notice": notice, "plans": [],
                      "stats": {"menu_items": len(products), "combos": 0},
                      "error": "放宽 10% 热量后仍无可行组合，"
                               "建议放宽预算上限或换门店"}},
            ensure_ascii=False, indent=2))
        return 0

    # 券后价
    for c in combos:
        ids = [i["id"] for i in c["items"]]
        fp, coupon = best_coupon(c["price"], ids, coupons)
        c["final_price"] = round(fp, 2)
        c["coupon"] = coupon
        c["kcal_per_yuan"] = round(c["kcal"] / c["final_price"], 2) if c["final_price"] > 0 else None

    picks = []
    used = set()

    def take_first(label, cands):
        """按候选顺序取第一个未被占用的组合。"""
        for cand in cands:
            key = tuple(sorted(i["id"] for i in cand["items"]))
            if key in used:
                continue
            used.add(key)
            picks.append({"label": label, "combo": cand})
            return True
        return False

    # 同价同热量时优先件数多的（即带免费冰水的那份更完整）
    by_price = sorted(combos, key=lambda c: (c["final_price"], c["kcal"],
                                             -len(c["items"])))
    by_kcal = sorted(combos, key=lambda c: (c["kcal"], c["final_price"],
                                            -len(c["items"])))

    take_first("最低价方案", by_price)
    take_first("热量最低方案", by_kcal)
    if args.protein is not None:
        ok = sorted((c for c in combos if c["protein"] >= args.protein),
                    key=lambda c: (-c["protein"], c["final_price"], c["kcal"],
                                   -len(c["items"])))
        take_first("蛋白质达标", ok)
    for c in by_price:
        if len(picks) >= args.top:
            break
        take_first("备选方案", [c])

    plans = []
    for i, p in enumerate(picks[: args.top], 1):
        c = p["combo"]
        items = [{"id": x["id"], "name": x["name"], "category": x["category"],
                  "kcal": x["kcal"], "protein": x["protein"], "price": x["price"],
                  "virtual": bool(x.get("virtual"))}
                 for x in c["items"]]
        plans.append({
            "rank": i,
            "label": p["label"],
            "items": items,
            "product_ids": [x["id"] for x in items],
            "combo_hash": store.combo_hash([x["id"] for x in items]),
            "summary": " + ".join(x["name"] for x in items),
            "nutrition": {"calories": c["kcal"], "protein": c["protein"]},
            "original_price": c["price"],
            "final_price": c["final_price"],
            "price_is_estimate": True,
            "coupon": ({"coupon_id": c["coupon"]["coupon_id"], "name": c["coupon"]["name"]}
                       if c["coupon"] else None),
            "kcal_per_yuan": c["kcal_per_yuan"],
            "protein_ok": (args.protein is None or c["protein"] >= args.protein),
        })

    print(json.dumps(
        {"ok": True,
         "data": {
             "constraints": {"calories": args.calories, "price": args.price,
                             "protein": args.protein,
                             "min_items": args.min_items,
                             "ice_water": bool(ice_water)},
             "relaxed": relaxed,
             "notice": notice,
             "effective_calories_cap": max_cal,
             "stats": {"menu_items": len(products), "candidates": sum(len(v) for v in kept.values()),
                       "combos": len(combos), "coupons": len(coupons),
                       "unmatched_nutrition": sorted(set(missing))[:20]},
             "plans": plans}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
