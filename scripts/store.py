#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcd-meal-community 共享数据层。

职责：
  1. 统一 DB 路径 / session_seen 路径
  2. combo_hash 生成（跨入口一致）
  3. 建表与连接
被所有 scripts/ 下的脚本 import。
"""
import os
import re
import sys
import json
import sqlite3
import hashlib
from datetime import datetime

# ---------- 编码兜底（Windows 控制台 GBK 会炸中文） ----------
for _s in ("stdout", "stderr"):
    _stream = getattr(sys, _s, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
# 允许用环境变量指向独立库（可视化验证页 / 演示数据用，避免污染正式数据）
DB_PATH = os.environ.get("MCD_DB_PATH") or os.path.join(DATA_DIR, "community.db")
SEEN_PATH = os.environ.get("MCD_SEEN_PATH") or os.path.join(DATA_DIR, "session_seen.json")

SEEN_TTL_HOURS = 24 * 7  # 会话已推列表过期时间（7 天）

# 内置虚拟商品（非真实 SKU，下单前必须剥离，改走订单备注）
VIRTUAL_PRODUCT_IDS = {"__ice_water__"}
VIRTUAL_REMARK = "请另附一杯免费冰水，谢谢"


def split_virtual(product_ids):
    """分离真实商品与虚拟商品。返回 (real_ids, virtual_names)。"""
    real, virt = [], []
    for pid in product_ids:
        if str(pid).strip() in VIRTUAL_PRODUCT_IDS:
            virt.append(str(pid).strip())
        else:
            real.append(str(pid).strip())
    names = ["冰水" if v == "__ice_water__" else v for v in virt]
    return real, names


# ---------- 通用取值工具（兼容 MCP 返回字段名差异） ----------
def pick(d, *keys, default=None):
    """按候选键顺序取第一个非空值。"""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, "", []):
            return d[k]
    return default


def to_num(v):
    """宽松转数字：'¥12.9' / '12.9元' -> 12.9；失败返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def unwrap(obj, depth=3):
    """剥掉 MCP envelope 的 data 层。"""
    for _ in range(depth):
        if isinstance(obj, dict) and "data" in obj and len(obj) <= 6:
            obj = obj["data"]
        else:
            break
    return obj


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    return unwrap(obj)


# ---------- combo_hash ----------
def combo_hash(product_ids):
    """餐品 ID 规范化 -> 字典序排序 -> '|' 拼接 -> SHA256 取前 16 位。

    加分隔符可避免 ['12','3'] 与 ['1','23'] 撞哈希；顺序归一保证
    同一组餐品无论从系统推荐区还是用户搭配区进入，hash 完全一致。
    """
    norm = sorted(str(i).strip() for i in product_ids if str(i).strip())
    if not norm:
        raise ValueError("combo_hash 需要至少一个餐品 ID")
    raw = "|".join(norm)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------- DB ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    combo_summary TEXT NOT NULL,
    product_ids TEXT,
    total_calories REAL,
    total_price REAL,
    total_protein REAL,
    source TEXT DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_hash TEXT NOT NULL,
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(combo_hash, user_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_hash TEXT NOT NULL,
    user_id TEXT,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_votes_hash ON votes(combo_hash);
CREATE INDEX IF NOT EXISTS idx_comments_hash ON comments(combo_hash);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_hash TEXT NOT NULL,
    title TEXT,
    user_id TEXT,
    store_code TEXT,
    order_type TEXT,
    product_ids TEXT,
    stripped_virtual TEXT,
    remark TEXT,
    coupon_id TEXT,
    local_estimate REAL,
    confirmed_price REAL,
    paid_amount REAL,
    order_no TEXT,
    pay_url TEXT,
    status TEXT DEFAULT 'created',
    raw_response TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_hash ON orders(combo_hash);
CREATE INDEX IF NOT EXISTS idx_orders_no ON orders(order_no);
"""


def _migrate(conn):
    """轻量迁移：老库补列，保证不必删库重建。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(posts)")}
    if "product_ids" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN product_ids TEXT")


def get_conn():
    # 自定义 MCD_DB_PATH 时，其父目录可能不存在（默认 DATA_DIR 之外）
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir and not os.path.isdir(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump({"sessions": {}}, f, ensure_ascii=False, indent=2)
    return DB_PATH


# ---------- 聚合视图 ----------
def combo_stats(conn, combo_hash_value):
    """返回单个 combo 的 顶/踩/评论数 聚合。"""
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END), 0) AS up,
          COALESCE(SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END), 0) AS down
        FROM votes WHERE combo_hash = ?
        """,
        (combo_hash_value,),
    ).fetchone()
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM comments WHERE combo_hash = ?",
        (combo_hash_value,),
    ).fetchone()
    up, down = int(row["up"]), int(row["down"])
    return {"up": up, "down": down, "net": up - down, "comments": int(cnt["c"])}


# ---------- session_seen ----------
def _load_seen():
    if not os.path.exists(SEEN_PATH):
        return {"sessions": {}}
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "sessions" not in data:
            data = {"sessions": {}}
        return data
    except Exception:
        return {"sessions": {}}


def _save_seen(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SEEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SEEN_PATH)


def seen_hashes(session_id="default"):
    """读取某会话已推 hash 列表，顺带清理过期会话。"""
    data = _load_seen()
    now = datetime.now()
    dirty = False
    for sid in list(data["sessions"].keys()):
        ts = data["sessions"][sid].get("updated_at")
        if not ts:
            continue
        try:
            age = (now - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
        except Exception:
            continue
        if age > SEEN_TTL_HOURS:
            del data["sessions"][sid]
            dirty = True
    if dirty:
        _save_seen(data)
    return list(data["sessions"].get(session_id, {}).get("seen", []))


def seen_add(session_id, hashes):
    data = _load_seen()
    entry = data["sessions"].setdefault(session_id, {"seen": [], "updated_at": ""})
    merged = list(dict.fromkeys(list(entry.get("seen", [])) + list(hashes)))
    entry["seen"] = merged
    entry["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_seen(data)
    return merged


def seen_reset(session_id="default"):
    data = _load_seen()
    data["sessions"].pop(session_id, None)
    _save_seen(data)


if __name__ == "__main__":
    print(init_db())
