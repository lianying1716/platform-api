# 账号长期稳定性 与 多账号 设计

> 日期：2026-10-09 · 状态：**待实施；已过对抗性审查；多账号产品决策已拍板**（本文是后续开发的唯一设计依据）
> 来源：对 `bili-sync`（B站）与 `dysync.net`/抖小云（抖音）的源码对照调研 + 本项目代码实测。
> 前置阅读：`docs/license-and-usage-compliance.md`（上游许可）。
> 审查：2026-10-09 经事实核查、数据模型攻击、改造路线攻击三轮子智能体审查；结论已并入本文。
> **阶段 0 未完成前，不得进入阶段 2；已发布的 Alembic revision 不得回改。**
> **已拍板：同内容跨账号 = 独立素材；未指定账号 = 轮询 + 随机打散，不能总用同一个；
> 账号/订阅变动不自动删除素材，素材只允许用户手动删除或外部 Agent 二次确认删除。**

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

**已拍板的产品模型**：账号是素材所有权边界。同一视频被两个账号采集时，产生**两条独立素材**，
各自下载、转写、分析、加工；同一账号内由不同订阅看到同一视频，仍合并为该账号的一条内容，
但保留多条来源记录。去重键因此是 `(platform, external_id, account_id)`，不是全局唯一。

> **注意：当前数据库没有 `account_id` 维度**。`content_item` 只有一个全局 `source_id`，
> 去重又只靠应用层 `SELECT`；同一视频被 A/B 采集时会塌成一条。阶段 0 必须先加账号维度，
> 再谈素材隔离。

### 3.3 修正方案

| 缺陷 | 修正 |
|---|---|
| A：静默覆盖 | `qr_login_poll` **只允许**在 `poll.external_id` 非空、且与现有账号 `external_id` 精确一致时更新该账号；身份为空或匹配不到时一律新建或明确拒绝。**删除“现有 external_id 为空就复用”分支**——它会把“新账号”误判成“旧账号回填”，重新制造覆盖缺陷。 |
| B：加不进账号 | 先对每个平台做“稳定身份可获取性实测”；能拿到的必须在登录/体检后回填（抖音已有 `sec_uid`/`uid` 与 `sid_tt` 兜底，小红书当前 `check()` 已返回 `user_id`，但扫码路径尚未调用 `check()`）。拿不到的**拒绝建号并提示改用可识别凭证**，不承诺“所有平台都必然拿得到”。 |
| C：手动不去重 | `create_account` 按 `(platform, external_id)` 查重：命中**更新**该账号凭证，未命中才新建。 |
| D：采集不绑账号 | `crawl_task` 加可选 `account_id`；未指定时在“该平台所有可用户”中按 `last_used_at NULLS FIRST + 随机` 选取，保证轮询不总用同一个；一次 run 固定一个账号并写入 `crawl_run.account_id`。 |

**核心原则**：**账号身份必须由平台返回的稳定 ID 决定，绝不靠"该平台只有一个账号"或"旧账号没有 ID"来猜。**

---

## 4. 数据隔离现状（二次深挖，实测）

上一节解决"**加得进**"，本节解决"**分得开**"——即累加账号后数据怎么隔离。

### 4.1 现状：25 张业务表里只有 `subscription` 是内容归属边界

> 线上 PG 共 26 张表 = 25 张业务表 + `alembic_version`。带 `account_id` 的业务表有 3 张：
> `account_credential`、`credential_audit_log`、`subscription`；其中只有 `subscription`
> 真正决定“内容属于哪个账号”。早期“26 张里只有 1 张带 account_id”的表述不准确，已修正。

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

**缺陷 E：同一视频被多账号收藏时，后采集的账号素材被吞掉**

已拍板“账号级独立素材”，但当前 `content_item` 没有 `account_id`，去重键只有
`(platform, external_id)`，且唯一性靠应用层 `SELECT` 保证（并发会破），于是：

```
B 同步同一视频 V1 时：
  去重命中已存在内容？ True | source_kind/source_id = subscription 1
  → B 的订阅关系【不会】被记录；内容只归属 A 的订阅
```

**后果**：B 的素材、下载、转写、分析全部丢失，前端按 B 过滤时看不到这条。这是与已拍板
产品模型正面冲突的缺陷，不是“归属没记全”这么轻。

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

### 4.3 目标模型：账号级独立素材 + 账号内来源合并

```
platform_account ──1:N──> subscription
      │ 1:N                     │
      │                         │ 产生来源记录
      │                         ▼
      │             content_source(content_id, subscription_id, ...)
      │
      ▼
content_item(platform, external_id, account_id)
   UNIQUE(platform, external_id, account_id)
      │ 1:1
      ▼
media_asset / transcript / analysis_result / video_understanding
```

- `content_item`：新增 **`account_id NOT NULL`** FK；唯一约束为
  `UNIQUE(platform, external_id, account_id)`。内容本身带账号所有权，账号筛选不再依赖 join。
  历史迁移若出现无法可靠归属的旧行，不放开 schema 约束，而是为每个平台创建一个
  `is_system=true` 的隐藏“历史占位账号”承接；新写入仍必须使用真实账号。
- 同一账号内：不同订阅（收藏夹 / 关注博主 / 合集）收到同一视频时，`content_item` 仍只一条；
  新建 `content_source(content_id, subscription_id NULL, source_kind, source_ref,
  first_seen_at, last_seen_at, active)` 记录全部来源，订阅粒度唯一。
- 跨账号：A、B 各有一条 `content_item`，各自 `media_asset`、转录、分析、加工和下载，
  真正“独立素材”。下载/加工幂等键必须包含 `account_id`，不能只按 `content_id`。
- `creator_profile` 保持全局唯一：博主资料属于平台公开信息，与账号所有权无关，
  两份独立素材可共享同一份博主资料。
- `crawl` / `import` 来源：同样落到 `content_item.account_id`；
  关键词采集使用本次 run 选定的账号，文章导入必须有明确账号或显式“系统归属”。

**删除与保留语义（按产品决定，不使用 CASCADE 自动删素材）**：

| 操作 | `content_item` / 素材 | `content_source` |
|---|---|---|
| 取消收藏 | **保留素材**，前端不删除 | 对应来源标记 `active=false`，保留历史 |
| 删订阅 | **保留素材**，前端不删除 | 保留来源历史，标记失效 |
| 删账号 | **保留素材**；账号软删除，不物理 CASCADE 素材 | 保留，归属仍指向已停用账号 |
| 手动删除素材 | 仅在用户明确操作时删除 | 随内容删除 |
| 外部 Agent 删除 | 必须二次确认，记录审计日志后才删除 | 随内容删除 |

**计数口径**：账号视图按 `content_item.account_id` 直接计数；全局视图按
`(platform, external_id)` 去重计数。同一视频被 A/B 收藏时，全局算 1 条内容、账号视图算 2 份素材。

---

## 5. 完整改造清单

目标：**多账号“加得进、分得开、删得可控、长期稳”**。分 7 个阶段（阶段 0 为前置门禁），
每个阶段只有在“依赖已完成 + 迁移验收在真实 PG 上执行过”后才算独立验收。
缺陷编号沿用上文（A/B/C/D 见 §3.3，E/F/G 见 §4）；对抗性审查整改项标为 R1–R3。

### 阶段 0：账号级唯一性与迁移地基（P0 门禁，先于一切）

> 不做这一步，账号级素材模型没有数据库唯一性托底，必须停止。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| R1 | 给 `content_item` 加 `account_id`（历史行按 `source_kind='subscription'` 回填；无法可靠归属的按平台挂到隐藏 `is_system=true` 历史占位账号，不硬猜、不留空）；新增唯一约束 `(platform, external_id, account_id)`；入库改 DB upsert，不再依赖裸 SELECT | `app/models.py`、新迁移 `0012`、三条写入路径 | 迁移在真实 PG 上先分类回填再建约束；A/B 采集同视频得到两条内容；历史占位账号不出现在普通账号列表 |
| R2 | Alembic 迁移集成测试：真实 PG 上跑 `upgrade head` → 灌旧数据 → 断言回填 → `downgrade`；测试不能只靠 `create_all` | `tests/`、CI | 测试与生产同路径执行迁移 |
| R3 | 迁移安全策略：全表回填/加约束说明锁影响，分批 + `lock_timeout`（或明确维护窗口） | `alembic/versions/` | 有真实数据规模的执行记录 |

### 阶段 1：账号身份与去重（P0，先做）

> 不解此阶段，第二/第三个账号加不进或互相覆盖。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 1.1 | 修**缺陷 A**：`qr_login_poll` 只允许 `poll.external_id` 非空且与现有账号精确一致时更新；**删除“现有 external_id 为空就复用”**。身份为空时绝不匹配现有账号；是否允许首个无身份账号由产品决定，第二个起必须拒绝并提示 | `app/api/accounts.py` | 加 B 后 A 完好、B 独立成行；空身份不会命中旧账号 |
| 1.2 | 修**缺陷 B**：先做各平台“稳定身份可获取性实测”，能拿到的登录后回填；拿不到的明确拒绝建号并提示 | `app/modules/{douyin,xhs,crawl_platforms,wechat_mp}.py`、`app/api/accounts.py` | 同平台可连续加 3 个可识别账号；不可识别平台有明确错误 |
| 1.3 | 修**缺陷 C**：`create_account` 按 `(platform, external_id)` 查重，命中**更新**该账号凭证 | `app/api/accounts.py` | 重复录入同一号 = 更新而非新增 |
| 1.4 | 账号删除前检查：有订阅/内容时**明确提示**；按产品决定默认软删除账号，不物理删素材 | `app/api/accounts.py` | 删账号有提示，素材仍保留 |
| 1.5 | 体检/扫码拿到的稳定身份与库中不一致时显式处理：更新或标记冲突，不能只回填空值 | `app/api/accounts.py` | 脏 `external_id` 有修正路径 |
| 1.6 | 补测试：`tests/test_multi_account.py`（A 不覆盖、B 可累加、C 去重、脏 ID 修正） | `tests/` | 四个场景全绿 |

### 阶段 2：账号级独立素材（P0，隔离核心）

> 不解此阶段，加了账号也“分不开”，B 的素材会被 A 吞掉。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 2.1 | 新增 `content_source(content_id, subscription_id NULL, source_kind, source_ref, first_seen_at, last_seen_at, active)`；订阅粒度唯一 `(content_id, subscription_id) WHERE subscription_id IS NOT NULL`；`content_item.account_id` 成为素材所有权主键的一部分 | `app/models.py`、新迁移 `0013_*` | 迁移可 upgrade/downgrade |
| 2.2 | 订阅、**关键词采集、文章导入**三条写入路径都按 `(platform, external_id, account_id)` upsert；内容已存在时补写/更新 `content_source`，不吞掉别账号素材 | `sync_service.py`、`crawl_service.py`、`import_service.py` | A/B 同视频 → 两条 content、各自素材；同账号两订阅 → 一条 content + 两条 source |
| 2.3 | 回填历史：仅 `source_kind='subscription' AND source_id IS NOT NULL` 能可靠映射账号；无法可靠映射的历史 crawl/import 挂到该平台隐藏历史占位账号，禁止硬猜；回填前后按 `source_kind` 分类断言 | 迁移 `0013` | 无跨 ID 空间错配；历史内容不消失且不污染真实账号视图 |
| 2.4 | 下载、转录、分析、加工的幂等键改为 `(account_id, content_id)`；防止 A/B 独立素材共用同一媒体文件或被错误去重 | `workers/handlers.py`、`services/` | A/B 各自下载、加工互不覆盖 |
| 2.5 | 内容查询与看板直接按 `content_item.account_id` 过滤；账号视图按 content 计数，全局视图按 `(platform, external_id)` 去重计数；保持旧参数向后兼容并同步前端 TS/MCP/外部调用说明 | `app/api/assets.py`、`app/api/analytics.py`、`web/src/pages/` | 不重复计数、分页正确、API 契约同批更新 |
| 2.6 | 补测试：A/B 同视频 → 两份独立素材和媒体；同账号多订阅不塌行；crawl/import 可见；按 account 过滤互不串 | `tests/test_content_multi_account.py` | 全绿 |

> **交付边界**：阶段 2 的 `account_id` 唯一约束、`content_source` 和回填必须一次交付；
> 阶段 3 的保留/删除策略另起 0014，不能回改已发布的 0013。

### 阶段 3：保留策略与删除审计（P1）

> 保证“订阅/账号变动不丢素材；删除必须显式且有审计”。

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 3.1 | 修**缺陷 F**：`content_item.source_id` / `content_source` 只作溯源；账号删除改为**软删除**（`status=deleted`、保留凭证密文或按策略清除），素材与 `content_item` 不物理级联 | `app/models.py`、迁移 `0014_*`、`app/api/accounts.py` | 删账号后素材仍在，前端不消失 |
| 3.2 | 统一 SQLite `PRAGMA foreign_keys=ON`，并补测试确认删除语义是“保留”而非 CASCADE | `app/db.py` | 测试能验出保留行为 |
| 3.3 | 取消收藏/删订阅：`content_source.active=false`，素材与内容保留；前端默认仍展示，可标记“来源已失效”但不删除 | `app/services/sync_service.py`、前端 | 取消收藏后素材仍可查 |
| 3.4 | 素材删除唯一入口：用户手动删除；外部 Agent 删除必须 `confirm=true` + 记录 `deletion_audit`（谁、何时、原因、content_id/account_id） | `app/api/assets.py`、`app/api/skills.py`、迁移 `0014_*` | 无审计不删除；确认后才删 |
| 3.5 | 账号列表/筛选默认排除 `status=deleted` 与 `is_system=true`；提供“已删除账号/历史占位素材”归档视图 | `app/api/accounts.py`、前端 | 软删账号与历史占位不污染日常视图，但素材可追溯 |

### 阶段 4：采集与任务带账号（P1，修缺陷 D）

| # | 改动 | 涉及文件 | 验收 |
|---|---|---|---|
| 4.1 | `crawl_task` 加可选 `account_id`（FK，SET NULL） | `app/models.py`、迁移 `0015_*` | 迁移 OK |
| 4.2 | `_platform_cookie()` 改为：优先任务指定账号 → 否则对本平台可用账号按 `last_crawl_used_at NULLS FIRST + random()` 轮询，**不得固定 id 最小账号**；一次 run 固定账号并写 `crawl_run.account_id` + 更新该账号 `last_crawl_used_at` | `app/models.py`、`app/services/crawl_service.py` | 连续 N 次未指定任务覆盖所有健康账号；不会只用同一个 |
| 4.3 | 采集页支持选账号 | `web/src/pages/Crawl.vue` | UI 可选 |
| 4.4 | 补测试：任务指定账号；未指定时多轮轮询覆盖所有健康账号；单账号平台退化行为 | `tests/test_crawl_multi_account.py` | 全绿 |

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
阶段0(唯一性/迁移地基) ──> 阶段1(能加) ──> 阶段2(分得开) ──> 阶段3(保留与删除审计)
                                                   │
                                                   ├──> 阶段4(采集显式账号)
                                                   └──> 阶段5(前端维度)
阶段6(长期稳定) 与阶段 0–5 均可并行，互不阻塞
```

**最小可靠闭环 = 阶段 0 + 阶段 1 + 阶段 2**：先有数据库唯一性，才能可靠地做到
“能加第二个账号，且两个账号的内容分得开、筛得出”。阶段 2 与阶段 3 不得共用同一个迁移号。
阶段 3 的目标不是“删得净”，而是“该留的绝不误删、该删的必须显式且有审计”。

---

## 6. 后续改造升级路线（版本视角）

把 §5 的阶段打包成可交付的版本，给出"到哪一步达成什么"。版本号在对抗性审查后重排，
原 v0.x 命名只保留为历史规划，不按旧编号实施。

| 版本 | 内容 | 达成状态 | 依赖 |
|---|---|---|---|
| **v0.1 · 地基稳** | 阶段 0（账号级唯一约束 + 真实 PG 迁移测试 + 锁策略） | 并发下每账号一条内容；A/B 不互相吞素材 | — |
| **v0.2 · 能加号** | 阶段 1（身份与去重 A/B/C + 脏 ID 修正 + 测试） | 同平台可加多个可识别账号，互不覆盖 | v0.1 |
| **v0.3 · 分得开** | 阶段 2（账号级 content + `content_source` + 三来源写入 + 独立媒体/加工） | 每个账号拥有独立素材；同账号多订阅不塌行 | v0.2 |
| **v0.4 · 删得稳** | 阶段 3（账号软删除 + 素材保留 + 删除审计） | 订阅/账号变动不误删素材；删除需显式确认 | v0.3 |
| **v0.5 · 采得准** | 阶段 4（采集任务显式账号 + 轮询打散 + 运行记录一致） | 可指定账号采集；未指定时公平轮换，不只用同一个 | v0.3 |
| **v0.6 · 看得到** | 阶段 5（前端账号筛选、账号卡片区分、API 兼容） | 全部前端页面支持账号维度 | v0.3 |
| **v0.7 · 长期稳** | 阶段 6（B站自动刷新 + 抖音守恒锁死） | B站可无人值守续期；抖音行为被测试锁死 | 独立 |

**验收基线（每个版本都要过）**：`uv run pytest tests/ -q` 全绿 + 前端 `npm run build` 成功 +
（涉及 UI 的）真机走一遍多账号流程 +（涉及 schema 的）真实 PG `alembic upgrade/downgrade` 集成测试。
只写“测试全绿”不构成迁移验收；测试夹具当前用 `create_all` 绕过 Alembic，必须另建迁移测试。

**里程碑建议**：先交付 **v0.1 + v0.2 + v0.3**（最小可靠闭环），确认多账号真的“加得进、分得开”；
再补 v0.4/v0.5/v0.6，长期稳定 v0.7 可随时插入（与隔离解耦）。

---

## 7. 红线（不可违反）

1. **凭证始终加密**；刷新/回写走既有 `_save_credential`，绝不落明文。
2. **每账号独立**：刷新、体检、同步都用该账号自己的凭证，禁止跨账号复用设备标识。
3. **不做持久化浏览器 profile**（多账号会争用/串号，且两个上游都证明不需要）。
4. **抖音保持守恒**——任何"主动变更设备身份"的改动都必须先回到本文档论证。
5. 上游代码/算法照抄须保留来源署名（B站刷新算法源自 bili-sync，MIT）。
6. **素材所有权按账号隔离**：`content_item.account_id NOT NULL`，唯一键
   `(platform, external_id, account_id)`。同一视频被 A/B 采集 = 两条独立内容、两套媒体与加工；
   同一账号内同一视频只一条内容，来源差异记录在 `content_source`。
7. **账号身份只认平台返回的稳定 ID**：禁止用"该平台只有一个账号"之类启发式来猜归属。
8. **每账号数据必须可独立查询**：新增任何内容/任务类 API 都要考虑 `account_id` 过滤维度。
9. **数据库唯一性必须由数据库保证**：`(platform, external_id, account_id)` 在阶段 0 补唯一约束前，
   任何回填/验收都不得假定账号级内容唯一。
10. **已发布的 Alembic revision 永不回改**：阶段 2/3/4 各用独立 revision，生产只按
    `alembic upgrade head` 前进；回滚路径必须随迁移一起交付。
11. **素材删除只允许显式触发**：取消收藏、删订阅、删账号都只停用来源/软删除账号，不删素材；
    手动删除或外部 Agent 二次确认删除除外，且必须留审计。
12. **未指定账号的采集必须公平轮换**：按最近使用时间 + 随机打散，连续运行不得固定使用同一个账号；
    单账号平台才允许退化为唯一选择。

---

## 8. 对抗性审查记录（2026-10-09）

| 审查 | 结论 |
|---|---|
| 事实核查 | 缺陷 E/F/G 属实；修正“26 张表”表述；“`(platform, external_id)` 唯一”只是产品意图，不是数据库现状；缺陷 B 的平台实现已有部分更新 |
| 数据模型攻击 | 发现 D1–D6：唯一约束缺失、link 唯一键丢订阅、回填跨 ID 空间、采集/导入不写 link、脏 `external_id` 不修正、删除语义未定义；T1–T3：取消收藏不可表达、收藏/关注语义拉平、join 计数放大 |
| 改造路线攻击 | 发现阶段 2/3 共用 0012 无法二次执行、迁移测试被 `create_all` 绕过、锁表未评估、计数口径与对外 API 兼容缺失、1.1 仍会覆盖空 ID 账号 |

**产品决策（2026-10-09 已拍板，覆盖审查时的待确认项）**：

1. **独立素材**：同一视频被 A/B 采集时各生成一条 `content_item` 和一套媒体/加工产物；
   同一账号内跨订阅收到同一视频才合并内容、保留多条 `content_source`。
2. **未指定账号的采集**：在该平台所有可用账号中按“最久未使用优先 + 随机打散”轮询，
   不能固定使用同一个；每次 run 固定账号并记录 `crawl_run.account_id`。
3. **保留素材**：取消收藏、删订阅、删账号都不删除素材，前端不删除；
   仅用户手动删除或外部 Agent 二次确认删除，且删除动作写审计日志。

**审查结论**：产品模型已定稿；阶段 0 完成并通过真实 PG 迁移验收后，才能进入阶段 2。
