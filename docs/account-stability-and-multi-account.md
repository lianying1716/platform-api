# 账号长期稳定性 与 多账号 设计

> 日期：2026-10-08 · 状态：**待实施**（本文是后续开发的依据）
> 来源：对 `bili-sync`（B站）与 `dysync.net`/抖小云（抖音）的源码对照调研 + 本项目代码实测。
> 前置阅读：`docs/license-and-usage-compliance.md`（上游许可）。

---

## 0. 一句话结论

**B站要"主动刷新"，抖音要"绝对守恒"——两个平台需要相反的策略。**

想把一套逻辑套到所有平台，必然有一个做错。本文按平台分别定策略，并给出多账号的正确做法。

---

## 1. 事实基础（已核实，含来源）

| 事实 | 证据 |
|---|---|
| 抖小云在 NAS 上**是纯 HTTP**，不跑浏览器 | `Dockerfile` 仅 `aspnet:6.0` + ffmpeg，无 chromium/playwright；`dy.net.csproj` 无浏览器库；抓取全走 `IHttpClientFactory.SendAsync` |
| `dy.cookie.exe` 只是**独立的 Windows 取 Cookie 小工具**，不参与运行时 | 该 exe 与主程序无调用关系 |
| 抖小云**没有任何自动刷新** | 全仓库无 `refresh_token`/`cookie/refresh`；失效只能人工 `FastResetCookie` 重登 |
| 抖小云 Cookie **整串透传**，不裁剪 | `TryAddWithoutValidation("Cookie", cookie)`；DB 存整串 |
| 抖小云多账号 = **一行一账号，各自独立整串** | `dy_cookie` 表；`cookie.Cookies` 逐账号使用 |
| bili-sync **有** B站自动刷新（完整算法） | `credential.rs:161-262`：RSA-OAEP→`/correspond/1/{path}`→`cookie/refresh`→`confirm/refresh` |
| bili-sync 刷新后**保留原 `buvid3`** | `get_new_credential()` 里 `credential.buvid3 = self.buvid3.clone()` |
| bili-sync 也只发**具名 Cookie**（5 个，非整串） | `client.rs:50-57` |

> ⚠️ 纠正两处早期误判：① 抖小云不是"靠浏览器"；② "我们只发 4 个 Cookie 是缺陷"不成立——bili-sync 同为具名集合，我们只差 `ac_time_value`。

---

## 2. 分平台策略

### 2.1 B站 —— 主动刷新（"该动"的平台）

B站服务端**原生支持 Cookie 续期**，`ac_time_value` 就是钥匙。我们存了却从不用 = 浪费。

**算法（照抄 bili-sync，Python 实现）**：
1. `GET /x/passport-login/web/cookie/info` → `data.refresh == true` 才需要刷新
2. 生成 `correspond_path`：用**内置 B站公钥**做 RSA-OAEP(SHA256) 加密 `refresh_{毫秒时间戳-20s}`，hex 编码
3. `GET https://www.bilibili.com/correspond/1/{path}` → 正则 `<div id="1-name">(.+?)</div>` 取 `refresh_csrf`
4. `POST /x/passport-login/web/cookie/refresh`（form：`csrf`=bili_jct、`refresh_csrf`、`refresh_token`=ac_time_value、`source=main_web`）
   → 从 **Set-Cookie** 取新 SESSDATA/bili_jct/DedeUserID，从 body 取新 `refresh_token`
5. `POST /x/passport-login/web/confirm/refresh`（用**新**凭证 + **旧** ac_time_value）
6. **新凭证必须保留旧 `buvid3`**（设备指纹一致性）

**触发时机**：账号体检时发现 `need_refresh` → 自动刷新 → 写回加密凭证。

**多账号**：每个账号有各自的 `ac_time_value`/`bili_jct`，**必须逐账号刷新**，禁止共用。

### 2.2 抖音 —— 绝对守恒（"不能动"的平台）

抖音**没有任何续期接口**。登录态绑在一整套设备指纹上，任何"优化"都可能让指纹漂移、加速风控。

**守恒清单（写进代码注释，防止未来被"优化"）**：
- Cookie **整串存取**，不做裁剪、不重建、不挑字段
- 参数模板（`webid`/`version_code`/`browser_version` 等）**写死**，不随机化
- **禁止**持久化浏览器 profile、禁止改设备标识
- 低频同步 + 冷却（沿用现状）
- 失效 → **只能人工重登**（与抖小云一致，这是刻意的，不是缺陷）

> 为什么抖小云稳定：整串 cookie + 写死参数 + 低频 + 不折腾。**少动就是稳。**

### 2.3 其他平台

| 平台 | 策略 | 依据 |
|---|---|---|
| 小红书 | 守恒（浏览器扫码只为取 cookie，之后纯 HTTP） | 无续期接口 |
| 快手 / 知乎 | 守恒 | 同上 |
| 微信公众号 | **无法长期**——微信服务端 4 天强制过期 | 保持"到期提示 + 一键重登" |

### 2.4 总表

| 平台 | 自动刷新 | Cookie 传递 | 失效恢复 |
|---|---|---|---|
| B站 | ✅ 有（本文 2.1） | 具名 5 项（含 ac_time_value） | 自动续期 |
| 抖音 | ❌ 不能有 | 整串透传（守恒） | 人工重登 |
| 小红书/快手/知乎 | ❌ | 整串透传 | 人工重登 |
| 公众号 | ❌ | token+cookie | 人工重登（4 天） |

---

## 3. 多账号设计（关键约束）

### 3.1 已正确的部分 ✅

- DB `platform_account` **一行一账号**，各自一份 AES-GCM 加密凭证
- 适配器**无状态**（`grep 'self.x ='` 零命中），凭证按参数传入
- 定时体检 `check_all_accounts` 逐账号解密各自凭证
- 同步缓存/冷却键含 `account_id`：`creator_videos:{platform}:{account_id}:{remote_id}`
- 扫码会话按 `session_id` 隔离，每会话独立 browser/context

### 3.2 严重缺陷（实测复现）❌

**缺陷 A：第二个账号会静默覆盖第一个账号的凭证**

`app/api/accounts.py:qr_login_poll` 的账号选择顺序：

```
1. 按 (platform, external_id) 精确匹配
2. 匹配不到，但该平台只有一个账号 → 【复用】它并覆盖凭证
3. 该平台有多个账号且无身份 → 抛 409
```

问题：**第 2 条在多账号场景下是错的**。实测（`已有账号A(SEC_A)` + 扫入 B(SEC_B)，B 的 external_id 匹配不到）：

```
该平台已有账号数 = 1
>>> 命中「只有一个就复用」分支 → account = 账号A SEC_A
>>> B 的凭证被写进 A，A 被覆盖
```

**后果**：用户以为加了 B，实际 A 的凭证被 B 覆盖 → **A 丢失、B 只有一个、且卡片显示的名字还是 A**。静默数据损坏。

**缺陷 B：无 `external_id` 的平台加不进第二/第三个账号**

`check()` 返回 `external_id` 的平台只有 **B站** 和 **知乎**：

| 平台 | check 返回 external_id |
|---|---|
| bilibili | ✅ mid |
| zhihu | ✅ id |
| douyin | ❌ 空 |
| xhs | ❌ 空（check 里有，但 QR 流程不一定回填） |
| wechat_mp | ❌ 空 |
| kuaishou | ❌ 空 |

实测（已有 2 个账号、平台不回身份）：

```
已有账号数: 2
>>> 抛 409：第三个账号加不进来
```

**后果**：抖音/小红书/微信/快手 用户**无法添加第二个账号**。

**缺陷 C：手动录入不去重**

`create_account`（手动粘 Cookie）**完全没有去重**，直接 `db.add()`。同一账号反复录入会生成多行重复账号。

**缺陷 D：采集任务不绑账号，Cookie 靠"第一个在线账号"猜**

`crawl_task` 表**没有 `account_id`**；执行时 `crawl_service._platform_cookie()` 这样取凭证：

```python
account = db.scalar(select(PlatformAccount).where(
    PlatformAccount.platform == platform, PlatformAccount.status == "active"
).order_by(PlatformAccount.id))   # 取 id 最小的那个
```

多账号下后果：
- **用哪个账号不可控**——永远是 id 最小的那个，与任务无关
- 若该账号被风控/限流，**其它健康账号无法顶上**
- 用户无法指定"这个采集任务用哪个号"

> 注：采集是**平台级**操作（关键词搜索与账号关系不大），所以这不是数据错误；但"隐式挑一个账号"应改成**显式**（可选指定，未指定时按"最近可用"而非"id 最小"）。

**已正确（非缺陷）**：内容去重键是 `(platform, external_id)`，**不含账号**——同一视频被两个账号采到应视为同一条内容，这是对的。

### 3.3 修正方案

| 缺陷 | 修正 |
|---|---|
| A：静默覆盖 | 第 2 条"复用唯一账号"**收紧**：只在 `poll.external_id` 与现有账号 `external_id` **一致**、或现有账号 `external_id` **为空**（升级回填场景）时才复用；否则**新建账号**。 |
| B：加不进账号 | 每个平台登录后**必须拿到稳定唯一身份**：抖音用 `sec_user_id`/`uid`（`_douyin_profile` 已能取，需确保写入 poll）、小红书用 `user_id`、快手/知乎用各自 id、公众号用 `token` 派生。取不到身份时**明确提示用户**而非静默复用。 |
| C：手动不去重 | `create_account` 按 `(platform, external_id)` 查重：命中**更新**该账号凭证，未命中才新建。 |
| D：采集不绑账号 | `crawl_task` 加可选 `account_id`；未指定时取"最近体检在线"的账号，并在 `crawl_run` 记录实际用了哪个账号。 |

**核心原则**：**账号身份必须由平台返回的稳定 ID 决定，绝不靠"该平台只有一个账号"来猜。**

---

## 4. 数据隔离现状（二次深挖，实测）

上一节解决"**加得进**"，本节解决"**分得开**"——即累加账号后数据怎么隔离。

### 4.1 现状：26 张表里只有 1 张带账号归属

| 层级 | 表 | 有 `account_id`？ |
|---|---|---|
| 账号 | `platform_account` / `account_credential` / `credential_audit_log` | ✅ |
| **订阅** | `subscription` | ✅ **唯一真正的隔离边界** |
| | `sync_run` | ❌（仅靠 `subscription_id` 间接） |
| **内容** | `content_item` | ❌ |
| | `content_metric_snapshot` / `comment` / `raw_payload` | ❌（靠 `content_id`） |
| 创作者 | `creator_profile` | ❌（`(platform, author_id)` 全局唯一） |
| 素材 | `media_asset` / `transcript` / `analysis_result` / `video_understanding` | ❌ |
| 采集 | `crawl_task` / `crawl_run` | ❌ |
| 其他 | `fetch_cache` / `task_log` / `job_queue` | ❌ |

### 4.2 实测复现的三个隔离缺陷

**缺陷 E：同一视频被多账号收藏时，只有第一条归属被记录**

去重键 `(platform, external_id)` **不含账号**（这个去重本身是对的——同一视频应是同一条内容），
但 `content_item` 只有一个 `source_id`，于是：

```
B 同步同一视频 V1 时：
  去重命中已存在内容？ True | source_kind/source_id = subscription 1
  → B 的订阅关系【不会】被记录；内容只归属 A 的订阅
```

**后果**：B 的"我收藏了它"丢失，前端按 B 过滤时看不到这条。**内容全局唯一是对的，但归属必须是多对多。**

**缺陷 F：删账号后内容变孤儿**

`content_item.source_id` 是**裸整数，不是外键**，删账号时 `subscription` 被 CASCADE 删掉，`content_item` 残留：

```
删账号 A 后：
  剩余内容条数 = 1（视频 V1 还在，但 source_id=1 已成孤儿）
  → 内容变成无主孤儿：既不属于 A（已删），也未归属 B
```

> 另记：SQLite 下**未开 `PRAGMA foreign_keys=ON`**（`app/db.py` 无相关监听），
> 本地开发/测试的 CASCADE 其实**不生效**，与生产 PG 行为不一致。

**缺陷 G：素材库 / 看板无账号维度**

- `list_assets()` 支持 platform/author/category… 但**无 `account_id`**
- `analytics` 按平台聚合，**无账号维度**
- 前端 `Assets.vue` 只有"平台"筛选，没有账号筛选

**对的（非缺陷）**：`creator_profile` 按 `(platform, author_id)` 全局唯一——博主资料与"谁关注了他"无关，
两个账号关注同一博主共享一份资料是**正确的**。

### 4.3 目标模型：内容全局唯一 + 归属多对多

```
platform_account ──1:N──> subscription ──┐
                                          │ 同步产生
                                          ▼
content_item (platform, external_id 唯一)  ←── 内容实体，全局一份
      ▲
      │ 多对多
content_account_link(content_id, account_id, subscription_id, first_seen_at)
```

- `content_item`：保持全局唯一，**不加** `account_id`（一条视频可被多个号收）
- **新增 `content_account_link`**：记录"哪个账号、经哪条订阅、何时收到这条内容"——这是隔离的真相
- `subscription`：不变（已是账号级）

---

## 5. 完整改造清单

目标：**多账号"加得进、分得开、删得净、长期稳"**。分 6 个阶段，每阶段可独立验收。
缺陷编号沿用上文（A/B/C/D 见 §3.3，E/F/G 见 §4）。

### 阶段 1：账号身份与去重（P0，先做）

> 不解此阶段，第二/第三个账号加不进或互相覆盖。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 1.1 | 修**缺陷 A**：`qr_login_poll` 的"复用唯一账号"收紧——仅当 `poll.external_id` 与现有账号一致、**或**现有账号 `external_id` 为空时才复用；否则**新建** | `app/api/accounts.py` | 加 B 后 A 完好、B 独立成行 |
| 1.2 | 修**缺陷 B**：各平台登录后**必须回填稳定身份**。抖音补 `_douyin_profile` 的 `sec_uid/uid` 写入 poll；小红书取 `user_id`；快手/知乎用各自 id；公众号用 `token` 派生标识。取不到 → **明确提示**，不静默 | `app/modules/{douyin,xhs,crawl_platforms,wechat_mp}.py`、`app/api/accounts.py` | 同平台可连续加 3 个账号 |
| 1.3 | 修**缺陷 C**：`create_account` 按 `(platform, external_id)` 查重，命中**更新**该账号凭证 | `app/api/accounts.py` | 重复录入同一号 = 更新而非新增 |
| 1.4 | 账号删除前检查：有订阅/内容时**明确提示**而非静默连删 | `app/api/accounts.py` | 删账号有明确提示 |
| 1.5 | 补测试：`tests/test_multi_account.py`（A 不覆盖、B 可累加、C 去重） | `tests/` | 三个场景全绿 |

### 阶段 2：内容归属多对多（P0，隔离核心）

> 不解此阶段，加了账号也"分不开"。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 2.1 | 新增表 `content_account_link(id, content_id, account_id, subscription_id, first_seen_at)`，唯一键 `(content_id, account_id)`；`content_id`/`account_id` 均真 FK + CASCADE | `app/models.py`、新迁移 `0012_*` | 迁移可 upgrade/downgrade |
| 2.2 | 同步入库时**同时写 link**：内容已存在也补写"本账号的归属"（修缺陷 E） | `app/services/sync_service.py` | 同一视频被 A/B 收 → 两条 link |
| 2.3 | 迁移时**回填历史数据**：按 `content_item.source_id → subscription.account_id` 生成 link | 迁移 `0012` | 老数据不漏 |
| 2.4 | 内容查询统一改为"经 link 关联账号" | `app/api/assets.py`、`analytics.py` | 可按 account 过滤 |
| 2.5 | `list_assets` 增加 `account_id` 参数；看板增加账号维度 | `app/api/assets.py`、`app/api/analytics.py` | API 支持 |
| 2.6 | 补测试：跨账号同视频 → 两条归属；按 account 过滤互不串 | `tests/test_content_multi_account.py` | 全绿 |

### 阶段 3：孤儿与级联（P1）

> 保证"删得净"。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 3.1 | 修**缺陷 F**：`content_item.source_id` 语义收敛（改真 FK 到 `subscription`，或明确标记为"仅溯源"），删账号时内容**不孤儿化**——由 link 兜底归属 | `app/models.py`、迁移 `0012` | 删 A 后内容仍归属 B |
| 3.2 | 统一 SQLite `PRAGMA foreign_keys=ON`（消除本地/生产行为差异） | `app/db.py` | 测试能验出 CASCADE |
| 3.3 | 删账号的收尾策略：无 link 的内容 → 标记或转交，**不留裸整数孤儿** | `app/api/accounts.py`、`app/services/` | 无孤儿 |

### 阶段 4：采集与任务带账号（P1，修缺陷 D）

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 4.1 | `crawl_task` 加可选 `account_id`（FK，SET NULL） | `app/models.py`、迁移 `0013_*` | 迁移 OK |
| 4.2 | `_platform_cookie()` 改为：优先任务指定账号 → 否则"最近体检在线"（非 id 最小），并在 `crawl_run` 记录实际账号 | `app/services/crawl_service.py` | 指定账号生效 |
| 4.3 | 采集页支持选账号 | `web/src/pages/Crawl.vue` | UI 可选 |
| 4.4 | 补测试：任务指定账号 / 未指定时的选择策略 | `tests/test_crawl_multi_account.py` | 全绿 |

### 阶段 5：前端账号维度（P1）

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 5.1 | 素材库加**账号筛选**（平台 tab + 账号 chips，沿用关注博主页模式） | `web/src/pages/Assets.vue` | 可切账号 |
| 5.2 | 看板加账号维度（或至少账号过滤） | `web/src/pages/Overview.vue`、`analytics.py` | 数据分开 |
| 5.3 | 账号中心：卡片显示 `external_id` 尾号 + 平台，**同平台多号能一眼区分** | `web/src/pages/Accounts.vue` | 不混淆 |
| 5.4 | 关注博主页已正确（平台 tab + 账号 chips）——**保持**，作为其他页的样板 | — | — |

### 阶段 6：长期稳定（P2，与隔离解耦，可并行）

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 6.1 | **B站按账号自动刷新**（§2.1 六步算法），体检发现 need_refresh 即刷新回写 | `app/modules/bilibili.py`、`app/services/` | 刷新后凭证更新、`buvid3` 保留 |
| 6.2 | B站 `_cookies()` 补 `ac_time_value`（与 bili-sync 对齐） | `app/modules/bilibili.py` | 一致 |
| 6.3 | **抖音守恒**落注释 + 测试（整串 cookie 不被裁剪、参数模板不被随机化） | `app/modules/douyin.py`、`tests/` | 测试锁死行为 |
| 6.4 | 抖音 cookie 域过滤核查：确保抓取时整串不漏域 | `app/modules/browser_qr.py` | 整串完整 |

### 依赖与顺序

```
阶段1(能加) ──> 阶段2(分得开) ──> 阶段3(删得净)
                     │
                     └──> 阶段5(前端维度)
阶段4(采集带账号) 依赖 阶段1
阶段6(长期稳定) 与 2/3/4/5 并行，互不阻塞
```

**最小可用闭环 = 阶段 1 + 阶段 2**：能加第二个账号，且两个账号的内容分得开、筛得出。

---

## 6. 后续改造升级路线（版本视角）

把 §5 的阶段打包成可交付的版本，给出"到哪一步达成什么"。

| 版本 | 内容 | 达成状态 | 依赖 |
|---|---|---|---|
| **v0.1 · 能加号** | 阶段 1（身份与去重 A/B/C + 测试） | 同平台可加任意多个账号，互不覆盖 | — |
| **v0.2 · 分得开** | 阶段 2（`content_account_link` + 回填 + API/看板账号维度） | 每个账号的内容/收藏夹可独立查看与筛选 | v0.1 |
| **v0.3 · 删得净** | 阶段 3（孤儿治理 + SQLite FK 一致）+ 阶段 4（采集带账号） | 删账号不留孤儿；采集可用指定账号 | v0.2 |
| **v0.4 · 看得到** | 阶段 5（前端账号筛选、账号卡片区分） | 全部前端页面支持账号维度 | v0.2 |
| **v0.5 · 长期稳** | 阶段 6（B站自动刷新 + 抖音守恒锁死） | B站可无人值守续期；抖音行为被测试锁死 | 独立 |

**验收基线（每个版本都要过）**：`uv run pytest tests/ -q` 全绿 + 前端 `npm run build` 成功 +
（涉及 UI 的）真机走一遍多账号流程。

**里程碑建议**：先交付 **v0.1 + v0.2**（最小可用闭环），确认多账号真的"加得进、分得开"，
再按 v0.3/v0.4 补全，v0.5 可随时插入（与隔离解耦）。

---

## 7. 红线（不可违反）

1. **凭证始终加密**；刷新/回写走既有 `_save_credential`，绝不落明文。
2. **每账号独立**：刷新、体检、同步都用该账号自己的凭证，禁止跨账号复用设备标识。
3. **不做持久化浏览器 profile**（多账号会争用/串号，且两个上游都证明不需要）。
4. **抖音保持守恒**——任何"主动变更设备身份"的改动都必须先回到本文档论证。
5. 上游代码/算法照抄须保留来源署名（B站刷新算法源自 bili-sync，MIT）。
6. **内容全局唯一、归属多对多**：`content_item` **不加** `account_id`；账号归属一律经
   `content_account_link` 表达。任何"给内容直接挂一个 account_id"的改动都是错的（同一条视频可被多个号收）。
7. **账号身份只认平台返回的稳定 ID**：禁止用"该平台只有一个账号"之类启发式来猜归属。
8. **每账号数据必须可独立查询**：新增任何内容/任务类 API 都要考虑 `account_id` 过滤维度。
