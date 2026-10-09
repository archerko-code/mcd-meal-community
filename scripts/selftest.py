#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端自测：覆盖策划案测试场景 1-10 + 新增行为。

覆盖点：
  组合搜索：免费冰水自动附加 / 允许单品 / 多券来源合并 / combo_hash 稳定
  社区：发布去重更新 / 顶踩改票防重复 / 评论隔离
  采样：换一个不重复 / 分层降级 tier>=2 / 池耗尽返回 null
  排序：hot / new / price / calories

用法：
  python3 selftest.py            # 独立临时库，不影响正式数据

退出码 0 = 全部通过，1 = 有失败项。
"""
import os
import re
import sys
import json
import tempfile
import subprocess

for _s in ("stdout", "stderr"):
    _st = getattr(sys, _s, None)
    if _st is not None and hasattr(_st, "reconfigure"):
        try:
            _st.reconfigure(encoding="utf-8")
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DEMO = os.path.join(SKILL, "data", "demo")
PY = sys.executable
NUT = os.path.join(DEMO, "nutrition.toon.txt")
MENU = os.path.join(DEMO, "menu.json")
COUPONS = os.path.join(DEMO, "coupons.json")

FAILS = []


def run(script, *args, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    if env:
        e.update(env)
    p = subprocess.run([PY, os.path.join(HERE, script)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8",
                       env=e, cwd=HERE)
    if p.returncode != 0:
        FAILS.append(f"{script} 退出码 {p.returncode}: {p.stderr.strip()[:300]}")
        return None
    try:
        return json.loads(p.stdout)
    except Exception as e:
        FAILS.append(f"{script} 输出非 JSON: {p.stdout[:200]} ({e})")
        return None


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="mcd_selftest_")
    env = {"MCD_DB_PATH": os.path.join(tmp, "t.db"),
           "MCD_SEEN_PATH": os.path.join(tmp, "t_seen.json")}

    print("== 组合搜索：免费冰水自动附加 ==")
    s = run("combo_search.py", "--nutrition", NUT, "--menu", MENU,
            "--coupons", COUPONS, "--calories", "500", "--price", "30",
            "--protein", "25")["data"]
    plans = s["plans"]
    check("输出 3 个方案", len(plans) == 3, f"got {len(plans)}")
    has_drink = all(any(i["category"] == "drink" for i in p["items"]) for p in plans)
    check("每个方案都含饮品（冰水自动补齐）", has_drink,
          " | ".join(p["summary"] for p in plans))
    check("冰水价格为 0", all(
        i["price"] == 0 for p in plans for i in p["items"]
        if i["name"] == "冰水"))
    check("三项 label 齐全",
          {p["label"] for p in plans} == {"最低价方案", "热量最低方案", "蛋白质达标"},
          str(sorted(p["label"] for p in plans)))
    check("最低价方案券后最低",
          plans[0]["final_price"] == min(p["final_price"] for p in plans))
    check("热量最低方案热量最低",
          plans[1]["nutrition"]["calories"] == min(p["nutrition"]["calories"] for p in plans))
    check("蛋白质达标方案满足下限",
          plans[2]["nutrition"]["protein"] >= 25)
    check("combo_hash 为 16 位十六进制",
          bool(re.fullmatch(r"[0-9a-f]{16}", plans[0]["combo_hash"] or "")),
          plans[0]["combo_hash"])

    print("== 组合搜索：允许单品（--no-ice-water）==")
    s2 = run("combo_search.py", "--nutrition", NUT, "--menu", MENU,
             "--coupons", COUPONS, "--calories", "500", "--price", "30",
             "--no-ice-water")["data"]
    single = [p for p in s2["plans"] if len(p["items"]) == 1]
    check("出现单件方案", bool(single),
          single[0]["summary"] if single else "未见单件")

    print("== 组合搜索：多券来源合并 ==")
    extra = os.path.join(tmp, "extra_coupon.json")
    with open(extra, "w", encoding="utf-8") as f:
        json.dump({"data": {"coupons": [
            {"couponId": "C-EXTRA", "couponName": "额外券满25减6",
             "discountAmount": 6, "threshold": 25, "description": "全场通用"}]}},
            f, ensure_ascii=False)
    s3 = run("combo_search.py", "--nutrition", NUT, "--menu", MENU,
             "--coupons", COUPONS, extra, "--calories", "500", "--price", "30")["data"]
    check("两个券文件被合并（3+1=4）", s3["stats"]["coupons"] == 4,
          str(s3["stats"]["coupons"]))

    print("== 组合搜索：无可行组合时放宽并告知 ==")
    s4 = run("combo_search.py", "--nutrition", NUT, "--menu", MENU,
             "--calories", "60", "--price", "3")["data"]
    if s4["plans"]:
        check("放宽后仍给出方案且 relaxed=true", s4["relaxed"] and bool(s4["notice"]),
              s4["notice"][:40])
    else:
        check("确实无解时返回 error 而非编造", bool(s4.get("error")), s4.get("error", "")[:40])

    print("== 社区：发布 / 顶踩 / 评论 ==")
    ids_a = ",".join(plans[0]["product_ids"])
    pA = run("write_post.py", "--ids", ids_a, "--title", "测试方案A",
             "--summary", plans[0]["summary"], "--calories", "1", "--price", "1",
             "--source", "system", env=env)
    check("发布写入 posts", pA and pA["data"]["action"] == "inserted")
    hA = pA["data"]["combo_hash"]
    check("发布 hash 与搜索 hash 一致（场景10 打通）",
          hA == plans[0]["combo_hash"], f"{hA} vs {plans[0]['combo_hash']}")

    rev = ",".join(reversed(plans[0]["product_ids"]))
    pA2 = run("write_post.py", "--ids", rev, "--title", "测试方案A",
              "--summary", plans[0]["summary"], env=env)
    check("乱序 ids 复用同一条记录", pA2["data"]["action"] == "updated"
          and pA2["data"]["id"] == pA["data"]["id"])

    run("vote.py", "--hash", hA, "--user", "u1", "--rating", "1", env=env)
    v = run("vote.py", "--hash", hA, "--user", "u1", "--rating", "1", env=env)
    check("重复顶不叠加", v["data"]["action"] == "unchanged"
          and v["data"]["stats"]["up"] == 1)
    v = run("vote.py", "--hash", hA, "--user", "u1", "--rating", "-1", env=env)
    check("改票生效且不新增行", v["data"]["stats"]["up"] == 0
          and v["data"]["stats"]["down"] == 1)

    run("write_comment.py", "--hash", hA, "--user", "u2", "--content", "很好吃", env=env)
    qc = run("query_comments.py", "--hash", hA, env=env)
    check("评论可读回", qc["data"]["total"] == 1)
    qc2 = run("query_comments.py", "--hash", plans[1]["combo_hash"], env=env)
    check("评论按组合隔离", qc2["data"]["total"] == 0)

    print("== 采样：换一个不重复 + 分层降级 ==")
    for i, p in enumerate(plans):
        run("write_post.py", "--ids", ",".join(p["product_ids"]),
            "--title", f"方案{i}", "--summary", p["summary"],
            "--calories", p["nutrition"]["calories"], "--price", p["final_price"],
            "--protein", p["nutrition"]["protein"], env=env)
    seen, tiers = [], set()
    for i in range(3):
        d = run("sample_recommend.py", "--max-calories", "600", "--max-price", "30",
                "--session", "st", "--exclude", env=env)["data"]
        tiers.add(d["tier"])
        if d["picked"]:
            seen.append(d["picked"]["combo_hash"])
    check("连续3次推送互不重复", len(seen) == 3 and len(set(seen)) == 3, str(seen))

    d_tight = run("sample_recommend.py", "--max-calories", "100", "--max-price", "5",
                  "--session", "st2", "--exclude", env=env)["data"]
    check("约束过紧时进入 tier>=2 且给出说明",
          d_tight["tier"] >= 2 and (d_tight["picked"] is None or d_tight["notice"]),
          f"tier={d_tight['tier']}")

    print("== 下单闭环：核价 -> 下单 -> 记录 -> 同步 ==")
    ck = run("checkout.py", "--hash", hA, "--store-code", "S0001",
             "--order-type", "takeaway", env=env)
    check("核价载荷生成", ck and ck["ok"])
    cd = ck["data"]
    check("虚拟冰水被剥离出商品列表",
          "__ice_water__" not in cd["real_product_ids"]
          and cd["stripped_virtual"] == ["冰水"], str(cd["stripped_virtual"]))
    check("给出订单备注替代冰水", bool(cd["remark"]), cd["remark"] or "（空）")
    check("核价载荷只含真实餐品",
          cd["calculate_price_request"]["products"]
          and all("__ice_water__" not in p["productCode"]
                  for p in cd["calculate_price_request"]["products"]))

    price_res = os.path.join(tmp, "price.json")
    with open(price_res, "w", encoding="utf-8") as f:
        json.dump({"success": True, "data": {
            "payAmount": cd["local_estimate"] + 1.0,
            "originAmount": cd["local_estimate"] + 5.0,
            "discountAmount": 4.0}}, f, ensure_ascii=False)
    cf = run("checkout.py", "--hash", hA, "--confirm", price_res, env=env)
    check("官方核价与本地估算差异可计算", cf and cf["data"]["diff"] == 1.0,
          str(cf["data"]["diff"]) if cf else "")
    check("回填后声明不再使用估算价", cf and cf["data"]["price_is_estimate"] is False)

    prep = run("order.py", "--hash", hA, "--store-code", "S0001",
               "--order-type", "takeaway", "--user", "u1", env=env)
    check("下单载荷生成", prep and prep["ok"])
    check("下单载荷把冰水写进备注",
          prep["data"]["create_order_request"].get("remark")
          == "请另附一杯免费冰水，谢谢",
          str(prep["data"]["create_order_request"].get("remark")))

    order_res = os.path.join(tmp, "order.json")
    with open(order_res, "w", encoding="utf-8") as f:
        json.dump({"data": {"orderNo": "MCD20261009001",
                            "payUrl": "https://mcd.cn/pay/xxx",
                            "payAmount": 16.5,
                            "orderStatus": "pending_payment"}}, f, ensure_ascii=False)
    rec = run("order.py", "--record", order_res, "--hash", hA, "--user", "u1",
              "--store-code", "S0001", env=env)
    check("订单写入 orders 表", rec and rec["data"]["order_no"] == "MCD20261009001",
          str(rec["data"]) if rec else "")
    check("支付链接被抓取", rec and rec["data"]["pay_url"])

    lst = run("order.py", "--list", "--user", "u1", env=env)
    check("订单可列出", lst and lst["data"]["total"] == 1)

    sync_res = os.path.join(tmp, "sync.json")
    with open(sync_res, "w", encoding="utf-8") as f:
        json.dump({"data": {"orderStatus": "paid", "payAmount": 16.5}}, f,
                  ensure_ascii=False)
    sy = run("order.py", "--sync", "--order-no", "MCD20261009001",
             "--result", sync_res, env=env)
    check("订单状态可同步", sy and sy["data"]["status"] == "paid"
          and sy["data"]["rows_updated"] == 1,
          str(sy["data"]) if sy else "")
    lst2 = run("order.py", "--list", env=env)
    check("同步后本地状态已更新",
          lst2["data"]["orders"][0]["status"] == "paid",
          lst2["data"]["orders"][0]["status"])

    print("== 排序 ==")
    for sort in ("hot", "new", "price", "calories"):
        r = run("query_posts.py", "--sort", sort, "--limit", "5", env=env)
        check(f"sort={sort}", r and r["ok"] and r["data"]["total"] == 3,
              f"total={r['data']['total'] if r else '?'}")

    print()
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
