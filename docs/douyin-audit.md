# 抖音链路审计（逐项照抄抖小云 dysync.net）

结论：**已全面对齐抖小云**。之前我自作聪明搞的 a_bogus 签名、uifid 补齐、
浏览器同源 fetch 兜底 —— 全是不必要的，甚至方向是错的。真实原因简单得多。

## 真正的根因：一个尾斜杠

`/aweme/v1/web/aweme/post` 能通，`/aweme/v1/web/aweme/post/`（多一个 `/`）被拦：

```
✅ 200  /aweme/v1/web/aweme/post
❌ 403  /aweme/v1/web/aweme/post/   Blocked by ArgusSecurityPlugin Uifid Not Found
```

二分实测确认：**与签名、uifid、msToken、参数多少都无关**，只跟这个尾斜杠有关。
抖小云长期稳定，就是因为它在 `DouyinRequestParamManager` 里用的路径没有尾斜杠。
同样的坑在收藏接口也一样（`listcollection/` → 403）。

教训：报错信息（"Uifid Not Found"）会把人带偏，**先做二分定位，别顺着错误信息猜**。

## 逐项对齐清单

### 1. 请求路径 —— 无尾斜杠

| 接口 | 我们（修前） | 抖小云 / 修后 |
|---|---|---|
| 博主视频 | `/aweme/v1/web/aweme/post/` ❌ | `/aweme/v1/web/aweme/post` ✅ |
| 收藏列表 | `/aweme/v1/web/aweme/listcollection` ✅ | 同 |
| 关注列表 | `/aweme/v1/web/user/following/list` ✅ | 同 |

### 2. 参数模板 —— 整套照抄

`app/modules/douyin.py` 现在有四套模板，逐项对应抖小云的
`DouyinRequestParamManager`：

| 我们的常量 | 抖小云对应 | 项数 |
|---|---|---|
| `POST_PARAMS` | `DouyinUpderPostParams` | 48 |
| `FOLLOW_PARAMS` | `DouyinMyFollowParams` | 40 |
| `COLLECT_PARAMS` | `DouyinCollectParams` | 34 |
| `FAVORITE_PARAMS` | `DouyinFavoriteParams` | 32 |

要点：抖小云的模板里 `uifid` / `msToken` / `a_bogus` / `verifyFp` / `fp` /
`x-secsdk-web-signature` 都**显式保留空串**。**「带空键」和「完全不带」服务端处理不同**，
所以照抄时也保留空键。它**从不真正计算签名**。

### 3. 关注列表取值 —— 对齐抖小云

| 项 | 我们（修前） | 抖小云 / 修后 |
|---|---|---|
| `offset` 起点 | `1` | **`0`** |
| `source_type` | `1` | **`4`** |
| `count` | 20 | 20 |
| 翻页方式 | 自己 `offset += count` | **用服务端返回的 `offset`** |

### 4. 下载 —— 直链，不用 yt-dlp

抖小云抓取时就把 `video.play_addr.url_list` 里
`https://www.douyin.com/aweme/v1/play...` 的直链存下来，下载阶段只做一次
**带 Cookie 的 HTTP GET**。不碰 yt-dlp（抖音页面下载要求 fresh cookies，很脆）。

我们照做：
- 抓取时把直链存进 `content_item.extra["download_url"]`
- `media_download.download_direct()` 流式落盘、按 Content-Type 修扩展名
- handler 优先走直链，没有直链才回退 yt-dlp

### 5. Token / 限速 —— 同抖小云

| 项 | 抖小云 | 我们 |
|---|---|---|
| 同步限速 | 全局令牌桶 `SlidingWindowRateLimiter` | `fetch_cache` + 风控冷却 |
| 博主视频限速 | `UseAutoRateLimit` | 订阅同步间隔 + 5 分钟抓取缓存 |
| 连接 | `MaxConnectionsPerServer=8`，超时 60s | httpx 默认池，超时 15s |
| 下载超时 | 5 分钟 | 300s |

## 修完移除的“多余发明”

- ❌ 删除 `app/modules/douyin_browser_fetch.py`（浏览器同源 fetch 兜底）——不需要
- ❌ 博主视频不再默认算 a_bogus —— 抖小云不算，路径对就行
- ✅ 仅在真被 Argus 拦时才**真算一次签名重试**（保险，正常不会走到）

## 验证

- `pytest 441 passed`
- 真机实测三个端点（博主视频 / 关注 / 收藏）全部 **200**，不再是 403
- 回归测试锁定：路径不得带尾斜杠；`extra.download_url` 优先直链下载

## 前端渲染

功能对齐抖小云，前端保持我们自己的 V3 包豪斯风格，无需照搬它的 UI。
