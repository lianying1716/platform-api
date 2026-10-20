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
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("platform.article_import")

_ALLOWED_HOSTS = {"mp.weixin.qq.com", "weixin.qq.com"}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 文章展示只允许静态排版标签；脚本、表单、iframe、事件属性和非 http(s) 链接
# 一律在入库前移除。前端仍会做第二道清洗，这里负责保证 API/Skills/导出拿到的
# 数据本身是安全的。
_ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li",
    "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "img", "a",
    "figure", "figcaption", "pre", "code", "table", "thead", "tbody",
    "tr", "th", "td", "hr", "div", "span",
}
_ALLOWED_ATTRS = {"href", "src", "alt", "title", "width", "height", "class"}


def _normalize_resource_url(value: str) -> str:
    """Normalize a fetched resource URL without confusing ``//host`` with a path.

    WeChat routinely serves images as protocol-relative URLs.  Treating those
    as same-origin paths leaves the browser to request this app instead of the
    image host, and the CSP then blocks or the proxy never sees the source.
    """
    value = (value or "").strip()
    if value.startswith("//"):
        return f"https:{value}"
    return value


def _is_safe_resource_url(value: str) -> bool:
    """Match the browser's URL interpretation, not just a string prefix.

    Browsers normalize ``/\\evil.example`` and ``/\\/evil.example`` to a
    protocol-relative host.  A prefix check for ``/`` would therefore turn an
    apparent same-origin link into an external navigation.
    """
    # WHATWG URL parsers treat backslashes in a path position as slashes.
    normalized = value.replace("\\", "/")
    parsed = urlparse(normalized)
    if parsed.scheme:
        return parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)
    # Resolve exactly as the browser would.  Same-origin paths keep our host;
    # anything that gains a different host is a disguised external URL.
    resolved = urlparse(urljoin("https://article.invalid/", normalized))
    return resolved.scheme == "https" and resolved.hostname == "article.invalid"


def sanitize_article_html(content) -> str:
    """清洗文章正文，返回只含白名单标签/属性的静态 HTML。"""
    for tag in content.find_all(True):
        if tag.name not in _ALLOWED_TAGS:
            tag.decompose()
            continue
        for attr in list(tag.attrs):
            if attr not in _ALLOWED_ATTRS:
                del tag.attrs[attr]
        for attr in ("href", "src"):
            value = tag.get(attr)
            if value is not None:
                value = _normalize_resource_url(str(value))
                if not _is_safe_resource_url(value):
                    del tag.attrs[attr]
                else:
                    tag[attr] = value
    return str(content)


def rewrite_article_images(content, *, asset_id: int | None = None) -> list[str]:
    """Return remote image URLs and optionally rewrite them to signed local URLs.

    Browser-side ``<img>`` requests bypass the server's SSRF guard.  Rewriting
    every remote image to the API proxy is therefore the only complete boundary.
    Only paths that the browser resolves back to this origin are left alone.
    Disguised external forms such as ``/\\host`` have already been removed by
    the sanitizer and must never be treated as local images.
    """
    from app.core.security import sign_article_image

    sources: list[str] = []
    for tag in content.find_all("img"):
        raw = _normalize_resource_url(str(tag.get("src") or ""))
        if not raw or not _is_safe_resource_url(raw):
            tag.decompose()
            continue
        if raw.startswith("/") and _is_safe_resource_url(raw):
            sources.append(raw)
            continue
        index = len(sources)
        sources.append(raw)
        if asset_id is not None:
            signature = sign_article_image(asset_id, index)
            tag["src"] = f"/api/articles/{asset_id}/images/{index}?sig={signature}"
            tag["data-source-index"] = str(index)
        else:
            del tag["src"]
    return sources


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
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            return None
        # 带时区偏移的 ISO 时间先归一到 UTC，再按项目约定丢弃 tzinfo（naive-UTC）
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
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
        "html": sanitize_article_html(content),
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
    from app.core.egress import client_kwargs

    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=False, **client_kwargs("wechat_mp")
        ) as client:
            resp = await client.get(url, headers=headers)
        if 300 <= resp.status_code < 400:
            raise ArticleImportError("目标链接发生重定向，已拒绝")
        resp.raise_for_status()
    except ArticleImportError:
        # 重定向等主动拒绝：原样抛出，保持既有语义
        raise
    except httpx.HTTPError as exc:
        # 网络错误 / 超时 / 非 2xx 一律归一为业务异常，避免端点返回 500
        raise ArticleImportError(f"文章抓取失败: {exc}") from exc
    item = parse_article_html(resp.text, url)
    logger.info("解析文章成功: %s", item["title"])
    return item
