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
| 6 | **博主主页视频** | `GET aweme/post`（带 uifid + 签名） | `GET aweme/post` + a_bogus | ❌ **403 Argus** |
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

## 未打通：博主主页视频（创作者订阅同步的关键）

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

**因此当前状态**：抖音的**收藏列表同步**（走 `listcollection`）可用；
**博主主页视频同步**（走 `aweme/post`）在补齐 uifid 与 secsdk 签名之前不可用。

### 可选解法（按成本排序）

1. **复用浏览器上下文发请求**（最稳）：已登录的 Playwright 页面里
   `page.evaluate` 发同源 fetch，平台自己会带上全部头与签名。
   代价：每条同步要多一次浏览器往返，慢。
2. **补齐 uifid + msToken + webid + verifyFp**，再看是否还需要 secsdk 签名。
3. 跟抖小云一样，把博主视频同步当作"需要用户自己确认可用"的降级能力。

> 抖小云能用是因为它全程带着用户从浏览器复制的完整 Cookie（含 `UIFID` 等），
> 且它**不下载博主视频列表用于抓取**——它同步的是收藏/关注等不需要 secsdk 的接口。
