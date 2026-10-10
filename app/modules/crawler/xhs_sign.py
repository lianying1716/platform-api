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
    upper = method.upper()
    if upper == "POST":
        headers = client.sign_headers_post(
            uri=uri, cookies=cookie_str, payload=data if isinstance(data, dict) else {}
        )
    elif upper == "GET":
        params = data if isinstance(data, dict) else {}
        cookie_dict = client._parse_cookies(cookie_str)
        a1 = cookie_dict.get("a1", "")
        x_s = client.sign_xs("GET", uri, a1, payload=params)
        headers = {
            "x-s": x_s,
            "x-s-common": client.sign_xsc(cookie_dict),
            "x-t": str(client.get_x_t()),
            "x-b3-traceid": client.get_b3_trace_id(),
        }
    else:
        raise ValueError(f"不支持的小红书签名方法: {method}")
    return {
        "x-s": headers.get("x-s", ""),
        "x-t": headers.get("x-t", ""),
        "x-s-common": headers.get("x-s-common", ""),
        "x-b3-traceid": headers.get("x-b3-traceid", _trace_id()),
    }
