"""Every UI setting must resolve to an explicit backend consumer."""

from pathlib import Path

from app.core import settings_registry


ROOT = Path(__file__).resolve().parent.parent


def test_registry_specs_have_resolvable_consumers():
    for spec in settings_registry.SPECS.values():
        # Symbol names contain dots (module.function); the executable module
        # path is the longest prefix that exists on disk.
        parts = spec.consumer.split(".")
        path = ""
        symbol = ""
        for cut in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:cut])
            module_path = ROOT / (candidate.replace(".", "/") + ".py")
            if module_path.exists():
                path, symbol = candidate, ".".join(parts[cut:])
                break
        module_path = ROOT / (path.replace(".", "/") + ".py")
        assert module_path.exists(), f"{spec.key}: consumer module missing"
        source = module_path.read_text(encoding="utf-8")
        assert symbol.split(".")[0] in source, f"{spec.key}: consumer symbol missing"


def test_subscription_form_has_no_removed_boolean_fields():
    source = (ROOT / "web/src/pages/FollowedCreators.vue").read_text(encoding="utf-8")
    assert "with_comments" not in source
    assert "comment_mode" in source
    assert "interval_mode" in source


def test_crawl_form_has_no_hardcoded_platform_options():
    source = (ROOT / "web/src/pages/Crawl.vue").read_text(encoding="utf-8")
    assert "keyword_crawl" in source
    assert "value: 'xhs'" not in source


def test_settings_page_removed_dead_llm_requests():
    source = (ROOT / "web/src/pages/Settings.vue").read_text(encoding="utf-8")
    assert "saveSettings" not in source
    assert "testLlm" not in source
    assert "proxy_url" not in source
