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
    c = ContentItem(account_id=1, platform="douyin", external_id="aw1", content_type="video")
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

    c = ContentItem(account_id=1, platform=platform, external_id=ext, content_type="video", source_kind="crawl")
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


def test_enqueue_skips_unsupported_platform(_engine):
    """不在采集平台集合内的类型不得入队（否则 worker 反复失败重试）。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.comment_gate import enqueue_comment_jobs

    db = sessionmaker(bind=_engine)()
    cs = [_mk(db, "w1", like=5000, comment=300, platform="wechat_mp")]
    settings = {"top_pct": 1.0, "budget": 10, "min_comments": 20, "min_likes": 100, "weight": 20}
    n = enqueue_comment_jobs(db, cs, platform="wechat_mp", settings=settings)
    assert n == 0
    assert db.query(JobQueue).filter_by(job_type="fetch_comments").count() == 0
    db.close()


def test_enqueue_reports_only_new_jobs_on_dedup(_engine):
    """M1：重复目标被 queue 去重吸收时，返回值只计本次新建的 job。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.comment_gate import enqueue_comment_jobs

    db = sessionmaker(bind=_engine)()
    cs = [_mk(db, f"d{i}", like=2000, comment=200) for i in range(3)]
    settings = {"top_pct": 1.0, "budget": 10, "min_comments": 20, "min_likes": 100, "weight": 20}
    assert enqueue_comment_jobs(db, cs, platform="douyin", settings=settings) == 3
    assert enqueue_comment_jobs(db, cs, platform="douyin", settings=settings) == 0
    assert db.query(JobQueue).filter_by(job_type="fetch_comments").count() == 3
    db.close()


def test_comment_settings_api(client):
    r = client.get("/api/system/comment-settings")
    assert r.status_code == 200
    assert r.json()["budget"] == 10
    r2 = client.put("/api/system/comment-settings", json={"budget": 4, "top_pct": 0.3})
    assert r2.status_code == 200
    assert client.get("/api/system/comment-settings").json()["budget"] == 4


def test_comment_settings_api_rejects_invalid_values(client):
    """Task 8 carry-forward（C-Task 3/4 review MUST）：越界值必须 422，且不得落库。

    budget=-1 会让门控 take=min(n-1, -1) 变成 n-1（off-by-one）；top_pct>1 / pages<1 等
    同样会破坏门控或抓取逻辑，故写入端必须校验范围。
    """
    baseline = client.get("/api/system/comment-settings").json()
    assert baseline["budget"] == 10  # 默认未被污染

    invalid = [
        {"budget": -1},
        {"top_pct": 1.5},
        {"top_pct": -0.1},
        {"min_comments": -1},
        {"min_likes": -1},
        {"weight": -1},
        {"pages": 0},
        {"per_page": 0},
        {"top_n": 0},
        {"interval": -0.1},
    ]
    for body in invalid:
        r = client.put("/api/system/comment-settings", json=body)
        assert r.status_code == 422, (body, r.status_code, r.text)

    # 越界写入被拒后，配置应保持原样（未被局部污染）。
    assert client.get("/api/system/comment-settings").json() == baseline


def test_comment_settings_api_rejects_non_numeric(client):
    for body in ({"budget": "abc"}, {"top_pct": "NaN"}, {"weight": None}):
        r = client.put("/api/system/comment-settings", json=body)
        assert r.status_code == 422, (body, r.status_code, r.text)


def test_comment_settings_api_valid_write_readback(client):
    """合法值写入后读回，且类型归一（interval 整型 2 -> 2.0）。"""
    r = client.put("/api/system/comment-settings", json={"budget": 4, "top_pct": 0.3, "interval": 2})
    assert r.status_code == 200
    got = client.get("/api/system/comment-settings").json()
    assert got["budget"] == 4
    assert got["top_pct"] == 0.3
    assert got["interval"] == 2.0
