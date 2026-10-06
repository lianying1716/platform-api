"""关键词采集统一入口（PRD 第二阶段：抓取能力抽离进 modules/）。

优先用纯 Python 签名直接 HTTP 抓取（bilibili/douyin/xhs/kuaishou，见 client.py），不依赖活浏览器、
不再子进程调 MediaCrawler。对暂未抽离的平台（weibo/tieba），
若配置了 PLATFORM_MEDIACRAWLER_DIR 则回退 CLI 子进程（兼容一期）。

签名/接口逻辑来源见 client.py、bili_sign.py、douyin_sign.py、xhs_sign.py、zhihu_sign.py 各文件头
（MediaCrawler NON-COMMERCIAL LEARNING LICENSE 1.1，仅自用学习）。
"""

import logging

from app.modules.crawler import client as _client

logger = logging.getLogger("platform.crawler")

# 已抽离为纯 Python 抓取的平台
_NATIVE = {"bilibili", "douyin", "xhs", "kuaishou", "zhihu"}


async def search(
    *,
    platform: str,
    keyword: str,
    sort: str = "popular",
    limit: int = 50,
    with_comments: bool = False,
    cookie: str = "",
) -> list[dict]:
    """统一抓取入口。crawl_service 调用此函数。

    - 原生平台：纯 Python 抓取（需 cookie）。
    - 其他平台：回退 MediaCrawler 子进程（需配置目录）。
    """
    if platform in _NATIVE:
        return await _client.search(platform=platform, keyword=keyword, cookie=cookie, limit=limit, sort=sort)
    return await _search_via_subprocess(platform=platform, keyword=keyword, limit=limit)


async def fetch_comments(*, aweme_id: str, cookie: str, pages: int = 2, per_page: int = 20,
                         top_n: int = 20, interval: float = 1.0) -> list[dict]:
    """当前仅抖音支持评论抓取。"""
    return await _client.fetch_comments(
        aweme_id=aweme_id, cookie=cookie, pages=pages, per_page=per_page,
        top_n=top_n, interval=interval,
    )


async def _search_via_subprocess(*, platform: str, keyword: str, limit: int) -> list[dict]:
    """对未抽离平台的兼容回退：CLI 子进程调 MediaCrawler。"""
    import asyncio
    import json
    from pathlib import Path

    from app.config import settings

    _MAP = {"weibo": "wb", "tieba": "tieba"}
    mc_platform = _MAP.get(platform)
    if mc_platform is None:
        raise ValueError(f"不支持的采集平台: {platform}")
    mc_dir = settings.mediacrawler_dir
    if not mc_dir or not Path(mc_dir).is_dir():
        raise RuntimeError(
            f"{platform} 抓取尚未抽离为纯 Python，需配置 PLATFORM_MEDIACRAWLER_DIR 走子进程兜底"
        )
    cmd = ["uv", "run", "main.py", "--platform", mc_platform, "--type", "search",
           "--keywords", keyword, "--save_data_option", "json"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=mc_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=settings.crawl_timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("MediaCrawler 子进程执行超时")
    if proc.returncode != 0:
        raise RuntimeError(f"MediaCrawler 退出码 {proc.returncode}: {stderr.decode(errors='ignore')[:300]}")
    data_dir = Path(mc_dir) / "data" / mc_platform / "json"
    files = sorted(data_dir.glob("search_contents_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return []
    raw = json.loads(files[0].read_text(encoding="utf-8"))
    return _normalize_subprocess(raw[:limit])


def _normalize_subprocess(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        ext = str(r.get("note_id") or r.get("aweme_id") or r.get("video_id") or r.get("id") or "")
        if not ext:
            continue
        out.append({
            "external_id": ext, "content_type": "video",
            "title": r.get("title") or r.get("desc") or "",
            "author_name": r.get("nickname") or r.get("user_name") or "",
            "author_id": str(r.get("user_id") or r.get("sec_uid") or ""),
            "url": r.get("note_url") or r.get("aweme_url") or r.get("video_url") or "",
            "cover_url": r.get("cover_url") or "",
            "metrics": {
                "play": int(r.get("video_play_count") or 0),
                "like": int(r.get("liked_count") or r.get("digg_count") or 0),
                "comment": int(r.get("comment_count") or 0),
            },
        })
    return out
