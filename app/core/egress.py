"""Unified outbound HTTP boundary for user- or remote-controlled URLs.

Image URLs from scraped pages are untrusted.  This client validates every hop,
including DNS results and redirects, and pins the validated address for the
actual connection so a second DNS answer cannot swap in a private address.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import httpcore

from app.core import outbound


MAX_REDIRECTS = 5
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def client_kwargs(platform: str) -> dict:
    """Proxy options for every outbound HTTP client.

    Keeping the name and shape here lets download/import/LLM callers opt into
    the same deployment-level egress policy without copying proxy logic.
    """
    return outbound.httpx_proxy_kwargs(platform)


def proxy_for(platform: str) -> str | None:
    """Proxy URL for libraries such as yt-dlp that take a plain string."""
    return outbound.proxy_for(platform)


class EgressBlocked(ValueError):
    """The target is not allowed to be fetched by the server."""


class EgressResponseTooLarge(EgressBlocked):
    pass


class EgressContentTypeBlocked(EgressBlocked):
    """The remote response is not an image and must never be proxied as one."""


_RASTER_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/bmp",
}

# File-signature prefixes for the raster formats we allow.  Content-Type is
# supplied by the origin and can be wrong (or was not checked by older cache
# entries), so the byte signature is the second, independent check.
_RASTER_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _signature_image_type(content: bytes) -> str | None:
    """Return the raster type implied by a file signature, if recognizable."""
    head = (content or b"")[:32]
    for signature, content_type in _RASTER_SIGNATURES:
        if head.startswith(signature):
            return content_type
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 16 and head[4:8] == b"ftyp":
        # ISOBMFF uses major_brand followed by minor_version and compatible
        # brands.  Accept avif/avis in either position.
        brands = {head[8:12], head[16:20]}
        if brands & {b"avif", b"avis"}:
            return "image/avif"
    return None


def validate_image_bytes(
    content: bytes, declared_type: str | None, *, allow_missing_type: bool = False
) -> str:
    """Validate cached/remote image bytes against the raster allowlist.

    The server-controlled signature decides the response type.  The origin's
    declared type, when present, must still be a supported raster type; this
    rejects a masqueraded HTML/SVG response even if its first bytes happen to
    resemble an image.  ``allow_missing_type`` is reserved for local legacy
    cache entries written before MIME metadata was required.
    """
    if declared_type:
        _image_content_type(declared_type)
    elif not allow_missing_type:
        raise EgressContentTypeBlocked("远端内容缺少 Content-Type")
    actual = _signature_image_type(content)
    if actual is None:
        raise EgressContentTypeBlocked("图片内容缺少可识别的位图文件签名")
    return actual


def _image_content_type(value: str | None) -> str:
    content_type = (value or "").split(";", 1)[0].strip().lower()
    if content_type not in _RASTER_IMAGE_TYPES:
        raise EgressContentTypeBlocked(f"远端内容不是图片: {content_type or 'missing content-type'}")
    return content_type


@dataclass(frozen=True)
class ImageResponse:
    url: str
    status_code: int
    content_type: str
    content: bytes


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in ipaddress.ip_network("169.254.169.254/32")
    )


def _resolve_public(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressBlocked("封面地址只允许 http/https")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise EgressBlocked("封面地址缺少主机名")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressBlocked(f"封面主机无法解析: {host}") from exc
    addresses = {str(info[4][0]) for info in infos}
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise EgressBlocked("封面地址解析到内网、回环或元数据地址")
    return host, sorted(addresses)[0]


def is_public_image_url(url: str) -> bool:
    """Cheap preflight used by list APIs before the browser requests a cover."""
    try:
        _resolve_public(url)
        return True
    except (EgressBlocked, ValueError):
        return False


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connect only to the already validated address at connect time."""

    def __init__(self, address: str):
        self.address = address
        self.inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # httpx still sees the original host for Host/SNI; only the TCP target
        # is replaced with the address we checked immediately before this call.
        return await self.inner.connect_tcp(
            self.address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self.inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds):
        return await self.inner.sleep(seconds)


async def fetch_image(
    url: str,
    *,
    purpose: str = "cover",
    platform: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 20,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> ImageResponse:
    """Fetch one image with SSRF checks and bounded redirects."""
    del purpose  # reserved for audit/logging; callers still declare intent
    current = (url or "").strip()
    for _hop in range(MAX_REDIRECTS + 1):
        host, address = _resolve_public(current)
        request_headers = dict(headers or {})
        request = httpx.Request("GET", current, headers=request_headers)
        # A direct transport keeps the pinned IP; proxies intentionally resolve
        # remotely, so proxy mode is a separate, documented deployment boundary.
        kwargs = outbound.httpx_proxy_kwargs(platform)
        if kwargs:
            transport = httpx.AsyncHTTPTransport(**kwargs)
        else:
            transport = httpx.AsyncHTTPTransport()
            # httpcore is reached through httpx's transport; replace the DNS
            # backend so only the validated IP can be connected.
            transport._pool._network_backend = _PinnedBackend(address)
        async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False) as client:
            response = await client.send(request)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                raise EgressBlocked("封面重定向缺少 Location")
            current = str(httpx.URL(current).join(location))
            continue
        content = response.content
        if len(content) > max_bytes:
            raise EgressResponseTooLarge("封面体积超过上限")
        content_type = validate_image_bytes(content, response.headers.get("content-type"))
        return ImageResponse(
            url=current,
            status_code=response.status_code,
            content_type=content_type,
            content=content,
        )
    raise EgressBlocked("封面重定向次数过多")
