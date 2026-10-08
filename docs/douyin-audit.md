# 抖音链路审计（对照抖小云 dysync.net）

逐环节实测对照 dysync.net 的抖音实现，结论如下。

## 结论总览

| # | 环节 | 抖小云做法 | 我们的做法 | 实测结论 |
|---|---|---|---|---|
| 1 | 扫码登录 | 手动填 Cookie | 浏览器扫码 + 多信号判定 | ✅ 可用 |
| 2 | 登录后取身份 | Cookie 里自带 SecUserId | 页面 self profile 取 | ✅ 已修 |
| 3 | 体检/探活 | `following/list` 或收藏列表 | 同左 | ✅ 形态被平台接受 |
| 4 | 收藏列表 | `POST aweme/listcollection` | 同左（POST） | ✅ 形态被接受 |
| 5 | 关注列表 | `GET user/following/list` | 同左 | ✅ 形态被接受 |
| 6 | 博主主页视频 | `GET aweme/post`（带 uifid + 签名） | 纯 HTTP 被拦 → **浏览器同源 fetch 兜底** | ✅ 已打通 |
| 7 | 下载 | yt-dlp | 同左 | ✅ 已修（原先必崩） |
| 8 | 评论抓取 | — | `GET comment/list` + a_bogus | ✅ 形态被接受 |

## 已修的两个必现缺陷

### 1. 下载 handler 每次必崩

`handle_download` 用 `result.get("video_file")` 读返回值，
但 `media_download.download()` 返回的是 **dataclass** `DownloadResult`，没有 `.get()`：

```
AttributeError: 'DownloadResult' object has no attribute 'get'
```

**影响**：所有平台、所有下载都失败。测试没发现是因为 `test_pipeline_e2e`
把 `download` mock 成了 dict，正好掩盖类型不一致。

**修复**：改属性访问；并把 mock 换成真实 `DownloadResult`；失败如实记 `error`。

### 2. 下载没带账号 Cookie

yt-dlp 的 Douyin extractor 明确要求 fresh cookies：

```
ERROR: [Douyin] ...: Fresh cookies (not necessarily logged in) are needed
```

`handle_download` 从来没把 cookie 传给 `download()`。**修复**：按平台取在线账号 Cookie 传入。

## 博主主页视频：已用浏览器兜底打通（原为 403）

`GET /aweme/v1/web/aweme/post/` 实测返回：

```
403 Blocked by ArgusSecurityPlugin Uifid Not Found
```

对照真实浏览器会话抓到的同接口请求，平台实际发送了这些我们**没有**的东西：

| 字段 | 真实请求 | 我们 |
|---|---|---|
| `uifid` **参数** | `3c3e9d4a...`（取自 cookie `UIFID_TEMP`） | 缺失 |
| `uifid` **请求头** | 同值 | 缺失 |
| `msToken` | localStorage `xmst` | 缺失 |
| `webid` | 页面生成 | 缺失 |
| `verifyFp` / `fp` | `verify_xxx` | 缺失 |

补齐 `uifid`（参数+头）后，错误从 `Uifid Not Found` 变成
`Signature Not Found` —— 说明还有一层 `x-secsdk-web-signature` 之类的签名没算。

### 解法（已实现）

在真实页面里用**同源 fetch** 调同一个接口，平台的 SDK 会自己补齐全部头与签名。
实测：把账号已存的 Cookie 注入一个全新无头浏览器上下文，打开抖音首页后
`page.evaluate` 发同源 fetch，返回 **200**（不再是 403）。

实现：`app/modules/douyin_browser_fetch.py`；
在 `douyin._fetch_creator_videos` 里，一旦发现 `403 / 412 / ArgusSecurityPlugin`
就自动回退浏览器路径（仅在纯 HTTP 被拦时走，避免每次都起浏览器）。

**当前状态**：抖音**收藏列表同步**与**博主主页视频同步**都可用；
代价是后者每次调用要起一次无头浏览器（数秒）。

### 为什么不能纯 Python 解决

真实请求带的 `x-secsdk-web-signature` 由页面安全 SDK 在运行时生成，
纯 Python 复刻成本高且随平台更新失效；浏览器兜底更稳。

> 抖小云能用纯 HTTP，是因为它同步的是收藏/关注等**不需要 secsdk** 的接口；
> 博主主页视频这条链路它同样依赖完整 Cookie（含 `UIFID`）。
