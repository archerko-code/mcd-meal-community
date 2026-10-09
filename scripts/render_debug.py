#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成可视化验证页 data/verify.html。

在**独立演示库**上跑完整链路（不碰 data/community.db）：
  组合搜索 -> 发布到搭配区 -> 顶/踩 -> 评论 -> 今日推荐采样 N 次 -> 汇总渲染

用法：
  python render_debug.py [--samples 6] [--out ../data/verify.html] [--live]

  --live  改用真实库 data/community.db（只读渲染，不造演示数据）
"""
import os
import sys
import json
import random
import sqlite3
import argparse
import tempfile
import subprocess
from datetime import datetime

for _s in ("stdout", "stderr"):
    _st = getattr(sys, _s, None)
    if _st is not None and hasattr(_st, "reconfigure"):
        try:
            _st.reconfigure(encoding="utf-8")
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DATA = os.path.join(SKILL, "data")
DEMO = os.path.join(DATA, "demo")
PY = sys.executable

DEMO_TITLES = [
    "低热量且最低价", "热量最低的轻食", "高蛋白且便宜", "减脂午餐首选",
    "练后补蛋白", "穷鬼套餐", "轻断食友好", "饱腹感拉满", "零度快乐餐",
]

DEMO_USERS = ["u_lin", "u_zhao", "u_qian", "u_sun", "u_zhou",
              "u_wu", "u_zheng", "u_wang"]

COMMENT_POOL = [
    "冰水是免费的别忘了拿，饱腹感比想象中强",
    "这个组合蛋白质够，减脂期我就吃这个",
    "性价比无敌，券后比单点还便宜",
    "热量最低但蛋白偏少，我会再加个麦乐鸡",
    "汉堡类券用上了，比原价省一截",
    "练后补蛋白够用，碳水也不高",
    "别点可乐，换零度直接省 210 大卡",
    "这搭配吃了一周，掉秤很稳",
    "沙拉的酱别全倒，热量差挺多",
    "两个人分着吃刚好，一个人有点撑",
]

# 多组约束各跑一次搜索，凑出更多样的社区组合
SEARCH_RUNS = [
    (500, 30, 25),
    (420, 22, None),
    (700, 40, 30),
    (600, 25, None),
]


def run(script, *args, env=None):
    """以子进程方式调用同目录脚本，返回解析后的 JSON。"""
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    if env:
        e.update(env)
    p = subprocess.run([PY, os.path.join(HERE, script)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8",
                       env=e, cwd=HERE)
    if p.returncode != 0:
        raise RuntimeError(f"{script} 执行失败：{p.stderr[:500]}")
    return json.loads(p.stdout)


def demo_order_flow(env, plan):
    """用模拟的 MCP 返回跑一遍「核价 -> 下单 -> 同步」，供页面展示。"""
    if not plan:
        return None
    tmpd = tempfile.mkdtemp(prefix="mcd_order_")
    h = plan["combo_hash"]

    ck = run("checkout.py", "--hash", h, "--store-code", "S0001",
             "--order-type", "takeaway", env=env)["data"]

    price_file = os.path.join(tmpd, "price.json")
    with open(price_file, "w", encoding="utf-8") as f:
        json.dump({"success": True, "data": {
            "payAmount": round((ck["local_estimate"] or 0) - 5.0, 2),
            "originAmount": ck["local_estimate"],
            "discountAmount": 5.0}}, f, ensure_ascii=False)
    cf = run("checkout.py", "--hash", h, "--confirm", price_file, env=env)["data"]

    prep = run("order.py", "--hash", h, "--store-code", "S0001",
               "--order-type", "takeaway", "--user", "demo_user",
               "--confirmed-price", cf["official"]["pay_amount"], env=env)["data"]

    order_file = os.path.join(tmpd, "order.json")
    with open(order_file, "w", encoding="utf-8") as f:
        json.dump({"data": {
            "orderNo": "MCD-DEMO-0001",
            "payUrl": "https://mcd.cn/pay/DEMO0001",
            "payAmount": cf["official"]["pay_amount"],
            "orderStatus": "pending_payment"}}, f, ensure_ascii=False)
    rec = run("order.py", "--record", order_file, "--hash", h, "--user", "demo_user",
              "--store-code", "S0001", env=env)["data"]

    sync_file = os.path.join(tmpd, "sync.json")
    with open(sync_file, "w", encoding="utf-8") as f:
        json.dump({"data": {"orderStatus": "paid",
                            "payAmount": cf["official"]["pay_amount"]}}, f,
                  ensure_ascii=False)
    sy = run("order.py", "--sync", "--order-no", "MCD-DEMO-0001",
             "--result", sync_file, env=env)["data"]
    orders = run("order.py", "--list", env=env)["data"]["orders"]

    return {
        "summary": prep["summary"],
        "checkout": {k: ck[k] for k in ("real_product_ids", "stripped_virtual",
                                        "remark", "local_estimate",
                                        "calculate_price_request")},
        "confirm": cf,
        "order_request": prep["create_order_request"],
        "record": rec,
        "sync": sy,
        "orders": orders,
    }


class Uniq(list):
    """按 combo_hash 去重收集方案。"""

    def __init__(self):
        super().__init__()
        self._seen = set()

    def add(self, plan):
        if plan["combo_hash"] in self._seen:
            return
        self._seen.add(plan["combo_hash"])
        self.append(plan)


def build_demo(env, samples):
    db = env["MCD_DB_PATH"]
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
    try:
        os.remove(env["MCD_SEEN_PATH"])
    except OSError:
        pass

    first = None
    plans = Uniq()
    for cal, price, protein in SEARCH_RUNS:
        cmd = ["--nutrition", os.path.join(DEMO, "nutrition.toon.txt"),
               "--menu", os.path.join(DEMO, "menu.json"),
               "--coupons", os.path.join(DEMO, "coupons.json"),
               "--calories", cal, "--price", price]
        if protein:
            cmd += ["--protein", protein]
        res = run("combo_search.py", *cmd, env=env)
        if first is None:
            first = res
        for p in res["data"]["plans"]:
            plans.add(p)

    for i, p in enumerate(plans):
        run("write_post.py", "--ids", ",".join(p["product_ids"]),
            "--title", DEMO_TITLES[i % len(DEMO_TITLES)], "--summary", p["summary"],
            "--calories", p["nutrition"]["calories"], "--price", p["final_price"],
            "--protein", p["nutrition"]["protein"],
            "--source", "system" if i % 4 == 3 else "user", env=env)

    rnd = random.Random(20261009)
    for p in plans:
        pool = list(DEMO_USERS)
        rnd.shuffle(pool)
        for u in pool[:rnd.randint(1, 5)]:
            run("vote.py", "--hash", p["combo_hash"], "--user", u, "--rating", "1", env=env)
        rest = [u for u in DEMO_USERS if u not in pool[:5]]
        for u in rest[:rnd.randint(0, 2)]:
            run("vote.py", "--hash", p["combo_hash"], "--user", u, "--rating", "-1", env=env)

    for i, p in enumerate(plans[:6]):
        for j, content in enumerate(rnd.sample(COMMENT_POOL, rnd.randint(1, 3))):
            run("write_comment.py", "--hash", p["combo_hash"],
                "--user", DEMO_USERS[(i + j) % len(DEMO_USERS)],
                "--content", content, env=env)

    timeline = []
    for i in range(samples):
        d = run("sample_recommend.py", "--max-calories", "600", "--max-price", "30",
                "--session", "verify", "--exclude", env=env)["data"]
        pk = d["picked"]
        timeline.append({
            "round": i + 1, "tier": d["tier"], "explore": d["explore"],
            "notice": d["notice"], "candidates": d["candidates"],
            "title": pk["title"] if pk else None,
            "summary": pk["combo_summary"] if pk else None,
            "calories": pk["total_calories"] if pk else None,
            "price": pk["total_price"] if pk else None,
            "hash": pk["combo_hash"] if pk else None,
            "hot_score": pk["hot_score"] if pk else None,
        })
    run("seen.py", "--session", "verify", "--reset", env=env)
    order_trace = demo_order_flow(env, list(plans)[0] if plans else None)
    return first, timeline, order_trace


def load_posts(env):
    return run("query_posts.py", "--sort", "hot", "--limit", "20",
               "--with-comments", env=env)["data"]["posts"]


def load_raw(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    out = {}
    for t in ("posts", "votes", "comments", "orders"):
        try:
            out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
        except sqlite3.Error:
            out[t] = []
    conn.close()
    return out


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mcd-meal-community 可视化验证</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;--panel:#161b22;--panel2:#1c2430;--line:#2b3646;
  --text:#e6edf3;--dim:#93a1b0;--gold:#ffc300;--red:#ff5c5c;
  --green:#3fb950;--blue:#58a6ff;--purple:#bc8cff;--orange:#f0883e;
}
body{background:var(--bg);color:var(--text);
  font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  padding:24px;line-height:1.6}
h1{font-size:22px;color:var(--gold);letter-spacing:.5px}
h2{font-size:16px;margin-bottom:12px;color:var(--text);
  border-left:3px solid var(--gold);padding-left:10px}
.sub{color:var(--dim);font-size:12px;margin-top:6px}
.wrap{max-width:1180px;margin:0 auto}
section{background:var(--panel);border:1px solid var(--line);
  border-radius:10px;padding:18px;margin-top:18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:14px}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:12px}
.kpi .n{font-size:24px;font-weight:700;color:var(--gold);font-variant-numeric:tabular-nums}
.kpi .l{font-size:12px;color:var(--dim);margin-top:2px}
.card{background:var(--panel2);border:1px solid var(--line);border-radius:8px;
  padding:14px;margin-bottom:10px}
.card.hi{border-color:var(--gold)}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.title{font-size:15px;font-weight:600}
.tag{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
  color:var(--dim);white-space:nowrap}
.tag.gold{color:var(--gold);border-color:#5c4a10;background:#2a2208}
.tag.blue{color:var(--blue);border-color:#1f3f66;background:#0d2036}
.tag.green{color:var(--green);border-color:#1c4a26;background:#0d2413}
.tag.red{color:var(--red);border-color:#5c1f1f;background:#2a0f0f}
.tag.purple{color:var(--purple);border-color:#3f2a5c;background:#1d1030}
.meta{color:var(--dim);font-size:12.5px;margin-top:6px}
.price{color:var(--gold);font-weight:700;font-variant-numeric:tabular-nums}
.item{display:inline-block;background:#0d1117;border:1px solid var(--line);
  border-radius:6px;padding:3px 9px;margin:6px 6px 0 0;font-size:12.5px}
.item .p{color:var(--dim);margin-left:6px}
.item.virtual{border-style:dashed;border-color:#3d5c3d;color:#9fd39f}
.comments{margin-top:8px;border-top:1px dashed var(--line);padding-top:8px}
.comment{font-size:12.5px;color:var(--dim);margin-top:4px}
.comment b{color:#b8c4d0;font-weight:500}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
th{text-align:left;color:var(--dim);font-weight:500;padding:7px 8px;
  border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid #1e2732;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tr.ok td{background:#0f2415}
tr.warn td{background:#2a2208}
tr.bad td{background:#2a0f0f}
.mono{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;color:var(--blue)}
.empty{color:var(--dim);font-size:13px;padding:10px 0}
.tabs{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.tab{cursor:pointer;font-size:12.5px;padding:5px 12px;border-radius:6px;
  border:1px solid var(--line);color:var(--dim);background:transparent}
.tab.on{color:#0d1117;background:var(--gold);border-color:var(--gold);font-weight:600}
pre{background:#0d1117;border:1px solid var(--line);border-radius:6px;padding:10px;
  font-size:11.5px;overflow:auto;max-height:280px;color:#a8b6c4}
.bar{height:6px;border-radius:3px;background:#0d1117;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--gold),var(--orange))}
.note{font-size:12.5px;color:var(--orange);margin-top:8px}
.legend{font-size:12px;color:var(--dim);margin-top:8px}
.disc{font-size:12px;color:var(--dim);border-left:3px solid var(--gold);
  background:rgba(255,199,44,.07);padding:8px 12px;border-radius:0 6px 6px 0;margin:10px 0 4px}
</style>
</head>
<body>
<div class="wrap">
  <h1>mcd-meal-community · 可视化验证</h1>
  <div class="disc">本页为参赛作品的功能自检看板。页面内的菜单、营养与价格数据来自
    仓库内构造的<b>演示数据</b>，仅用于离线复现整条链路，<b>不代表麦当劳真实菜单与价格</b>；
    真实数值以麦当劳官方渠道的实时结果为准。本项目非麦当劳官方产品。</div>
  <div class="sub" id="subtitle"></div>
  <div class="kpis" id="kpis"></div>
  <section><h2>① 系统推荐区 · 组合搜索结果</h2><div id="plans"></div></section>
  <section><h2>② 今日推荐 · 采样序列（验证「换一个」不重复）</h2><div id="timeline"></div></section>
  <section><h2>③ 用户搭配区 · 按热度排序</h2><div id="posts"></div></section>
  <section><h2>④ 哈希打通验证 · 系统推荐区 ↔ 用户搭配区</h2><div id="bridge"></div></section>
  <section><h2>⑤ 下单闭环 · 核价 → 下单 → 同步</h2><div id="orderflow"></div></section>
  <section><h2>⑥ 数据表原始记录</h2>
    <div class="tabs" id="tabs"></div>
    <pre id="raw"></pre>
  </section>
</div>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

$('subtitle').textContent =
  `生成于 ${D.generated_at} ｜ 数据源：${D.db_label} ｜ 约束：热量≤${D.constraints.calories}kcal 预算≤¥${D.constraints.price}`
  + (D.constraints.protein ? ` 蛋白质≥${D.constraints.protein}g` : '')
  + ` ｜ 免费冰水：${D.constraints.ice_water ? '已启用' : '已关闭'}`;

$('kpis').innerHTML = [
  ['搭配区方案', D.raw.posts.length],
  ['投票记录', D.raw.votes.length],
  ['评论记录', D.raw.comments.length],
  ['候选组合数', D.stats.combos],
  ['命中优惠券', D.stats.coupons],
  ['采样轮次', D.timeline.length],
  ['本地订单', D.raw.orders.length],
].map(([l, n]) => `<div class="kpi"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');

// ① 系统推荐区
$('plans').innerHTML = D.plans.map(p => {
  const coupon = p.coupon ? `<span class="tag gold">券 · ${esc(p.coupon.name)}</span>` : `<span class="tag">未命中券</span>`;
  const protein = p.protein_ok ? `<span class="tag green">蛋白达标</span>` : `<span class="tag red">蛋白未达标</span>`;
  const items = p.items.map(i =>
    `<span class="item${i.virtual ? ' virtual' : ''}">${esc(i.name)}<span class="p">${i.price > 0 ? '¥' + i.price : '免费'} · ${i.kcal}kcal</span></span>`).join('');
  return `<div class="card ${p.rank === 1 ? 'hi' : ''}">
    <div class="row">
      <div class="title">方案${p.rank} · ${esc(p.summary)}</div>
      <div>${coupon}${protein}<span class="tag blue">${esc(p.label)}</span></div>
    </div>
    <div class="meta">热量 <b>${p.nutrition.calories}kcal</b> ｜ 蛋白质 <b>${p.nutrition.protein}g</b>
      ｜ 原价 ¥${p.original_price} → <span class="price">${p.coupon ? '券后' : '实付'} ¥${p.final_price}</span>
      ｜ ${p.kcal_per_yuan} kcal/元</div>
    <div>${items}</div>
    <div class="meta">combo_hash <span class="mono">${p.combo_hash}</span></div>
  </div>`;
}).join('') + (D.relaxed ? `<div class="note">⚠️ ${esc(D.notice)}</div>` : '');

// ② 采样序列
const pickedHashes = D.timeline.filter(t => t.hash).map(t => t.hash);
const uniqN = new Set(pickedHashes).size;
const dupOk = pickedHashes.length === uniqN;
$('timeline').innerHTML =
  `<div class="meta">共推送 <b>${pickedHashes.length}</b> 条，去重后 <b>${uniqN}</b> 条 —— `
  + (dupOk ? '<span class="tag green">✅ 无重复，「换一个」去重生效</span>'
           : '<span class="tag red">❌ 出现重复推送</span>')
  + `</div><table><thead><tr>
  <th>轮次</th><th>层级</th><th>探索位</th><th>候选池</th><th>推中组合</th><th>hot_score</th><th>hash</th><th>说明</th>
</tr></thead><tbody>` + D.timeline.map(t => {
  const cls = t.tier === 1 ? 'ok' : (t.tier === 2 ? 'warn' : 'bad');
  const tier = ['', '第一层·约束内', '第二层·放宽120%', '第三层·忽略约束'][t.tier];
  return `<tr class="${cls}">
    <td>${t.round}</td><td>${tier}</td>
    <td>${t.explore ? '<span class="tag purple">探索</span>' : '—'}</td>
    <td>${t.candidates}</td>
    <td>${t.title ? esc(t.title) + ' · ' + esc(t.summary) + ' <span class="tag">' + t.calories + 'kcal / ¥' + t.price + '</span>' : '<span class="tag red">无可用组合</span>'}</td>
    <td>${t.hot_score ?? '—'}</td>
    <td class="mono">${t.hash ? t.hash : '—'}</td>
    <td>${esc(t.notice || '')}</td></tr>`;
}).join('') + `</tbody></table>`;

// ③ 用户搭配区
$('posts').innerHTML = D.posts.length ? D.posts.map((p, i) => {
  const cm = (p.top_comments || []).map(c =>
    `<div class="comment">「${esc(c.content)}」 <b>— ${esc(c.user_id)}</b></div>`).join('');
  const maxHot = Math.max(...D.posts.map(x => x.hot_score || 0), 1);
  const w = Math.round((p.hot_score || 0) / maxHot * 100);
  return `<div class="card">
    <div class="row">
      <div class="title">${i + 1}. ${esc(p.title)}
        <span class="tag ${p.source === 'system' ? 'blue' : 'green'}">${p.source === 'system' ? '系统发布' : '用户发布'}</span></div>
      <div><span class="tag gold">👍 ${p.up}</span><span class="tag red">👎 ${p.down}</span>
        <span class="tag">💬 ${p.comment_count}</span></div>
    </div>
    <div class="meta">${esc(p.combo_summary)}</div>
    <div class="meta">热量 <b>${p.total_calories}kcal</b> ｜ 蛋白质 <b>${p.total_protein}g</b>
      ｜ 券后 <span class="price">¥${p.total_price}</span> ｜ hot_score <b>${p.hot_score}</b></div>
    <div class="meta" style="margin-top:9px;font-size:11.5px">热度强度</div>
    <div class="bar"><i style="width:${w}%"></i></div>
    ${cm ? `<div class="comments">${cm}</div>` : ''}
    <div class="meta">combo_hash <span class="mono">${p.combo_hash}</span></div>
  </div>`;
}).join('') : '<div class="empty">搭配区暂无数据。</div>';

// ④ 哈希打通
const okAll = D.bridge.every(b => b.in_community);
$('bridge').innerHTML =
  `<div class="meta">系统推荐区算出的 combo_hash，与搭配区 posts 表里的哈希逐条比对：</div>
   <table><thead><tr><th>方案</th><th>combo_hash</th><th>是否已在搭配区</th></tr></thead><tbody>`
  + D.bridge.map(b => `<tr class="${b.in_community ? 'ok' : 'bad'}">
      <td>${esc(b.summary)}</td><td class="mono">${b.hash}</td>
      <td>${b.in_community ? '✅ 一致，评论数据可打通' : '❌ 未找到'}</td></tr>`).join('')
  + `</tbody></table>
     <div class="note">${okAll ? '✅ 全部一致 —— 同一组餐品无论从哪个入口进入，hash 相同，评论互通。'
                              : '⚠️ 存在不一致项，需检查 combo_hash 生成规则。'}</div>`;

// ⑤ 下单闭环
const of = D.order_trace;
$('orderflow').innerHTML = !of
  ? '<div class="empty">本页未运行下单闭环（--live 模式跳过演示下单）。</div>'
  : `<div class="meta">以方案「${esc(of.summary)}」为例走完整条链路：</div>
  <div class="card">
    <div class="title">第 1 步 · 精确核价</div>
    <div class="meta">本地估算 ¥${of.checkout.local_estimate} → 官方核价
      <span class="price">¥${of.confirm.official.pay_amount}</span>
      ｜ 差额 ${of.confirm.diff} ｜
      <span class="tag ${of.confirm.verdict === '一致' ? 'green' : 'gold'}">${esc(of.confirm.verdict)}</span></div>
    <div class="meta">核价请求只含真实餐品：<span class="mono">${of.checkout.real_product_ids.join(', ')}</span></div>
    <div class="meta">剥离的虚拟品：<span class="tag red">${of.checkout.stripped_virtual.join('、') || '无'}</span>
      改走订单备注「${esc(of.checkout.remark || '')}」</div>
  </div>
  <div class="card">
    <div class="title">第 2 步 · 下单</div>
    <div class="meta">create-order 请求载荷：</div>
    <pre>${esc(JSON.stringify(of.order_request, null, 2))}</pre>
    <div class="meta">订单号 <span class="mono">${esc(of.record.order_no)}</span>
      ｜ 实付 <span class="price">¥${of.record.paid_amount}</span>
      ｜ 状态 ${esc(of.record.status)}</div>
    <div class="meta">支付链接 <span class="mono">${esc(of.record.pay_url)}</span></div>
  </div>
  <div class="card">
    <div class="title">第 3 步 · 状态同步</div>
    <div class="meta">query-order 返回后回填本地：状态变为
      <span class="tag green">${esc(of.sync.status)}</span>，影响 ${of.sync.rows_updated} 行</div>
  </div>
  <div class="meta" style="margin-top:10px">本地 orders 表：</div>
  <pre>${esc(JSON.stringify(of.orders, null, 2))}</pre>`;

// ⑥ 原始表
let cur = 'posts';
function renderRaw() {
  const rows = D.raw[cur];
  $('raw').textContent = rows.length
    ? JSON.stringify(rows, null, 2)
    : '（空表）';
}
$('tabs').innerHTML = ['posts', 'votes', 'comments', 'orders'].map(t =>
  `<div class="tab${t === cur ? ' on' : ''}" data-t="${t}">${t}（${D.raw[t].length}）</div>`).join('');
$('tabs').onclick = e => {
  const t = e.target.dataset.t;
  if (!t) return;
  cur = t;
  [...$('tabs').children].forEach(c => c.classList.toggle('on', c.dataset.t === t));
  renderRaw();
};
renderRaw();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(DATA, "verify.html"))
    ap.add_argument("--live", action="store_true",
                    help="只读渲染真实库，不造演示数据")
    args = ap.parse_args()

    if args.live:
        env = {}
        db = os.path.join(DATA, "community.db")
        db_label = "正式库 community.db（只读）"
        search = run("combo_search.py",
                     "--nutrition", os.path.join(DEMO, "nutrition.toon.txt"),
                     "--menu", os.path.join(DEMO, "menu.json"),
                     "--coupons", os.path.join(DEMO, "coupons.json"),
                     "--calories", "500", "--price", "30", "--protein", "25")
        timeline = []
        order_trace = None
    else:
        db = os.path.join(DATA, "verify_demo.db")
        env = {"MCD_DB_PATH": db, "MCD_SEEN_PATH": os.path.join(DATA, "verify_demo_seen.json")}
        db_label = "演示库 verify_demo.db"
        search, timeline, order_trace = build_demo(env, args.samples)

    posts = load_posts(env)
    raw = load_raw(db)
    post_hashes = {p["combo_hash"] for p in posts}

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_label": db_label,
        "constraints": search["data"]["constraints"],
        "stats": search["data"]["stats"],
        "relaxed": search["data"]["relaxed"],
        "notice": search["data"]["notice"],
        "plans": search["data"]["plans"],
        "timeline": timeline,
        "order_trace": order_trace,
        "posts": posts,
        "raw": raw,
        "bridge": [{"summary": p["summary"], "hash": p["combo_hash"],
                    "in_community": p["combo_hash"] in post_hashes}
                   for p in search["data"]["plans"]],
    }

    data_js = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html = HTML.replace("__DATA__", data_js)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
