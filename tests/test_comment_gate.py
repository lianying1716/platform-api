"""评论门控与模型测试。"""
from sqlalchemy.exc import IntegrityError


def test_models_created(_engine):
    from sqlalchemy import inspect

    names = set(inspect(_engine).get_table_names())
    assert {"comment", "comment_fetch_state"} <= names


def test_comment_unique_constraint(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment, ContentItem

    S = sessionmaker(bind=_engine)
    db = S()
    c = ContentItem(platform="douyin", external_id="aw1", content_type="video")
    db.add(c)
    db.flush()
    db.add(Comment(content_id=c.id, platform="douyin", comment_id="c1"))
    db.commit()
    db.add(Comment(content_id=c.id, platform="douyin", comment_id="c1"))
    try:
        db.commit()
        raise AssertionError("duplicate (content_id, comment_id) should fail")
    except IntegrityError:
        db.rollback()
    finally:
        db.close()


def test_comment_settings_defaults(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.core.comment_settings import get_comment_settings

    db = sessionmaker(bind=_engine)()
    s = get_comment_settings(db)
    assert s["top_pct"] == 0.2
    assert s["budget"] == 10
    assert s["min_comments"] == 20
    assert s["min_likes"] == 100
    assert s["weight"] == 20
    assert s["pages"] == 2 and s["per_page"] == 20 and s["top_n"] == 20
    assert s["interval"] == 1.0
    db.close()


def test_comment_settings_override(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.core.comment_settings import get_comment_settings
    from app.models import SystemSetting

    db = sessionmaker(bind=_engine)()
    db.add(SystemSetting(key="comment_gate_budget", value="3"))
    db.add(SystemSetting(key="comment_gate_top_pct", value="0.5"))
    db.commit()
    s = get_comment_settings(db)
    assert s["budget"] == 3 and s["top_pct"] == 0.5
    db.close()
