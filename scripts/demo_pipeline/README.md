# 演示素材生成管线

为演示站（`web/dist-demo`）与宣传片生产**真实**素材：真转录、真 AI 分析、真封面。

> 代码里没有任何内置密钥或本机路径：一律走环境变量，缺了就报错退出。
> 中间产物默认落在 `data/demo_pipeline/`（已被 `.gitignore` 覆盖）。

## 环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `PLATFORM_ROOT` | 仓库根目录 | 由脚本位置反推 |
| `DEMO_STORAGE` | 真实视频所在目录（`transcribe_batch`） | `<root>/data/demo_storage` |
| `DEMO_BUILD_DIR` | 中间产物目录 | `<root>/data/demo_pipeline` |
| `COVER_CACHE` | 封面缓存目录 | `<root>/data/cover_cache` |
| `PLATFORM_DATABASE_URL` | PostgreSQL DSN | 本地 `127.0.0.1:5433/platform` |
| `ANALYZE_API_KEY` | 分析/拆解用的大模型 Key | 必填 |
| `ANALYZE_BASE_URL` / `ANALYZE_MODEL` | 分析接口（OpenAI 兼容） | 星算 AI `https://api.xsai5.xyz/v1` / `deepseek-v4.1-flash-free` |
| `IMAGE_API_KEY` | 封面生成用 Key | 必填 |
| `IMAGE_BASE_URL` | 图像生成接口（OpenAI 兼容） | 星算 AI `https://api.xsai5.xyz/v1` |

## 管线顺序

```
transcribe_batch.py     → transcripts.json   （faster-whisper，CPU int8）
analyze_batch.py        → analysis.json      （分析 + 爆款拆解）
fetch_articles.py       → articles.json      （真实公众号）
fetch_article_covers.py → cover_cache/       （真实封面）
gen_covers2.py          → cover_cache/       （补缺封面）
import_articles.py      → PostgreSQL         （走平台 import 服务）
export_real_pack.py     → real_pack.json     （导出内容包）
```

导出的 `real_pack.json` 由 `scripts/seed_demo_real.py` 注入演示库；
`fetch_articles.py` 需要一份 `$DEMO_BUILD_DIR/article_urls.json`（待抓取的公众号 URL 列表）。
