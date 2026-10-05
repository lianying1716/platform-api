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
