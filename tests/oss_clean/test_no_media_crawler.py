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
