"""小红书 x-s/x-t 请求签名。

逻辑来源：MediaCrawler media_platform/xhs/playwright_sign.py:sign_with_xhshow
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）
底层算法：xhshow 开源库（https://github.com/Cloxl/xhshow），作为 PyPI 依赖。

纯算法签名，无需活浏览器。含原项目对 xhshow GET 请求 a3_hash 的 bug 修复
（xhshow issue #104），照搬以保证签名正确。
"""

import hashlib
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

_patched = False


def _patch_xhshow_a3_hash() -> None:
    """修复 xhshow GET 请求 a3_hash 计算（原项目 playwright_sign.py 同款补丁）。"""
    global _patched
    if _patched:
        return
    from xhshow.core.crypto import CryptoProcessor

    _original_build = CryptoProcessor.build_payload_array

    def _patched_build(self, hex_parameter, a1_value, app_identifier="xhs-pc-web",
                       string_param="", timestamp=None, sign_state=None):
        payload = _original_build(self, hex_parameter, a1_value, app_identifier,
                                  string_param, timestamp, sign_state)
        if "{" not in string_param:  # GET 请求
            correct_md5_hex = hashlib.md5(string_param.encode("utf-8")).hexdigest()
            correct_md5_bytes = [int(correct_md5_hex[i:i + 2], 16) for i in range(0, 32, 2)]
            seed_byte = payload[4]
            ts_bytes = payload[8:16]
            correct_a3_hash = self._custom_hash_v2(list(ts_bytes) + correct_md5_bytes)
            for i in range(16):
                payload[128 + i] = correct_a3_hash[i] ^ seed_byte
        return payload

    CryptoProcessor.build_payload_array = _patched_build
    _patched = True


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _build_sign_string(uri: str, data: dict | str | None, method: str) -> str:
    if method.upper() == "POST":
        c = uri
        if isinstance(data, dict):
            c += json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(data, str):
            c += data
        return c
    if not data or (isinstance(data, dict) and not data):
        return uri
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            if isinstance(v, list):
                vs = ",".join(str(x) for x in v)
            elif v is not None:
                vs = str(v)
            else:
                vs = ""
            parts.append(f"{k}={quote(vs, safe=',')}")
        return f"{uri}?{'&'.join(parts)}"
    return f"{uri}?{data}"


def sign(uri: str, data: dict | str | None = None, cookie_str: str = "", method: str = "POST") -> dict[str, Any]:
    """返回 {x-s, x-t, x-s-common, x-b3-traceid}。"""
    _patch_xhshow_a3_hash()
    from xhshow import Xhshow

    client = Xhshow()
    if method.upper() == "POST":
        headers = client.sign_headers_post(
            uri=uri, cookies=cookie_str, payload=data if isinstance(data, dict) else {}
        )
    else:
        content = _build_sign_string(uri, data, method)
        cookie_dict = client._parse_cookies(cookie_str)
        a1 = cookie_dict.get("a1", "")
        ts = time.time()
        d_value = hashlib.md5(content.encode("utf-8")).hexdigest()
        payload_array = client.crypto_processor.build_payload_array(d_value, a1, "xhs-pc-web", content, ts)
        xor_result = client.crypto_processor.bit_ops.xor_transform_array(payload_array)
        config = client.config
        x3_b64 = client.crypto_processor.b64encoder.encode_x3(xor_result[: config.PAYLOAD_LENGTH])
        sig_data = config.SIGNATURE_DATA_TEMPLATE.copy()
        sig_data["x3"] = config.X3_PREFIX + x3_b64
        x_s = config.XYS_PREFIX + client.crypto_processor.b64encoder.encode(
            json.dumps(sig_data, separators=(",", ":"), ensure_ascii=False)
        )
        headers = {
            "x-s": x_s,
            "x-s-common": client.sign_xs_common(cookie_dict),
            "x-t": str(client.get_x_t(ts)),
            "x-b3-traceid": client.get_b3_trace_id(),
        }
    return {
        "x-s": headers.get("x-s", ""),
        "x-t": headers.get("x-t", ""),
        "x-s-common": headers.get("x-s-common", ""),
        "x-b3-traceid": headers.get("x-b3-traceid", _trace_id()),
    }
