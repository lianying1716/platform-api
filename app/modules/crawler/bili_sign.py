"""B站 WBI 请求签名。

逻辑/算法来源：MediaCrawler media_platform/bilibili/help.py:BilibiliSign
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）
算法参考：https://socialsisteryi.github.io/bilibili-API-collect/docs/misc/sign/wbi.html

WBI key（img_key/sub_key）优先可由调用方提供；缺省时本模块调 /x/web-interface/nav
纯 HTTP 拉取，无需活浏览器。
"""

import time
import urllib.parse
from hashlib import md5

import httpx

from app.core import rate_limit

_MIXIN_MAP = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_NAV_API = "https://api.bilibili.com/x/web-interface/nav"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in _MIXIN_MAP)[:32]


async def fetch_wbi_keys(cookies: dict | None = None) -> tuple[str, str]:
    """纯 HTTP 拉取 wbi img_key/sub_key（无需浏览器 localStorage）。"""
    async with rate_limit.http_client(
        "bilibili",
        headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com"},
        timeout=15,
    ) as c:
        resp = await c.get(_NAV_API, cookies=cookies or {})
        data = resp.json().get("data", {})
        wbi = data.get("wbi_img", {})
        img_url = wbi.get("img_url", "")
        sub_url = wbi.get("sub_url", "")
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    return img_key, sub_key


def sign(params: dict, img_key: str, sub_key: str) -> dict:
    """给参数加 wts + w_rid 签名，返回带签名的新 dict。"""
    salt = _mixin_key(img_key, sub_key)
    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = dict(sorted(signed.items()))
    signed = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in signed.items()
    }
    query = urllib.parse.urlencode(signed)
    signed["w_rid"] = md5((query + salt).encode()).hexdigest()
    return signed
