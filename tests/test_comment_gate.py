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


def _mk(db, ext, like, comment, platform="douyin"):
    from app.models import ContentItem, ContentMetricSnapshot

    c = ContentItem(platform=platform, external_id=ext, content_type="video", source_kind="crawl")
    db.add(c)
    db.flush()
    db.add(ContentMetricSnapshot(content_id=c.id, like=like, comment=comment))
    db.commit()
    return c


def test_gate_percentile_min_and_budget(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.services.comment_gate import select_targets

    db = sessionmaker(bind=_engine)()
    # 10 条，全部过下限(评论>=20,赞>=100)，分位0.2=2条，预算10不设限
    cs = [_mk(db, f"a{i}", like=1000 - i * 10, comment=100 - i) for i in range(10)]
    picks = select_targets(db, cs, platform="douyin",
                           settings={"top_pct": 0.2, "budget": 10, "min_comments": 20,
                                     "min_likes": 100, "weight": 20})
    assert len(picks) == 2
    assert [p.external_id for p in picks] == ["a0", "a1"]  # 分最高两条
    db.close()


def test_gate_min_filters_out_cold(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.services.comment_gate import select_targets

    db = sessionmaker(bind=_engine)()
    hot = _mk(db, "hot", like=5000, comment=300)
    cold = _mk(db, "cold", like=10, comment=1)  # 不过下限
    picks = select_targets(db, [hot, cold], platform="douyin",
                           settings={"top_pct": 1.0, "budget": 10, "min_comments": 20,
                                     "min_likes": 100, "weight": 20})
    assert [p.external_id for p in picks] == ["hot"]
    db.close()


def test_gate_skips_already_fetched_and_other_platform(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import CommentFetchState
    from app.services.comment_gate import select_targets

    db = sessionmaker(bind=_engine)()
    a = _mk(db, "done", like=900, comment=200)
    b = _mk(db, "fresh", like=800, comment=150)
    _mk(db, "bili", like=9000, comment=900, platform="bilibili")
    db.add(CommentFetchState(content_id=a.id, fetched_count=5))
    db.commit()
    from app.models import ContentItem

    picks = select_targets(db, db.query(ContentItem).all(), platform="douyin",
                           settings={"top_pct": 1.0, "budget": 10, "min_comments": 20,
                                     "min_likes": 100, "weight": 20})
    assert [p.external_id for p in picks] == ["fresh"]
    db.close()


def test_enqueue_comment_jobs(_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.comment_gate import enqueue_comment_jobs

    db = sessionmaker(bind=_engine)()
    cs = [_mk(db, f"b{i}", like=2000, comment=200) for i in range(3)]
    settings = {"top_pct": 1.0, "budget": 10, "min_comments": 20, "min_likes": 100, "weight": 20}
    n = enqueue_comment_jobs(db, cs, platform="douyin", settings=settings)
    assert n == 3
    jobs = db.query(JobQueue).filter_by(job_type="fetch_comments").all()
    assert len(jobs) == 3 and all(j.priority == 150 for j in jobs)
    db.close()
