"""百度贴吧 PC 接口签名。

逻辑来源：MediaCrawler media_platform/tieba/client.py:_sign_pc_params
（NON-COMMERCIAL LEARNING LICENSE 1.1, Copyright (c) 2024 relakkes@gmail.com，仅自用学习）

签名 = 把参数字典按键名排序后逐项拼 ``k=v``（排除 ``sign``/``sig`` 与 ``None``），
末尾追加固定密钥，取 MD5 十六进制。**纯算法，无需活浏览器。**
"""

import hashlib

PC_SIGN_SECRET = "36770b1f34c9bbf2e7d1a99d2b82fa9e"


def sign(params: dict) -> str:
    """计算 PC 接口 sign 值。"""
    sign_text = ""
    for key in sorted(params):
        if key in {"sign", "sig"} or params[key] is None:
            continue
        sign_text += f"{key}={params[key]}"
    sign_text += PC_SIGN_SECRET
    return hashlib.md5(sign_text.encode("utf-8")).hexdigest()
