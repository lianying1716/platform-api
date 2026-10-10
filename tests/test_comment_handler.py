"""fetch_comments worker handler：幂等写库 + state。"""
import asyncio


def _seed(db):
    from app.models import ContentItem

    c = ContentItem(account_id=1, platform="douyin", external_id="aw1", content_type="video")
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
    monkeypatch.setattr("app.workers.handlers._content_cookie", lambda db, content: "cookie")
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
    monkeypatch.setattr("app.workers.handlers._content_cookie", lambda db, content: "cookie")
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))
    assert db.query(Comment).filter_by(content_id=c.id).count() == 1  # 不重复
    db.close()


def test_handler_skips_state_when_no_comments(_engine, monkeypatch):
    """空结果不写 CommentFetchState。

    fetch_comments 在「被风控/封禁」与「确实无评论」两种情况下都返回 []，
    二者无法区分。若此时落 state，门控 D4「已有 state 即跳过」会让该作品永久不再重抓。
    因此 added == 0 时不落 state，留待下次重试。
    """
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment, CommentFetchState
    from app.workers.handlers import handle_fetch_comments

    db = sessionmaker(bind=_engine)()
    c = _seed(db)

    async def fake_fetch(**kw):
        return []

    monkeypatch.setattr("app.modules.crawler.fetch_comments", fake_fetch)
    monkeypatch.setattr("app.workers.handlers._content_cookie", lambda db, content: "cookie")
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))

    assert db.query(Comment).filter_by(content_id=c.id).count() == 0
    assert db.get(CommentFetchState, c.id) is None  # 不落 state，避免永久跳过
    db.close()


def test_handler_keeps_existing_state_when_all_duplicate(_engine, monkeypatch):
    """后续重抓若全是重复评论（added == 0），不清空已有 state 的计数。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment, CommentFetchState
    from app.workers.handlers import handle_fetch_comments

    db = sessionmaker(bind=_engine)()
    c = _seed(db)

    async def fake_fetch(**kw):
        return [{"comment_id": "c1", "parent_comment_id": "0", "user_id": "u1", "nickname": "甲",
                 "avatar": "", "content": "你好", "like_count": 5, "ip_location": "", "created_at": None}]

    monkeypatch.setattr("app.modules.crawler.fetch_comments", fake_fetch)
    monkeypatch.setattr("app.workers.handlers._content_cookie", lambda db, content: "cookie")
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))
    assert db.get(CommentFetchState, c.id).fetched_count == 1

    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))  # 全重复 -> added == 0
    assert db.query(Comment).filter_by(content_id=c.id).count() == 1
    assert db.get(CommentFetchState, c.id).fetched_count == 1  # 保留，不被清零
    db.close()


def test_handler_dedups_duplicate_cids_in_one_batch(_engine, monkeypatch):
    """同一批 fetch 内 cid 重复时只落一行，不触发唯一约束 IntegrityError。

    crawler.fetch_comments 跨页游标可能重返回同一 cid；handler 的去重集合若不在
    循环内同步刚插入的 id，两个相同 cid 都会被 db.add，commit 违反
    UniqueConstraint(content_id, comment_id) -> IntegrityError -> 任务永久失败。
    """
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment
    from app.workers.handlers import handle_fetch_comments

    db = sessionmaker(bind=_engine)()
    c = _seed(db)

    async def fake_fetch(**kw):
        return [
            {"comment_id": "dup", "parent_comment_id": "0", "user_id": "u1", "nickname": "甲",
             "avatar": "", "content": "第一次", "like_count": 5, "ip_location": "", "created_at": None},
            {"comment_id": "dup", "parent_comment_id": "0", "user_id": "u1", "nickname": "甲",
             "avatar": "", "content": "第二次(重复cid)", "like_count": 5, "ip_location": "", "created_at": None},
        ]

    monkeypatch.setattr("app.modules.crawler.fetch_comments", fake_fetch)
    monkeypatch.setattr("app.workers.handlers._content_cookie", lambda db, content: "cookie")

    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))  # must not raise
    assert db.query(Comment).filter_by(content_id=c.id).count() == 1
    db.close()


def test_handler_skips_non_comment_platform_content(_engine, monkeypatch):
    """非评论白名单平台（如小红书）直接跳过，不取 cookie 也不抓取（不抛错）。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment, ContentItem
    from app.workers.handlers import handle_fetch_comments

    db = sessionmaker(bind=_engine)()
    c = ContentItem(account_id=1, platform="xhs", external_id="note1", content_type="video")
    db.add(c)
    db.commit()

    called = {"fetch": False}

    async def fake_fetch(**kw):
        called["fetch"] = True
        return []

    monkeypatch.setattr("app.modules.crawler.fetch_comments", fake_fetch)
    asyncio.run(handle_fetch_comments(db, {"content_id": c.id}))  # must not raise
    assert called["fetch"] is False
    assert db.query(Comment).filter_by(content_id=c.id).count() == 0
    db.close()
