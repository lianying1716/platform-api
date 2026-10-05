"""fetch_comments worker handler：幂等写库 + state。"""
import asyncio


def _seed(db):
    from app.models import ContentItem

    c = ContentItem(platform="douyin", external_id="aw1", content_type="video")
    db.add(c)
    db.flush()
    return c


def test_handler_writes_comments_and_state(_engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment, CommentFetchState
    from app.workers.handlers import handle_fetch_comments

    db = sessionmaker(bind=_engine)()
    c = _seed(db)

    async def fake_fetch(**kw):
        return [
            {"comment_id": "c1", "parent_comment_id": "0", "user_id": "u1", "nickname": "甲",
             "avatar": "", "content": "你好", "like_count": 5, "ip_location": "北京", "created_at": 100},
        ]

    monkeypatch.setattr("app.modules.crawler.fetch_comments", fake_fetch)
    monkeypatch.setattr("app.workers.handlers._comment_cookie", lambda db, platform: "cookie")
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))

    assert db.query(Comment).filter_by(content_id=c.id).count() == 1
    st = db.get(CommentFetchState, c.id)
    assert st is not None and st.fetched_count == 1
    db.close()


def test_handler_is_idempotent(_engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment
    from app.workers.handlers import handle_fetch_comments

    db = sessionmaker(bind=_engine)()
    c = _seed(db)

    async def fake_fetch(**kw):
        return [{"comment_id": "c1", "parent_comment_id": "0", "user_id": "u1", "nickname": "甲",
                 "avatar": "", "content": "你好", "like_count": 5, "ip_location": "", "created_at": None}]

    monkeypatch.setattr("app.modules.crawler.fetch_comments", fake_fetch)
    monkeypatch.setattr("app.workers.handlers._comment_cookie", lambda db, platform: "cookie")
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))
    assert db.query(Comment).filter_by(content_id=c.id).count() == 1  # 不重复
    db.close()
