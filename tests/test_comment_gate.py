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
