# 第三方软件声明（Third-Party Notices）

本文件列出 `platform-api` 直接依赖的第三方软件及其许可证，供开源与分发时参考。
完整依赖树见 `uv.lock`；最终分发前请以锁文件解析出的实际版本为准。

## 运行时依赖

| 项目 | 用途 | 许可证 |
|---|---|---|
| FastAPI | HTTP API 框架 | MIT |
| Starlette | ASGI 基础 | BSD-3-Clause |
| Uvicorn | ASGI 服务器 | BSD-3-Clause |
| SQLAlchemy | ORM | MIT |
| Alembic | 数据库迁移 | MIT |
| PostgreSQL / psycopg2-binary | 数据库与驱动 | PostgreSQL License / LGPL（含例外） |
| httpx | HTTP 客户端 | BSD-3-Clause |
| cryptography | AES-GCM 凭证加密 | Apache-2.0 OR BSD-3-Clause |
| APScheduler | 进程内调度 | MIT |
| pydantic / pydantic-settings | 配置与校验 | MIT |
| beautifulsoup4 | HTML 解析 | MIT |
| qrcode | 二维码生成 | BSD |
| yt-dlp | 通用媒体下载 | Unlicense |
| xhshow | 小红书请求签名 | MIT |
| playwright | 受管浏览器（可选 extra: browser） | Apache-2.0 |
| playwright-stealth | 浏览器反检测（可选 extra: browser） | MIT |
| faster-whisper | 语音转录（可选 extra: transcribe） | MIT |
| mcp | MCP 服务（可选 extra: mcp） | MIT |

## 开发依赖

| 项目 | 用途 | 许可证 |
|---|---|---|
| pytest | 测试框架 | MIT |
| pytest-asyncio | 异步测试支持 | Apache-2.0 |

## 历史留痕

`platform-api` 早期版本曾参考 MediaCrawler（NON-COMMERCIAL LEARNING LICENSE 1.1）的
关键词采集协议实现。2026-10-10 起，仓库内已不含其实代码或复制资源：

- 签名改用宽松许可证依赖（`xhshow`）或由平台页面自身完成；
- 快手 GraphQL 查询文档在仓库内自行定义；
- 浏览器反检测改用 MIT 的 `playwright-stealth`。

当前主线代码不受该非商业许可约束。详细合规口径见工作台仓库
`docs/license-and-usage-compliance.md`。

## 适配器参考来源

`app/modules/` 下的各平台适配器参考（重写）了以下开源项目，许可均为宽松许可，
来源注释保留在对应源码文件头部：

| 项目 | 许可 | 参考范围 |
|---|---|---|
| [amtoaer/bili-sync](https://github.com/amtoaer/bili-sync) | MIT | B站 |
| [jianzhichu/dysync.net](https://github.com/jianzhichu/dysync.net) | MIT | 抖音 |
| [wechat-article/wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter) | MIT | 微信公众号 |

## 平台商标

前端 `web/src/brand/platforms.ts` 中的平台标识几何来自 simple-icons（CC0-1.0），
仅作"这是哪个平台"的识别性使用，不暗示任何官方合作或背书。商标归各平台所有。
