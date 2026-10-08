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

## 4. 实施顺序（建议）

| 优先级 | 事项 | 理由 |
|---|---|---|
| **P0** | 修缺陷 A（静默覆盖）+ B（多账号阻塞） | 数据损坏 + 功能不可用，当前就影响你 |
| **P1** | 修缺陷 C（手动录入去重） | 防重复账号 |
| **P1** | B站按账号自动刷新 | 唯一真正的"长期稳定"，你反复强调 |
| **P2** | 抖音守恒清单落注释/测试（防未来误"优化"） | 防止再走弯路 |
| **P2** | B站 `_cookies()` 补 `ac_time_value` | 与 bili-sync 对齐（低成本） |
| **P3** | 抖音 cookie 域过滤核查（整串是否被裁剪） | 守恒的细节验证 |

---

## 5. 红线（不可违反）

1. **凭证始终加密**；刷新/回写走既有 `_save_credential`，绝不落明文。
2. **每账号独立**：刷新、体检、同步都用该账号自己的凭证，禁止跨账号复用设备标识。
3. **不做持久化浏览器 profile**（多账号会争用/串号，且两个上游都证明不需要）。
4. **抖音保持守恒**——任何"主动变更设备身份"的改动都必须先回到本文档论证。
5. 上游代码/算法照抄须保留来源署名（B站刷新算法源自 bili-sync，MIT）。
