"""平台适配器包。import 即完成注册。"""

from app.modules import bilibili, crawl_platforms, douyin, wechat_mp, xhs  # noqa: F401
from app.modules.base import REGISTRY, get_adapter  # noqa: F401
