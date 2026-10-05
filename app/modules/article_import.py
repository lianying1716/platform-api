"""单篇微信公众号文章导入（协议来源：wechat-article-exporter，MIT，仅取"单篇"能力）。

背景：微信已于 2026-07 关闭"跨公众号历史文章列表"后台接口（ret=200013），
因此本模块**只做单篇文章**：给一个 mp.weixin.qq.com/s... 的公开链接，抓正文入库。
不做列表、不做订阅、不做公开搜索发现。

安全：只允许微信域名 + https + 文章路径；拒绝重定向（防 SSRF）。
"""
import hashlib
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("platform.article_import")

_ALLOWED_HOSTS = {"mp.weixin.qq.com", "weixin.qq.com"}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ArticleImportError(ValueError):
    """URL 非法或文章页无法解析。"""


def normalize_article_url(raw: str) -> str:
    """校验并规范化文章 URL；非微信/https/文章路径一律拒绝。"""
    raw = (raw or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ArticleImportError("仅支持 https 链接")
    if parsed.hostname not in _ALLOWED_HOSTS:
        raise ArticleImportError("仅支持微信公众号文章链接（mp.weixin.qq.com）")
    if not (parsed.path == "/s" or parsed.path.startswith("/s/")):
        raise ArticleImportError("仅支持微信公众号文章链接（/s...）")
    return parsed._replace(fragment="").geturl()


def article_external_id(url: str) -> str:
    """稳定去重 ID：优先 sn，其次 mid+idx，其次路径 slug，最后 URL 哈希。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if qs.get("sn"):
        return qs["sn"][0]
    if qs.get("mid"):
        return f"{qs['mid'][0]}_{qs.get('idx', ['1'])[0]}"
    slug = parsed.path.removeprefix("/s/").strip("/")
    if slug:
        return slug
    return hashlib.sha1(url.encode()).hexdigest()[:32]


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


def _var(html: str, name: str) -> str:
    m = re.search(rf"var\s+{name}\s*=\s*[\"']([^\"']+)[\"']", html)
    return m.group(1).strip() if m else ""


def _parse_time(html: str, soup: BeautifulSoup) -> datetime | None:
    m = re.search(r"var\s+ct\s*=\s*[\"'](\d{9,11})[\"']", html)
    if m:
        return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc).replace(tzinfo=None)
    iso = _meta(soup, "article:published_time")
    if iso:
        try:
            return datetime.fromisoformat(iso).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def parse_article_html(html: str, url: str) -> dict:
    """从文章页 HTML 提取统一 item。"""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find(id="js_content")
    if content is None:
        raise ArticleImportError("未找到文章正文（可能链接失效或被风控拦截）")

    title = ""
    h1 = soup.find(id="activity-name")
    if h1:
        title = h1.get_text(strip=True)
    title = title or _meta(soup, "og:title")

    author_name = ""
    name_el = soup.find(id="js_name")
    if name_el:
        author_name = name_el.get_text(strip=True)
    author_name = author_name or _var(html, "nickname")

    # 正文里的图片补上 data-src（微信懒加载），保证导出可用
    for img in content.find_all("img"):
        if img.get("data-src") and not img.get("src"):
            img["src"] = img["data-src"]

    text = content.get_text("\n", strip=True)

    return {
        "external_id": article_external_id(url),
        "title": title or "未命名文章",
        "author_name": author_name,
        "author_id": _var(html, "biz"),
        "url": url,
        "cover_url": _meta(soup, "og:image"),
        "published_at": _parse_time(html, soup),
        "text": text,
        "html": str(content),
    }


async def fetch_article(url: str, *, cookie: str = "") -> dict:
    """抓取并解析单篇文章。拒绝重定向（防 SSRF）。"""
    url = normalize_article_url(url)
    headers = {
        "User-Agent": _UA,
        "Referer": "https://mp.weixin.qq.com/",
        "Origin": "https://mp.weixin.qq.com",
    }
    if cookie:
        headers["Cookie"] = cookie
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        resp = await client.get(url, headers=headers)
    if 300 <= resp.status_code < 400:
        raise ArticleImportError("目标链接发生重定向，已拒绝")
    resp.raise_for_status()
    item = parse_article_html(resp.text, url)
    logger.info("解析文章成功: %s", item["title"])
    return item
