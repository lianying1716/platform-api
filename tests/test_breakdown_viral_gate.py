"""自动爆款拆解阀门（需求 2）：判定逻辑 + 配置 API 校验 + 默认只靠中位数倍数。"""

from app.core.breakdown_settings import (
    get_breakdown_settings,
    is_viral,
    resolve_breakdown_settings_from_rows,
)


def _ctx(play=0, like=0, median_play=0, median_like=0, rate=0.0):
    return {
        "play": play, "like": like,
        "median_play": median_play, "median_like": median_like,
        "engagement_rate": rate,
    }


def test_defaults_follow_median_multiple_only():
    cfg = resolve_breakdown_settings_from_rows({})
    assert cfg["viral_multiple"] == 3.0
    assert cfg["min_play"] == 0 and cfg["min_like"] == 0
    assert cfg["min_engagement_rate"] == 0.0
    assert cfg["match_rule"] == "any"
    assert cfg["auto_breakdown"] is False  # 默认不自动拆解（耗资源）
    # 只有「相对中位数」这一条参与：3 倍以上算爆款
    assert is_viral(_ctx(play=300, median_play=100), cfg) is True
    assert is_viral(_ctx(play=299, median_play=100), cfg) is False
    # 无中位数（该作者无快照）时不满足倍数阀
    assert is_viral(_ctx(play=99999, median_play=0), cfg) is False


def test_absolute_thresholds_and_any_rule():
    cfg = resolve_breakdown_settings_from_rows({})
    cfg["min_play"] = 10000
    cfg["min_like"] = 500
    cfg["match_rule"] = "any"
    # 达到播放门槛即爆款（点赞没到也行，any）
    assert is_viral(_ctx(play=10001), cfg) is True
    # 达到点赞门槛也爆款
    assert is_viral(_ctx(like=500), cfg) is True
    # 两个都没到 → 不爆款
    assert is_viral(_ctx(play=1, like=1), cfg) is False


def test_all_rule_requires_every_threshold():
    cfg = resolve_breakdown_settings_from_rows({})
    cfg["viral_multiple"] = 0  # 关掉倍数阀，只留绝对门槛
    cfg["min_play"] = 1000
    cfg["min_like"] = 100
    cfg["match_rule"] = "all"
    assert is_viral(_ctx(play=1000, like=100), cfg) is True
    assert is_viral(_ctx(play=1000, like=99), cfg) is False
    assert is_viral(_ctx(play=999, like=100), cfg) is False


def test_engagement_rate_valve():
    cfg = resolve_breakdown_settings_from_rows({})
    cfg["viral_multiple"] = 0
    cfg["min_engagement_rate"] = 0.05
    assert is_viral(_ctx(rate=0.05), cfg) is True
    assert is_viral(_ctx(rate=0.049), cfg) is False


def test_turn_off_all_valves_never_viral():
    """所有阀都关（倍数=0、绝对=0）时不判爆款，避免全量误判。"""
    cfg = resolve_breakdown_settings_from_rows({"breakdown_viral_multiple": "0"})
    # viral_multiple 非正回退默认 3.0，所以还得手动置 0 走 resolve 路径
    cfg = resolve_breakdown_settings_from_rows({})
    cfg["viral_multiple"] = 0
    assert is_viral(_ctx(play=10 ** 9), cfg) is False


def test_settings_api_roundtrip_and_validation(client):
    r = client.put("/api/system/breakdown-settings", json={
        "min_play": 10000, "min_like": 300,
        "min_engagement_rate": 0.03, "publish_within_days": 30,
        "match_rule": "all",
        "auto_transcribe": True, "auto_understand": True, "auto_breakdown": True,
    })
    assert r.status_code == 200, r.text
    data = client.get("/api/system/breakdown-settings").json()
    assert data["min_play"] == 10000 and data["min_like"] == 300
    assert data["min_engagement_rate"] == 0.03
    assert data["publish_within_days"] == 30
    assert data["match_rule"] == "all"
    assert data["auto_understand"] is True and data["auto_breakdown"] is True

    assert client.put("/api/system/breakdown-settings", json={"match_rule": "bogus"}).status_code == 422
    assert client.put("/api/system/breakdown-settings", json={"min_engagement_rate": 2}).status_code == 422
    assert client.put("/api/system/breakdown-settings", json={"min_play": -1}).status_code == 422
    assert client.put("/api/system/breakdown-settings", json={"auto_breakdown": "yes"}).status_code == 422
