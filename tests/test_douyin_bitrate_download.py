"""抖音下载看齐抖小云：码率档选择 + 全档全镜像轮换 + 文件名规则。

回归背景（对照 dysync.net）：
- 抖小云 GetBestMatchedVideoUrl 会在 video.bit_rate[] 里按编码偏好 + 码率选最佳档；
- 首选失败后 SwitchOtherUrlAddressDown 会遍历**所有码率档的所有镜像**重试；
- 文件名用 SanitizeLinuxFileName(desc, awemeId)，带 aweme id 且 100 字节截断。
我们此前只取顶层 play_addr 的 url_list，其它档的镜像完全没用上。
"""

import pytest

from app.modules.douyin import _best_bitrate, _play_url_list, _pick_play_url


def _rate(bw, urls, h265=0):
    return {
        "bit_rate": bw,
        "is_h265": h265,
        "play_addr": {"url_list": urls},
    }


def test_best_bitrate_prefers_h264_then_highest():
    """默认偏好 H.264（兼容性），同编码取最高码率；只有 H.265 时才用它。"""
    aw = {"video": {"bit_rate": [
        _rate(1000, ["https://cdn/a"]),
        _rate(5000, ["https://cdn/b"]),
        _rate(9000, ["https://cdn/h265"], h265=1),
    ]}}
    assert _best_bitrate(aw)["bit_rate"] == 5000


def test_best_bitrate_falls_back_to_h265_when_no_h264():
    aw = {"video": {"bit_rate": [_rate(9000, ["https://cdn/h265"], h265=1)]}}
    assert _best_bitrate(aw)["bit_rate"] == 9000


def test_best_bitrate_skips_tiers_without_urls():
    aw = {"video": {"bit_rate": [
        _rate(99999, []),
        _rate(4000, ["https://cdn/ok"]),
    ]}}
    assert _best_bitrate(aw)["bit_rate"] == 4000


def test_play_url_list_orders_best_first_then_all_other_mirrors():
    """首选档优先，其次其它档全镜像 —— 对应抖小云 SwitchOtherUrlAddressDown。"""
    aw = {"video": {
        "play_addr": {"url_list": ["https://cdn/top"]},
        "bit_rate": [
            _rate(1000, ["https://cdn/low1", "https://cdn/low2"]),
            _rate(5000, ["https://www.douyin.com/aweme/v1/play?best", "https://cdn/best-backup"]),
        ],
    }}
    urls = _play_url_list(aw)
    assert urls[0] == "https://www.douyin.com/aweme/v1/play?best", "最佳档的官方直链应排最前"
    assert "https://cdn/best-backup" in urls
    assert "https://cdn/low1" in urls and "https://cdn/low2" in urls
    assert len(urls) == len(set(urls)), "必须去重"


def test_play_url_list_dedups_and_handles_no_bitrate():
    """没有 bit_rate[] 时回退顶层 play_addr（老响应结构）。"""
    aw = {"video": {"play_addr": {"url_list": ["https://cdn/x", "https://cdn/x"]}}}
    assert _play_url_list(aw) == ["https://cdn/x"]
    assert _pick_play_url(aw) == "https://cdn/x"


def test_play_url_list_uses_download_addr_as_last_resort():
    aw = {"video": {"play_addr": {"url_list": []},
                    "download_addr": {"url_list": ["https://cdn/dl"]}}}
    assert _play_url_list(aw) == ["https://cdn/dl"]


def test_play_url_list_empty_when_nothing_available():
    assert _play_url_list({}) == []
    assert _pick_play_url({}) == ""


# ------------------------------------------------------- 文件名（SanitizeLinuxFileName）

def test_download_output_name_appends_content_id():
    from app.workers.handlers import _download_output_name
    from app.models import ContentItem

    c = ContentItem(platform="douyin", external_id="7694592567556994555",
                    title="挑战吃遍全城辣度天花板")
    name = _download_output_name(c, "https://x")
    assert name.endswith("7694592567556994555"), "文件名必须带内容 ID，避免同名覆盖"
    assert len(name.encode("utf-8")) <= 100


def test_download_output_name_keeps_full_id_with_long_title():
    """长标题必须按字节让位，ID 不能被截掉（Linux 255 字节 + 唯一性）。"""
    from app.workers.handlers import _download_output_name
    from app.models import ContentItem

    c = ContentItem(platform="douyin", external_id="7694592567556994555", title="长标题" * 60)
    name = _download_output_name(c, "u")
    assert name.endswith("7694592567556994555"), f"ID 被截掉了: {name}"
    assert len(name.encode("utf-8")) <= 150, len(name.encode("utf-8"))


def test_download_output_name_handles_empty_title():
    from app.workers.handlers import _download_output_name
    from app.models import ContentItem

    c = ContentItem(platform="douyin", external_id="123456789012", title="")
    assert "123456789012" in _download_output_name(c, "https://x")


def test_download_output_name_sanitizes_path_separators():
    from app.workers.handlers import _download_output_name
    from app.models import ContentItem

    c = ContentItem(platform="douyin", external_id="9", title="a/b\\c:d")
    name = _download_output_name(c, "u")
    assert "/" not in name and "\\" not in name and ":" not in name
