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

### 5. 限速 / 节流 —— 逐条照抄抖小云

抖小云长期稳定、无风控，靠的不是什么签名，而是**随机间隔 + 单批上限**。
`job/DouyinBasicSyncJob.cs` 原文：

```csharp
await Task.Delay(_random.Next(2, 10) * 1000);  // 翻页之间（line 467）
await Task.Delay(_random.Next(1, 4) * 1000);   // 每次下载前（line 914）
await Task.Delay(_random.Next(2, 10) * 1000);  // 动态/图文之间（line 1014）
```

`IsSyncLimitReached`：单次同步**最多 30 条**，剩下的等下次任务继续
（收藏合集另有 `config.BatchCount`，默认 18、上限 30）。

我们照做：

| 项 | 抖小云 | 我们 |
|---|---|---|
| 翻页间隔 | 2~10s 随机 | `_pace()`：`_PAGE_DELAY_RANGE=(2,10)` 随机，每个翻页循环后调用 |
| 单批上限 | 30 条 | `_BATCH_LIMIT = 30`，`_fetch_paged` / `_fetch_creator_videos` 生效 |
| 下载间隔 | 1~4s 随机 | （下载队列已有节流，见 handlers） |
| 同步限速 | 全局令牌桶 `SlidingWindowRateLimiter` | `fetch_cache` + 风控冷却 |
| 连接 | `MaxConnectionsPerServer=8`，超时 60s | httpx 默认池，超时 15s |
| 下载超时 | 5 分钟 | 300s |

测试注意：`_pace()` 每次调用都重读模块级 `_PAGE_DELAY_RANGE`，
测试用 autouse fixture 把它置 0 即可跳过真实等待（不要用默认参数绑定，那样改不动）。

### 6. 订阅源类型 —— 补齐抖小云的全部入口

抖小云 `VideoTypeEnum` 的同步入口我们逐条搬了过来：

| 抖小云 VideoType | 我们 source_type | 接口 |
|---|---|---|
| dy_favorite（喜欢的/点赞） | `like` | `/aweme/v1/web/aweme/favorite` |
| dy_collects（收藏） | `favorite` | POST `/aweme/v1/web/aweme/listcollection` |
| dy_custom_collect（自定义收藏夹） | `collect_folder` | `/aweme/v1/web/collects/list` + `/collects/video/list` |
| dy_mix（收藏合集） | `collection` | `/aweme/v1/web/mix/listcollection` + `/mix/aweme` |
| dy_series（收藏短剧） | `series` | `/aweme/v1/web/series/collections` + `/series/aweme` |
| dy_follows / dy_followuser（关注的博主） | `creator` | `/aweme/v1/web/aweme/post` |

六套参数模板（`COLLECT_FOLDER_LIST_PARAMS` / `COLLECT_FOLDER_VIDEO_PARAMS` /
`MIX_LIST_PARAMS` / `MIX_VIDEO_PARAMS` / `SERIES_LIST_PARAMS` / `SERIES_VIDEO_PARAMS`）
逐项照抄 `DouyinRequestParamManager`，路径一律**无尾斜杠**。
统一映射函数 `_aweme_to_item`，分页走 `_fetch_paged`（服务端 cursor + 30 上限 + 随机间隔）。
前端「新建跟踪」的来源类型按平台动态出选项（抖音 6 种 / B站 3 种）。

## 修完移除的“多余发明”

- ❌ 删除 `app/modules/douyin_browser_fetch.py`（浏览器同源 fetch 兜底）——不需要
- ❌ 博主视频不再默认算 a_bogus —— 抖小云不算，路径对就行
- ✅ 仅在真被 Argus 拦时才**真算一次签名重试**（保险，正常不会走到）

## 验证

- `pytest 451 passed`
- 真机实测三个端点（博主视频 / 关注 / 收藏）全部 **200**，不再是 403
- 回归测试锁定：路径不得带尾斜杠；`extra.download_url` 优先直链下载

## 前端渲染

功能对齐抖小云，前端保持我们自己的 V3 包豪斯风格，无需照搬它的 UI。
