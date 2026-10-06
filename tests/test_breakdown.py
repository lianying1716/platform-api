"""爆款拆解（viral_breakdown）测试。

Task 1：数据模型 + 迁移 0006 —— 表存在、asset_id 唯一。
后续 Task 2/3 会在此文件追加提示词/服务相关用例。
"""
from sqlalchemy.exc import IntegrityError


def test_viral_breakdown_model_exists(session):
    from sqlalchemy import inspect

    from app.models import ViralBreakdown

    assert ViralBreakdown.__tablename__ == "viral_breakdown"
    tables = set(inspect(session.get_bind()).get_table_names())
    assert "viral_breakdown" in tables


def test_viral_breakdown_columns_shape(_engine):
    """列集合与关键约束。"""
    from sqlalchemy import inspect

    insp = inspect(_engine)
    cols = {c["name"] for c in insp.get_columns("viral_breakdown")}
    assert {
        "id", "asset_id", "score", "verdict", "sections", "comment_insights",
        "data_context", "understanding_available", "model", "prompt_version",
        "usage", "status", "error", "created_at",
    } <= cols

    # asset_id 唯一（unique=True + index=True -> 唯一索引）
    uidx = [i for i in insp.get_indexes("viral_breakdown")
            if i["unique"] and i["column_names"] == ["asset_id"]]
    assert uidx, "viral_breakdown.asset_id 应有唯一索引"


def test_viral_breakdown_unique_asset(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import ContentItem, MediaAsset, ViralBreakdown

    S = sessionmaker(bind=_engine)
    db = S()
    c = ContentItem(platform="douyin", external_id="t1vb", content_type="video")
    db.add(c)
    db.flush()
    asset = MediaAsset(content_id=c.id, video_file="v.mp4")
    db.add(asset)
    db.flush()
    db.add(ViralBreakdown(asset_id=asset.id, verdict="第一条"))
    db.commit()

    # 同一 asset_id 再插一行必须触发唯一约束。
    db.add(ViralBreakdown(asset_id=asset.id, verdict="第二条"))
    try:
        db.commit()
        raise AssertionError("duplicate viral_breakdown.asset_id should fail")
    except IntegrityError:
        db.rollback()
    finally:
        db.close()
