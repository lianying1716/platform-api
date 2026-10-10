"""小红书请求签名。

签名由 MIT 许可证的 ``xhshow`` 依赖完成（https://github.com/Cloxl/xhshow）。
本模块只负责把平台请求参数整理为 xhshow 的调用形状，不携带第三方签名实现。
"""

import json
import uuid
from typing import Any

def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def sign(uri: str, data: dict | str | None = None, cookie_str: str = "", method: str = "POST") -> dict[str, Any]:
    """返回 {x-s, x-t, x-s-common, x-b3-traceid}。"""
    from xhshow import Xhshow

    client = Xhshow()
    if method.upper() != "POST":
        raise ValueError("小红书采集当前仅支持 POST 签名")
    headers = client.sign_headers_post(
        uri=uri, cookies=cookie_str, payload=data if isinstance(data, dict) else {}
    )
    return {
        "x-s": headers.get("x-s", ""),
        "x-t": headers.get("x-t", ""),
        "x-s-common": headers.get("x-s-common", ""),
        "x-b3-traceid": headers.get("x-b3-traceid", _trace_id()),
    }
