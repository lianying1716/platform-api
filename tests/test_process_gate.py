"""素材分档加工：模型结构与门控纯函数测试。"""
from sqlalchemy.exc import IntegrityError


def test_tiered_models_exist(session):
    from app.models import AssetFrame, VideoUnderstanding, ContentItem, MediaAsset
    from sqlalchemy import inspect

    assert "asset_frame" in inspect(session.get_bind()).get_table_names()
    assert "video_understanding" in inspect(session.get_bind()).get_table_names()
    assert hasattr(ContentItem, "duration_sec") and hasattr(ContentItem, "auto_category")
    assert hasattr(MediaAsset, "process_tier")


def test_tiered_columns_shape(_engine):
    """新增列/表的关键字段与约束（唯一约束、可空性）。"""
    from sqlalchemy import inspect

    insp = inspect(_engine)
    content_cols = {c["name"] for c in insp.get_columns("content_item")}
    assert {"duration_sec", "auto_category", "category_confidence"} <= content_cols

    asset_cols = {c["name"] for c in insp.get_columns("media_asset")}
    assert {"process_tier", "frames_status", "understand_status"} <= asset_cols

    frame_cols = {c["name"] for c in insp.get_columns("asset_frame")}
    assert {"id", "asset_id", "scene_index", "timestamp_sec", "path", "is_keyframe", "created_at"} <= frame_cols

    vu_cols = {c["name"] for c in insp.get_columns("video_understanding")}
    assert {
        "id", "asset_id", "category", "category_confidence", "category_reason",
        "dimensions", "summary", "timeline", "model", "prompt_version",
        "usage", "status", "error", "created_at",
    } <= vu_cols

    # asset_frame(asset_id, scene_index) 联合唯一
    frame_uqs = [tuple(u["column_names"]) for u in insp.get_unique_constraints("asset_frame")]
    assert ("asset_id", "scene_index") in frame_uqs
    # video_understanding.asset_id 唯一（unique=True + index=True → 唯一索引）
    vu_uidx = [i for i in insp.get_indexes("video_understanding")
               if i["unique"] and i["column_names"] == ["asset_id"]]
    assert vu_uidx, "video_understanding.asset_id 应有唯一索引"


def test_asset_frame_unique_constraint(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import AssetFrame, ContentItem, MediaAsset

    S = sessionmaker(bind=_engine)
    db = S()
    c = ContentItem(platform="douyin", external_id="t1af", content_type="video")
    db.add(c)
    db.flush()
    asset = MediaAsset(content_id=c.id, video_file="v.mp4")
    db.add(asset)
    db.flush()
    db.add(AssetFrame(asset_id=asset.id, scene_index=0, timestamp_sec=0.0, path="f0.jpg"))
    db.commit()
    db.add(AssetFrame(asset_id=asset.id, scene_index=0, timestamp_sec=1.0, path="dup.jpg"))
    try:
        db.commit()
        raise AssertionError("duplicate (asset_id, scene_index) should fail")
    except IntegrityError:
        db.rollback()
    finally:
        db.close()


def test_video_understanding_unique_asset(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import ContentItem, MediaAsset, VideoUnderstanding

    S = sessionmaker(bind=_engine)
    db = S()
    c = ContentItem(platform="douyin", external_id="t1vu", content_type="video")
    db.add(c)
    db.flush()
    asset = MediaAsset(content_id=c.id, video_file="v.mp4")
    db.add(asset)
    db.flush()
    db.add(VideoUnderstanding(asset_id=asset.id, category="other"))
    db.commit()
    db.add(VideoUnderstanding(asset_id=asset.id, category="commerce"))
    try:
        db.commit()
        raise AssertionError("duplicate video_understanding.asset_id should fail")
    except IntegrityError:
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Task 3: 动态门控 select_tier（纯函数，不碰 DB）
# ---------------------------------------------------------------------------

_GATE = {
    "weight_like": 1,
    "weight_comment": 2,
    "weight_play": 0,
    "min_likes_floor": 100,
    "l2_min_heat": 200,
    "l3_min_heat": 1000,
    "top_pct": 0.5,
    "budget": 2,
}


def _cand(cid, like=0, comment=0, play=0, existing_tier=""):
    return {
        "content_id": cid,
        "like": like,
        "comment": comment,
        "play": play,
        "existing_tier": existing_tier,
    }


def test_gate_tiers_and_budget():
    from app.services.process_gate import select_tier
    s = {"weight_like":1,"weight_comment":2,"weight_play":0,"min_likes_floor":100,
         "l2_min_heat":200,"l3_min_heat":1000,"top_pct":0.5,"budget":2}
    cands=[{"content_id":i,"like":l,"comment":0,"play":0,"existing_tier":""}
           for i,l in enumerate([5000,800,50,10])]
    out={r["content_id"]:r["tier"] for r in select_tier(cands,s)}
    assert out[0]=="l3" and out[1]=="l2" and out[2]=="l1" and out[3]=="l1"
    assert sum(1 for r in select_tier(cands,s) if r["tier"] in ("l2","l3")) <= s["budget"]


def test_gate_empty_input():
    from app.services.process_gate import select_tier
    assert select_tier([], _GATE) == []


def test_gate_floor_caps_to_l1():
    """like < min_likes_floor → 不高于 l1，即便 score 达到 l3 门槛。"""
    from app.services.process_gate import select_tier
    # play 权重为 0，用极大 comment 把分数顶到 l3 门槛，但 like 低于下限。
    s = dict(_GATE, weight_comment=10.0, top_pct=1.0)
    out = {r["content_id"]: r["tier"] for r in select_tier([_cand(1, like=10, comment=500)], s)}
    assert out[1] == "l1"


def test_gate_score_formula():
    """score = w_like*like + w_comment*comment + w_play*play。"""
    from app.services.process_gate import select_tier
    s = dict(_GATE, weight_like=1.0, weight_comment=2.0, weight_play=0.5, top_pct=1.0)
    # 100*1 + 50*2 + 400*0.5 = 100 + 100 + 200 = 400 → l2（>=200 且 <1000）
    rows = select_tier([_cand(1, like=100, comment=50, play=400)], s)
    assert rows[0]["tier"] == "l2"
    assert rows[0]["score"] == 400


def test_gate_budget_demotes_lowest_scores():
    """超预算时按分数从低到高降级，保留分数最高者。"""
    from app.services.process_gate import select_tier
    s = dict(_GATE, top_pct=1.0, budget=1)
    cands = [_cand(1, like=5000), _cand(2, like=3000), _cand(3, like=800)]
    out = {r["content_id"]: r["tier"] for r in select_tier(cands, s)}
    assert out[1] == "l3"
    assert out[2] == "l1"  # 次高分也超出 budget，被降级
    assert out[3] == "l1"  # 唯一预算名额之外的最低分被降级
    assert sum(1 for t in out.values() if t in ("l2","l3")) <= s["budget"]


def test_gate_no_downgrade_existing_l3():
    """已 l3 的不降级：分数再低也保持 l3。"""
    from app.services.process_gate import select_tier
    s = dict(_GATE, top_pct=0.1, budget=0)
    cands = [_cand(1, like=5000), _cand(2, like=1, existing_tier="l3")]
    out = {r["content_id"]: r["tier"] for r in select_tier(cands, s)}
    assert out[2] == "l3"


def test_gate_percentile_limits_upgrades():
    """分位：只有分数最高的 top_pct 份额可能高于 l1。"""
    from app.services.process_gate import select_tier
    s = dict(_GATE, top_pct=0.5, budget=10)
    # 4 条仅取前 2；第 3、4 名即便过下限也压回 l1。
    cands = [_cand(i, like=l) for i, l in enumerate([5000, 3000, 800, 500])]
    out = {r["content_id"]: r["tier"] for r in select_tier(cands, s)}
    assert out[0] == "l3" and out[1] == "l3"
    assert out[2] == "l1" and out[3] == "l1"
