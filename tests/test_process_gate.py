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


# ---------------------------------------------------------------------------
# Task 3 接线（B1）：select_targets / enqueue_process_jobs 把门控接到采集链路
# ---------------------------------------------------------------------------


def _mk_ready(db, ext, like, comment, *, platform="douyin", content_type="video",
              category="", tier="", download_status="done"):
    """造「内容 + 指标快照 + 已下载素材」，作为自动分档的候选。"""
    from app.models import ContentItem, ContentMetricSnapshot, MediaAsset

    c = ContentItem(
        platform=platform, external_id=ext, content_type=content_type, source_kind="crawl"
    )
    db.add(c)
    db.flush()
    db.add(ContentMetricSnapshot(content_id=c.id, like=like, comment=comment))
    asset = MediaAsset(
        content_id=c.id, video_file="v.mp4", download_status=download_status, process_tier=tier
    )
    db.add(asset)
    db.commit()
    return c, asset


def test_select_targets_picks_l2_l3_only_for_ready_video(_engine):
    """门控候选：只收已下载完成的视频，按档位给出 (asset, tier, reason)。"""
    from sqlalchemy.orm import sessionmaker

    from app.services.process_gate import select_targets

    db = sessionmaker(bind=_engine)()
    hot, hot_asset = _mk_ready(db, "hot", like=5000, comment=0)
    warm, warm_asset = _mk_ready(db, "warm", like=800, comment=0)
    cold, _ = _mk_ready(db, "cold", like=10, comment=0)          # 不过下限
    pending, _ = _mk_ready(db, "pending", like=9000, comment=0,
                           download_status="downloading")         # 未下载完
    article, _ = _mk_ready(db, "art", like=9000, comment=0, content_type="article")

    picks = select_targets(
        db, [hot, warm, cold, pending, article], settings=dict(_GATE, top_pct=1.0)
    )
    got = {a.id: t for a, t, _ in picks}
    assert got == {hot_asset.id: "l3", warm_asset.id: "l2"}
    db.close()


def test_enqueue_process_jobs_creates_jobs_and_sets_status(_engine):
    """自动门控入队：l2→extract_frames、l3→understand_video，并即时置 pending 状态。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    hot, hot_asset = _mk_ready(db, "hot", like=5000, comment=0)
    warm, warm_asset = _mk_ready(db, "warm", like=800, comment=0)
    _mk_ready(db, "cold", like=5, comment=0)

    n = enqueue_process_jobs(db, [hot, warm], settings=dict(_GATE, top_pct=1.0))
    assert n == 2

    jobs = {j.job_type: j for j in db.query(JobQueue).all()}
    assert set(jobs) == {"extract_frames", "understand_video"}
    assert jobs["extract_frames"].payload["asset_id"] == warm_asset.id
    assert jobs["understand_video"].payload["asset_id"] == hot_asset.id
    assert all(j.priority == 120 for j in jobs.values())

    db.refresh(warm_asset)
    db.refresh(hot_asset)
    assert warm_asset.frames_status == "pending"
    assert hot_asset.understand_status == "pending"
    db.close()


def test_enqueue_process_jobs_dedup_and_already_processed(_engine):
    """重复调用只新建一次；已达目标档位的素材不再入队。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    hot, hot_asset = _mk_ready(db, "hot", like=5000, comment=0)
    done, done_asset = _mk_ready(db, "done", like=6000, comment=0, tier="l3")
    settings = dict(_GATE, top_pct=1.0)

    assert enqueue_process_jobs(db, [hot, done], settings=settings) == 1
    # 同一批再来一次：hot 的 job 仍在队列（dedup 吸收），done 已 l3 → 返回值 0。
    assert enqueue_process_jobs(db, [hot, done], settings=settings) == 0
    assert db.query(JobQueue).filter_by(job_type="understand_video").count() == 1
    assert db.query(JobQueue).filter_by(job_type="understand_video").one().payload[
        "asset_id"
    ] == hot_asset.id
    db.close()


def test_enqueue_process_jobs_empty_or_cold_returns_zero(_engine):
    """没有够格候选（全冷/无素材/无指标）时不建 job。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    cold, _ = _mk_ready(db, "cold", like=1, comment=0)
    assert enqueue_process_jobs(db, [], settings=dict(_GATE, top_pct=1.0)) == 0
    assert enqueue_process_jobs(db, [cold], settings=dict(_GATE, top_pct=1.0)) == 0
    assert db.query(JobQueue).count() == 0
    db.close()


def test_select_targets_uses_latest_metric_snapshot(_engine):
    """候选指标取最新快照：旧快照高、新快照低时按新值判（不误升级）。"""
    from datetime import datetime, timedelta

    from sqlalchemy.orm import sessionmaker

    from app.models import ContentMetricSnapshot
    from app.services.process_gate import select_targets

    db = sessionmaker(bind=_engine)()
    c, asset = _mk_ready(db, "trend", like=5000, comment=0)
    # 再落一条更晚、更冷的快照，应按它判为 l1。
    db.add(
        ContentMetricSnapshot(
            content_id=c.id, like=5, comment=0,
            captured_at=datetime.utcnow() + timedelta(seconds=5),
        )
    )
    db.commit()

    picks = select_targets(db, [c], settings=dict(_GATE, top_pct=1.0))
    assert picks == []
    db.close()


# ---------------------------------------------------------------------------
# I1：在途预算（全局）——单条/下载完成路径也不能突破 budget
# ---------------------------------------------------------------------------


def test_enqueue_inflight_budget_caps_single_item_calls(_engine):
    """I1：逐条调用（下载完成路径 batch=1）时，累计在途 l2/l3 job 不得超过 budget。

    回归：修复前 50 条爆款逐一走门控 → 建 50 个 L3 job（budget=20 被突破）。
    """
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    settings = dict(_GATE, top_pct=1.0, budget=3)  # top_pct=1 排除分位干扰，只看预算
    created = 0
    for i in range(10):
        c, _ = _mk_ready(db, f"hot{i}", like=5000, comment=0)
        created += enqueue_process_jobs(db, [c], settings=settings)

    assert created == 3
    assert db.query(JobQueue).filter_by(job_type="understand_video").count() == 3
    db.close()


def test_enqueue_inflight_budget_counts_running_and_frees_on_done(_engine):
    """在途预算把 running job 也计入；job 完成后额度释放，后续可再入队。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    settings = dict(_GATE, top_pct=1.0, budget=1)

    c1, a1 = _mk_ready(db, "hot1", like=5000, comment=0)
    assert enqueue_process_jobs(db, [c1], settings=settings) == 1
    # 该 job 被 worker 领走（running）仍占额度：第 2 条够热也不入队。
    job = db.query(JobQueue).filter_by(job_type="understand_video").one()
    job.status = "running"
    db.commit()
    c2, a2 = _mk_ready(db, "hot2", like=6000, comment=0)
    assert enqueue_process_jobs(db, [c2], settings=settings) == 0

    # job 完成后额度释放 → 第 2 条可入队。
    job.status = "done"
    db.commit()
    assert enqueue_process_jobs(db, [c2], settings=settings) == 1
    db.close()


def test_enqueue_budget_zero_creates_nothing(_engine):
    """budget=0 → 无额度，即便热度爆表也不建 job（写入侧已保证 budget>=0）。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    c, _ = _mk_ready(db, "hot", like=999999, comment=0)
    assert enqueue_process_jobs(db, [c], settings=dict(_GATE, top_pct=1.0, budget=0)) == 0
    assert db.query(JobQueue).count() == 0
    db.close()


def test_enqueue_inflight_budget_does_not_block_dedup_hits(_engine):
    """在途预算不会令「同 asset 已在途」的重复调用重复计数或重复建 job。"""
    from sqlalchemy.orm import sessionmaker

    from app.models import JobQueue
    from app.services.process_gate import enqueue_process_jobs

    db = sessionmaker(bind=_engine)()
    # budget=2：首次入队后仍有 1 个额度，第二次调用会走到 dedup 分支（而非额度耗尽短路）。
    settings = dict(_GATE, top_pct=1.0, budget=2)
    c, _ = _mk_ready(db, "hot", like=5000, comment=0)
    assert enqueue_process_jobs(db, [c], settings=settings) == 1
    assert enqueue_process_jobs(db, [c], settings=settings) == 0  # dedup 吸收
    assert db.query(JobQueue).count() == 1
    db.close()
