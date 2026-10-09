---
name: mcd-meal-community
description: 麦麦营养搭子——麦当劳营养搭配与社区工具。当用户要求根据热量和价格推荐餐品组合时使用（系统推荐区），触发词包括"减脂餐""热量不超标""便宜又健康""帮我搭配500大卡以内的""预算25块吃什么""麦当劳怎么吃不长胖"。当用户要求浏览、创建、评价他人搭配组合时也使用（用户搭配区），触发词包括"看看别人怎么搭的""发布我的组合""这个组合好吃吗""顶一下这个方案""换一个"。也承接已选定方案的下单闭环，触发词包括"帮我点这个""就买方案2""下单"。不适用于单纯查询单个餐品热量、或单纯查优惠券的场景。
---

# 麦麦营养搭子 · 麦当劳营养搭配与社区

## 0. 执行前置（每次调用都要做）

| 项 | 值 |
|---|---|
| 技能根目录 `SKILL_DIR` | `~/.workbuddy/skills/mcd-meal-community` |
| Python | 优先 `python3`；不可用时用会话托管 Python 绝对路径 |
| 脚本目录 | `SKILL_DIR/scripts`，**所有脚本必须以 `scripts/` 为工作目录执行**（脚本互相 import） |
| MCP 连接器 | `mcd-mcp`（`https://mcp.mcd.cn`，Streamable HTTP） |

调用脚本时把 `SKILL_DIR/data` 作为 MCP 数据落盘目录：

```
SKILL_DIR/data/mcp_cache/{nutrition.toon.txt|menu.json|coupons.json}
```

**不要在对话里手工推算组合、价格或哈希**——一律交给脚本，脚本输出即事实。

## 1. 意图路由（第一步）

| 用户信号 | 路由 |
|---|---|
| 出现热量/卡路里/大卡、预算/多少钱、蛋白质/蛋白 | 系统推荐区（第 2 步） |
| 出现 看看别人/浏览/发布/创建组合/顶/踩/评论/换一个 | 用户搭配区（第 7 步） |
| 出现 可视化/验证/看看效果/自测/怎么确认 | 运行 §12 生成可视化验证页 |
| 出现 下单/帮我点/就买这个/结账 | 先走系统推荐区或搭配区定方案，再走 §8 下单闭环 |
| 两者都有 | 先走系统推荐区，输出末尾附搭配区入口 |
| 完全不明 | 输出两分区入口，让用户选，不要瞎猜 |

## 2. 系统推荐区 · 解析约束（第二步）

1. 提取**热量上限**（默认 600kcal）、**预算上限**（默认 30 元）、**蛋白质下限**（可选）。
2. 用户未给出热量或预算时，**直接追问一句**，不要默认值硬跑。
3. 用户说"最便宜"就只给预算不给热量时，热量用默认 600kcal 并声明。
4. 把解析结果记下来，后续每轮输出都要复述约束。

## 3. 系统推荐区 · 组合搜索（第三步）

按顺序执行：

1. 调 MCP `list-nutrition-foods`，把返回的 `data` 原样写入 `data/mcp_cache/nutrition.toon.txt`（TOON 文本，别转 JSON，转错会丢字段）。
2. 调 MCP `query-nearby-stores` 拿门店（需要地址时先问用户城市/地址）；再调 `query-meals` 拿该门店菜单，返回体写入 `data/mcp_cache/menu.json`。
3. **尽量凑齐三个券来源**，全部落盘后一起传给 `--coupons`（可传多个文件，脚本按 `coupon_id` 去重合并）：
   - `query-store-coupons` → `data/mcp_cache/coupons_store.json`（当前门店可用券）
   - `available-coupons` → `data/mcp_cache/coupons_available.json`（麦麦省**即时可领**券）
   - `query-my-coupons` → `data/mcp_cache/coupons_mine.json`（用户已持有的券）
   用户说"帮我领券"时，先调 `auto-bind-coupons` 一键领取，再走上面三步。
   所有券接口都取不到时，省略 `--coupons`。
4. 执行组合搜索：

```bash
cd $SKILL_DIR/scripts
python3 combo_search.py \
  --nutrition ../data/mcp_cache/nutrition.toon.txt \
  --menu      ../data/mcp_cache/menu.json \
  --coupons   ../data/mcp_cache/coupons_store.json \
              ../data/mcp_cache/coupons_available.json \
              ../data/mcp_cache/coupons_mine.json \
  --calories 500 --price 30 --protein 25
```

参数：`--min-items`（默认 1，允许单品）、`--no-ice-water`（关闭内置免费冰水）、`--top`（默认 3）。

**内置免费冰水**：脚本默认给"不含饮品的组合"自动补一杯 0 元 0 卡的冰水，让每个方案都是完整一餐，
同时避免"单品"和"单品+冰水"重复出现。冰水用固定 ID `__ice_water__`，哈希稳定、可跨入口共享。
组合中至少含一件真实餐品，冰水不会单独成"餐"。

5. 读 `data.plans`，**每个 plan 已带 `combo_hash`、`product_ids`、`final_price`**。
6. `data.relaxed == true` 时，**必须在输出里明确告知已放宽约束**，并引用 `data.notice`。
7. `data.plans` 为空时，引用 `data.error`，请用户放宽预算或换门店，**不要编造组合**。

### Top3 的三条产出规则

| 方案 | 取法 |
|---|---|
| 最低价方案 | 券后价最低；同价取热量更低 |
| 热量最低方案 | 热量最低；同热量取更便宜 |
| 蛋白质达标 | 仅当用户设了蛋白质下限时输出；取达标组合中蛋白质最高，同蛋白取更便宜 |

同价同热量时**件数多的优先**（即带免费冰水的那份更完整）。

## 4. 系统推荐区 · 券后价复核（第三步补充）

`combo_search` 的价格是**本地启发式估算**（`price_is_estimate: true`），规则为：门槛校验 → 适用范围（`scope_ids` 交集，空表示全场通用）→ 面额优先、否则按折扣（`7.5` 与 `0.75` 两种写法都兼容）→ 取单券净减最多的一张。

对 Top3 方案，各调一次 MCP `calculate-price`（传 `product_ids` 与命中券 `coupon_id`）做精确复核。复核值与估算不一致时，**以 `calculate-price` 为准并覆盖展示值**；复核失败则在价格后标注"估算价"。

**套餐类商品（随心配 / 1+1 / 超值套餐）**：`query-meals` 返回的套餐要作为**一个独立餐品**参与枚举，不要拆成单品——套餐单价通常低于单品相加。套餐营养需在 `list-nutrition-foods` 里有同名条目才能匹配；匹配不上的会出现在 `data.stats.unmatched_nutrition`，此时如实告知"该套餐未纳入计算"，不要凭空补营养值。

## 5. 系统推荐区 · 今日推荐（第四步）

```bash
cd $SKILL_DIR/scripts
python3 sample_recommend.py --max-calories 600 --max-price 30 \
  --session <会话ID> --exclude
```

1. `--session` 用当前会话的稳定标识（无则用 `default`）。
2. `--exclude` 把本次命中的 hash 写入 `session_seen.json`，这是"换一个"不重复的唯一机制，**每次推送都要带**。
3. 读 `data.picked`；`tier` 为 2/3 时把 `data.notice` 原样告知用户。
4. `data.explore == true` 表示命中 20% 探索位，展示时可加"新品尝鲜"标签。
5. `data.picked == null` 时按 `data.notice` 引导，不要重试刷屏。

用户说"换一个"→ 重跑同一条命令即可。用户说"重置推荐"→ 先跑
`python3 seen.py --session <会话ID> --reset` 再推送。

## 6. 系统推荐区 · 展示与交互（第五、六步）

1. 渲染输出**必须**使用 `assets/output_template.md` 的模板，不要自由发挥版式。
2. 展示每个方案前，用 `query_comments.py --hash <h> --limit 3` 取顶/踩数与评论。
3. 交互指令映射，逐条执行：

| 用户说 | 执行 |
|---|---|
| 顶/踩方案N | `vote.py --hash <方案N的hash> --user <会话用户ID> --rating 1\|-1` |
| 评论N 内容 | `write_comment.py --hash <hash> --user <用户ID> --content "内容"` |
| 发布方案N | `write_post.py --ids "<product_ids 逗号分隔>" --title ... --summary ... --calories ... --price ... --protein ... --source system` |
| 下单方案N / 就买这个 | 先复述订单要素取得确认，再走 §8 下单闭环 |
| 看看搭配区 | 转到第 7 步 |
| 换一个 | 见 §5 |

4. 投票与评论后，**回显最新的顶/踩数**，让用户看到生效。
5. 同一个用户重复投票是"改票"不是叠加，回显时说明已更新。
6. 发布时 `--ids` 用脚本返回的 `product_ids` 原样传入，**顺序无所谓**（哈希会自动归一）。

## 7. 用户搭配区（第七、八、九步）

**浏览**：

```bash
python3 query_posts.py --sort hot --limit 10 --with-comments
python3 query_posts.py --sort new --limit 10
python3 query_posts.py --max-calories 600 --max-price 30 --sort price
```

`--sort` 可选 `hot`（热榜，见 §9 公式）/ `new` / `price` / `calories`。

**创建**：

1. 先问用户要哪几样（或让他说"就按刚推荐的那个"）。
2. 已知餐品 ID 直接 `write_post.py`；只有名称没有 ID 时，从 `menu.json` 反查 ID。
3. `--title` 让用户起名字，用户不起就按"低热量且最便宜"这类风格自动生成并告知。
4. 发布成功后回显 `combo_hash` 与 `action`；`action: updated` 说明该组合已存在，是复用而非新建——**要告诉用户评论数据已自动继承**。

**互动**：同 §6 的 `顶/踩/评论` 映射。

## 8. 下单闭环

推荐只是半程。把方案真正落单要三步，**脚本准备参数、Agent 调 MCP**。

**定位组合：`--hash` 与 `--ids` 二选一**

| 场景 | 用哪个 |
|---|---|
| 用户已把方案发布到搭配区（`write_post.py` 跑过） | `--hash <combo_hash>` |
| 用户看完推荐直接说「就买方案N」（**最常见**） | `--ids <方案N的product_ids逗号分隔> --price <方案N的final_price>` |

⚠️ `combo_search.py` 只算不落库，它返回的 `combo_hash` **不在 posts 表里**。
别为了让下单跑通而偷偷调 `write_post.py`——那会往社区塞一条用户没主动发布的记录。
直接用 `--ids`，干净且不打扰社区。

**第一步 · 精确核价**

```bash
# 场景 A：组合已在搭配区
python3 checkout.py --hash <combo_hash> --store-code <门店编码> \
  --order-type takeaway [--coupon <couponId>] --out ../data/mcp_cache/price_req.json

# 场景 B：直接从搜索结果下单（推荐）
python3 checkout.py --ids "<product_ids逗号分隔>" --price <final_price> \
  --store-code <门店编码> --order-type takeaway \
  [--coupon <couponId>] --out ../data/mcp_cache/price_req.json
```

读 `data.calculate_price_request`，交给 MCP `calculate-price`，返回存文件后回填：

```bash
python3 checkout.py --ids "<product_ids逗号分隔>" --confirm ../data/mcp_cache/price_result.json
```

⚠️ `--confirm` 同样要带 `--hash` 或 `--ids`（combo_hash 不可逆，脚本无法从哈希反推商品编码）。

`verdict` 为「一致」就直接用；否则**以 `official.pay_amount` 覆盖本地估算**再展示。

**第二步 · 下单**

```bash
# 场景 A：组合已在搭配区
python3 order.py --hash <combo_hash> --store-code <门店编码> \
  --order-type takeaway --user <用户ID> \
  [--address-id <地址ID>] [--coupon <couponId>] [--confirmed-price 15.5] \
  --out ../data/mcp_cache/order_req.json

# 场景 B：直接从搜索结果下单（推荐）
python3 order.py --ids "<product_ids逗号分隔>" --price <final_price> \
  --store-code <门店编码> --order-type takeaway --user <用户ID> \
  [--address-id <地址ID>] [--coupon <couponId>] [--confirmed-price 15.5] \
  --out ../data/mcp_cache/order_req.json
```

读 `data.create_order_request` 交给 MCP `create-order`，返回存文件后回填：

```bash
# 场景 A 的组合（已在 posts 表）：用 --hash
python3 order.py --hash <combo_hash> --record ../data/mcp_cache/order_result.json \
  --user <用户ID> --store-code <门店编码>

# 场景 B 的组合（--ids 直通，未发帖）：回填时必须同样带 --ids，
# 否则订单历史里查不到「这单点了什么」
python3 order.py --ids "<product_ids逗号分隔>" --record ../data/mcp_cache/order_result.json \
  --user <用户ID> --store-code <门店编码>
```

回填后返回 `order_no` 与 `pay_url`，**把支付链接原样转述给用户**，不要自己拼链接。
返回里若出现 `warning`（实付与确认价有差额），先把差额讲清楚再给链接。

**第三步 · 同步状态**

```bash
python3 order.py --sync --order-no <订单号> --result ../data/mcp_cache/order_query.json
python3 order.py --list --user <用户ID> --limit 10
```

`--list` 返回 `total`（该用户订单总数）+ `returned`（本页条数）+ `orders`，不要拿 `returned` 当总数汇报。

### 免费冰水下单时必须改走备注

冰水是内置虚拟商品（ID `__ice_water__`），**不是真实 SKU**，塞进 `create-order` 会被接口拒绝。
`checkout.py` / `order.py` 已自动处理：把冰水剥离出商品列表，写进 `remark`
「请另附一杯免费冰水，谢谢」。**不要试图把 `__ice_water__` 放进 products。**

### 下单前必须确认的事

1. 下单是**外部动作**。必须先向用户复述「门店 + 餐品 + 实付金额 + 就餐方式」，取得明确同意后再调 `create-order`。
2. `store-code` 不能猜，必须来自 `query-nearby-stores` 的返回。
3. 外送场景先调 `delivery-query-addresses` 拿地址，再传 `--address-id`。

## 9. 热榜公式

```
hot_score = (net_votes + 1)^0.8 / (hours_since_post + 2)^0.5
```

- `net_votes = 顶 - 踩`；净票为负时底座取 0.05（权重显著降低但不归零）。
- 时间按 `posts.created_at`（**UTC**）计算，脚本已处理时区，不要自己算。
- 不要手算这个分数，`query_posts` 与 `sample_recommend` 都会返回。

## 10. 边界与红线

1. **无可行组合**：脚本已自动放宽 10% 热量并允许单件，仍为空才报错；如实转述，不编造。
2. **门店售罄/下架**：`query-meals` 未返回的餐品会在内连接时被剔除，属正常行为，告知用户即可。
3. **营养数据匹配不上**：看 `data.stats.unmatched_nutrition`，把未能匹配的餐品名告知用户，并说明它们已被排除。
4. **搜索被截断**：`data.stats.truncated == true` 表示组合枚举撞到了硬上限、后面还有组合没算。
   此时**必须告诉用户"结果可能不是全局最优"**，不要宣称已找全。常规菜单规模下该字段恒为 `false`。
5. **数据落盘**：MCP 原始返回一律写文件再喂脚本，**不要凭记忆改数字**。
6. **限流**：MCP 每分钟 600 次。不要为每个候选组合都调 `calculate-price`，只对 Top3 调。
7. **隐私**：不输出用户手机号、完整配送地址；门店与用户标识按脱敏处理。
8. **社区数据**：只读写本技能 `data/community.db`，不碰其它数据库；`user_id` 用会话内稳定标识即可，不要索取真实身份。
9. **下单不可自作主张**：`create-order` 是真实扣款动作，必须先复述订单要素并得到用户明确同意；支付链接只能转述 MCP 返回值，**不得自己构造**。`order.py --record` 返回的 `warning` 若提示实付与确认价有差额，**必须先向用户说明差额**，不得直接把支付链接甩过去。
10. **虚拟商品不进订单**：`__ice_water__` 只能出现在本地组合与推荐展示里，下单前必须剥离并转成 `remark`。

## 11. 脚本速查

| 脚本 | 作用 | 关键参数 |
|---|---|---|
| `combo_search.py` | 组合枚举 + 券后价 + Top3 | `--nutrition --menu --coupons --calories --price --protein --top --min-items --no-ice-water` |
| `write_post.py` | 发布组合 | `--ids --title --summary --calories --price --protein --source` |
| `vote.py` | 顶/踩 | `--hash\|--ids --user --rating 1\|-1` |
| `write_comment.py` | 写留言 | `--hash\|--ids --user --content` |
| `query_comments.py` | 读留言 | `--hash\|--ids --limit` |
| `query_posts.py` | 浏览搭配区 | `--sort --limit --max-calories --max-price --min-protein --with-comments` |
| `sample_recommend.py` | 今日推荐采样 | `--max-calories --max-price --min-protein --session --exclude --reset-seen` |
| `hot_score.py` | 热榜分数 | `--net --hours` |
| `seen.py` | 已推列表查看/重置 | `--session --reset` |
| `render_debug.py` | 生成可视化验证页 | `--samples --out --live` |
| `checkout.py` | 核价：准备载荷 / 回填官方价 | `--hash\|--ids --store-code --order-type --coupon --confirm --price` |
| `order.py` | 下单：载荷 / 记录 / 列表 / 同步 | `--hash\|--ids --store-code --record --list --sync --order-no --price --title --summary` |
| `selftest.py` | 端到端自测（独立临时库） | 直接运行，退出码 0 = 全通过 |
| `store.py` | 共享数据层（被 import） | 直接运行 = 初始化 DB |

## 12. 可视化验证

一条命令生成自检看板 `data/verify.html`（单文件、数据内联、双击即看，不需要起服务器）：

```bash
cd $SKILL_DIR/scripts
python3 render_debug.py            # 演示库：自动跑一遍完整链路后渲染
python3 render_debug.py --live     # 只读渲染真实库，不造演示数据
```

页面分六块，一眼看清整条链路是否成立：

| 区块 | 验证什么 |
|---|---|
| ① 系统推荐区 | Top3 方案、券后价、命中券、combo_hash |
| ② 今日推荐 · 采样序列 | 「换一个」是否重复、分层降级 tier 1/2/3、20% 探索位 |
| ③ 用户搭配区 | 热榜排序、热度强度条、顶踩数与评论 |
| ④ 哈希打通 | 系统推荐区算出的 hash 与 posts 表逐条比对 |
| ⑤ 下单闭环 | 核价载荷、官方价回填、下单记录、状态同步 |
| ⑥ 数据表原始记录 | posts / votes / comments / orders 四表原始 JSON |

演示数据落在独立的 `data/verify_demo.db` 与 `verify_demo_seen.json`，**不会污染正式库**。
页面里出现红色 ❌ 或「出现重复推送」，即自检未通过。

改完脚本先跑一遍回归：

```bash
python3 selftest.py      # 退出码 0 = 全部通过
```
