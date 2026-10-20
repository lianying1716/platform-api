"""加工配置读取（门控参数 / 类型目录 / 类型提示词）：默认值与覆盖。"""


def test_process_defaults(session):
    from app.core.process_settings import PROCESS_KEYS, get_process_settings

    s = get_process_settings(session)
    # 两档简化：返回的键集合与 PROCESS_KEYS 完全一致（抽帧相关键已彻底移除）。
    assert set(s) == set(PROCESS_KEYS)
    assert s["min_heat"] >= 0
    assert s["video_max_mb"] > 0
    assert "provider" not in s and "vision_model" not in s and "classify_model" not in s


def test_process_override(session):
    from app.core.process_settings import get_process_settings
    from app.models import SystemSetting

    session.add(SystemSetting(key="process_min_heat", value="999"))
    session.commit()
    assert get_process_settings(session)["min_heat"] == 999


def test_process_defaults_types(session):
    """类型归一：数值/布尔/字符串各归其位（权重 float、开关 bool、provider str）。"""
    from app.core.process_settings import get_process_settings

    s = get_process_settings(session)
    assert isinstance(s["min_heat"], int) and isinstance(s["budget"], int)
    assert isinstance(s["top_pct"], float) and 0.0 <= s["top_pct"] <= 1.0
    assert isinstance(s["weight_comment"], float)
    assert isinstance(s["classify_enabled"], bool)
    assert isinstance(s["use_cover"], bool) and s["use_cover"] is False
    assert "vision_model" not in s


def test_process_bool_and_string_override(session):
    from app.core.process_settings import get_process_settings
    from app.models import SystemSetting

    session.add(SystemSetting(key="process_classify_enabled", value="0"))
    session.add(SystemSetting(key="process_use_cover", value="true"))
    session.commit()
    s = get_process_settings(session)
    assert s["classify_enabled"] is False
    assert s["use_cover"] is True


def test_process_bad_value_falls_back_to_default(session):
    """非法/空值不抛异常，回退内置默认。"""
    from app.core.process_settings import get_process_settings
    from app.models import SystemSetting

    session.add(SystemSetting(key="process_budget", value="not-a-number"))
    session.add(SystemSetting(key="process_top_pct", value=""))
    session.commit()
    s = get_process_settings(session)
    assert s["budget"] == 20
    assert s["top_pct"] == 0.2


def test_taxonomy_defaults_and_override(session):
    from app.core.process_settings import DEFAULT_TAXONOMY, get_taxonomy
    from app.models import SystemSetting

    tax = get_taxonomy(session)
    assert tax == DEFAULT_TAXONOMY
    assert tax["commerce"] == "带货/种草/开箱"
    assert tax["other"] == "其他/无法归类"

    session.add(SystemSetting(key="process_type_taxonomy", value='{"commerce": "带货v2"}'))
    session.commit()
    assert get_taxonomy(session) == {"commerce": "带货v2"}


def test_taxonomy_corrupt_falls_back(session):
    from app.core.process_settings import DEFAULT_TAXONOMY, get_taxonomy
    from app.models import SystemSetting

    session.add(SystemSetting(key="process_type_taxonomy", value="{not-json"))
    session.commit()
    assert get_taxonomy(session) == DEFAULT_TAXONOMY


def test_type_prompts_defaults_structure(session):
    from app.core.process_settings import get_type_prompts

    prompts = get_type_prompts(session)
    assert "commerce" in prompts
    spec = prompts["commerce"]
    assert isinstance(spec["focus"], str) and spec["focus"]
    dims = spec["dimensions"]
    assert isinstance(dims, list) and dims
    assert {"key", "name", "ask"} <= set(dims[0])


def test_type_prompts_override(session):
    from app.core.process_settings import get_type_prompts
    from app.models import SystemSetting

    payload = '{"commerce": {"focus": "只看价格", "dimensions": [{"key": "price", "name": "价格", "ask": "多少钱"}]}}'
    session.add(SystemSetting(key="process_type_prompts", value=payload))
    session.commit()
    prompts = get_type_prompts(session)
    assert prompts == {
        "commerce": {
            "focus": "只看价格",
            "dimensions": [{"key": "price", "name": "价格", "ask": "多少钱"}],
        }
    }


def test_type_prompts_corrupt_falls_back(session):
    from app.core.process_settings import DEFAULT_TYPE_PROMPTS, get_type_prompts
    from app.models import SystemSetting

    session.add(SystemSetting(key="process_type_prompts", value="[]"))
    session.commit()
    # 结构非法（顶层不是对象）→ 回退内置默认
    assert get_type_prompts(session) == DEFAULT_TYPE_PROMPTS


def test_common_dimensions_defaults(session):
    from app.core.process_settings import get_common_dimensions

    dims = get_common_dimensions(session)
    keys = {d["key"] for d in dims}
    assert {"visual", "speech", "structure", "emotion", "highlights", "scene_count"} <= keys
