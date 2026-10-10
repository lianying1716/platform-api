"""开源清理的净室断言：不打包 MediaCrawler 复制的资源，签名不依赖第三方 JS。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRAWLER = ROOT / "app" / "modules" / "crawler"


def test_no_vendored_third_party_crawler_assets():
    for rel in (
        "libs/douyin.js",
        "libs/zhihu.js",
        "libs/stealth.min.js",
        "graphql/kuaishou/search_query.graphql",
        "graphql/kuaishou/vision_profile_user_list.graphql",
    ):
        assert not (CRAWLER / rel).exists(), f"第三方复制文件仍在: {rel}"


def test_media_crawler_strings_are_gone_from_runtime_modules():
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "MediaCrawler" in text or "relakkes" in text or "NON-COMMERCIAL" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_old_signature_modules_are_removed():
    assert not (CRAWLER / "douyin_sign.py").exists()
    assert not (CRAWLER / "zhihu_sign.py").exists()
    for d in (CRAWLER / "libs", CRAWLER / "graphql"):
        if d.exists():
            files = [p for p in d.rglob("*") if p.is_file()]
            assert files == [], f"残留第三方资源: {files}"


def test_browser_fetch_paths_are_rate_limited():
    """浏览器同源抓取也属于平台出站流量，必须过统一限速桶。"""
    root = Path(__file__).resolve().parents[2]
    douyin = (root / "app" / "modules" / "douyin_browser_fetch.py").read_text(encoding="utf-8")
    generic = (root / "app" / "modules" / "browser_fetch.py").read_text(encoding="utf-8")
    assert 'rate_limit.acquire("douyin")' in douyin
    assert "rate_limit.acquire(platform)" in generic


def test_browser_paths_reuse_a_pool_instead_of_cold_starting():
    """抖音/知乎都要靠页面签名，必须复用浏览器上下文，不能每次冷启动 Chromium。"""
    root = Path(__file__).resolve().parents[2]
    for rel in ("app/modules/douyin_browser_fetch.py", "app/modules/browser_fetch.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "browser_pool.acquire" in text, f"{rel} 未复用浏览器池"
        assert "chromium.launch" not in text, f"{rel} 仍有冷启动"
