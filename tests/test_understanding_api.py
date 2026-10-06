"""分档加工 API：系统设置读写校验 + 素材分档动作（理解/入队/改类型重跑）。

覆盖计划 Task 8：
- GET/PUT /api/system/process-settings（门控参数 + 类型目录 + 类型提示词，可读可编辑、非法 422）。
- GET /api/assets/{id}/understanding。
- POST /api/assets/{id}/process（basic → transcribe / advanced → understand_video）。
- POST /api/assets/{id}/reclassify（入队判类重跑）。
- 鉴权：无 token → 401。
"""


def _seed_asset(db, *, video_file="v.mp4", relative_path="douyin/a/1"):
    from app.models import ContentItem, MediaAsset

    c = ContentItem(platform="douyin", external_id="aw1", content_type="video", title="t")
    db.add(c)
    db.flush()
    a = MediaAsset(content_id=c.id, relative_path=relative_path, video_file=video_file)
    db.add(a)
    db.commit()
    return c.id, a.id


# --------------------------------------------------------------------------- 鉴权


def test_process_endpoints_require_auth(anon_client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()

    assert anon_client.get("/api/system/process-settings").status_code == 401
    assert anon_client.get(f"/api/assets/{asset_id}/understanding").status_code == 401
    assert anon_client.post(f"/api/assets/{asset_id}/process", json={"tier": "advanced"}).status_code == 401
    assert anon_client.post(f"/api/assets/{asset_id}/reclassify").status_code == 401


# --------------------------------------------------------------------------- 系统设置


def test_get_process_settings_shape(client):
    body = client.get("/api/system/process-settings").json()
    assert body["min_heat"] >= 0
    assert body["provider"] in ("qwen", "gemini")
    assert isinstance(body["taxonomy"], dict) and "commerce" in body["taxonomy"]
    assert isinstance(body["type_prompts"], dict) and "commerce" in body["type_prompts"]
    assert isinstance(body["common_dimensions"], list) and body["common_dimensions"]
    # 两档简化：没有任何以 frame 命名的配置键残留。
    assert all("frame" not in key for key in body)


def test_put_process_settings_rejects_out_of_range(client):
    r = client.put("/api/system/process-settings", json={"min_heat": -1})
    assert r.status_code == 422

    r2 = client.put("/api/system/process-settings", json={"top_pct": 2})
    assert r2.status_code == 422

    r3 = client.put("/api/system/process-settings", json={"provider": "nope"})
    assert r3.status_code == 422


def test_put_process_settings_rejects_bad_structure(client):
    # 类型目录必须是 str->str
    assert client.put("/api/system/process-settings", json={"taxonomy": {"commerce": 1}}).status_code == 422
    # 类型提示词结构非法
    assert (
        client.put(
            "/api/system/process-settings",
            json={"type_prompts": {"commerce": {"focus": "x", "dimensions": [{"key": "k"}]}}},
        ).status_code
        == 422
    )
    # 通用维度结构非法
    assert client.put("/api/system/process-settings", json={"common_dimensions": [{"key": "k"}]}).status_code == 422


def test_put_process_settings_rejects_empty_structure(client):
    """空容器会被读取侧当作合法值，静默退化类型化提示词——写入侧必须 422。"""
    assert client.put("/api/system/process-settings", json={"taxonomy": {}}).status_code == 422
    assert client.put("/api/system/process-settings", json={"type_prompts": {}}).status_code == 422
    assert client.put("/api/system/process-settings", json={"common_dimensions": []}).status_code == 422
    # 空 key 的维度同样非法
    assert (
        client.put(
            "/api/system/process-settings",
            json={"common_dimensions": [{"key": "  ", "name": "n", "ask": "a"}]},
        ).status_code
        == 422
    )


def test_put_process_settings_roundtrip(client):
    payload = {
        "min_heat": 4242,
        "use_cover": True,
        "provider": "gemini",
        "vision_model": "gemini-3.8-flash",
        "taxonomy": {"commerce": "带货v2", "other": "其他"},
        "type_prompts": {
            "commerce": {"focus": "只看价格", "dimensions": [{"key": "price", "name": "价格", "ask": "多少钱"}]}
        },
        "common_dimensions": [{"key": "visual", "name": "画面", "ask": "画了什么"}],
    }
    r = client.put("/api/system/process-settings", json=payload)
    assert r.status_code == 200 and r.json()["ok"] is True

    body = client.get("/api/system/process-settings").json()
    assert body["min_heat"] == 4242
    assert body["use_cover"] is True
    assert body["provider"] == "gemini"
    assert body["vision_model"] == "gemini-3.8-flash"
    assert body["taxonomy"] == {"commerce": "带货v2", "other": "其他"}
    assert body["type_prompts"]["commerce"]["focus"] == "只看价格"
    assert body["common_dimensions"][0]["key"] == "visual"


def test_put_process_settings_partial_does_not_wipe_others(client):
    before = client.get("/api/system/process-settings").json()
    client.put("/api/system/process-settings", json={"min_heat": 111})
    after = client.get("/api/system/process-settings").json()
    assert after["min_heat"] == 111
    assert after["budget"] == before["budget"]
    assert after["taxonomy"] == before["taxonomy"]


# --------------------------------------------------------------------------- 理解 读


def test_get_understanding_pending(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()
    body = client.get(f"/api/assets/{asset_id}/understanding").json()
    assert body["status"] == "pending"


def test_get_understanding_returns_result(client, _engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import VideoUnderstanding

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.add(
        VideoUnderstanding(
            asset_id=asset_id,
            category="commerce",
            category_confidence=0.91,
            category_reason="卖点+下单",
            dimensions={"product": "裙子"},
            summary="一条裙子",
            timeline=[{"t": 1, "event": "展示"}],
            model="qwen3.8-flash",
            prompt_version="v1:commerce",
            usage={"video_tokens": 10},
            status="done",
        )
    )
    db.commit()
    db.close()

    body = client.get(f"/api/assets/{asset_id}/understanding").json()
    assert body["status"] == "done"
    assert body["category"] == "commerce"
    assert body["category_confidence"] == 0.91
    assert body["dimensions"] == {"product": "裙子"}
    assert body["prompt_version"] == "v1:commerce"


def test_get_understanding_unknown_asset(client):
    assert client.get("/api/assets/999999/understanding").status_code == 404


# --------------------------------------------------------------------------- 入队动作


def _jobs(db, job_type):
    from app.models import JobQueue

    return db.query(JobQueue).filter(JobQueue.job_type == job_type).all()


def test_post_process_enqueues_advanced(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()

    r = client.post(f"/api/assets/{asset_id}/process", json={"tier": "advanced"})
    assert r.status_code == 200
    assert r.json()["job_id"]

    db = Session()
    jobs = _jobs(db, "understand_video")
    assert len(jobs) == 1
    assert jobs[0].payload["asset_id"] == asset_id
    from app.models import MediaAsset

    assert db.get(MediaAsset, asset_id).understand_status == "pending"
    db.close()


def test_post_process_enqueues_basic_transcribe(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()

    r = client.post(f"/api/assets/{asset_id}/process", json={"tier": "basic"})
    assert r.status_code == 200
    db = Session()
    jobs = _jobs(db, "transcribe")
    assert len(jobs) == 1
    assert jobs[0].payload["asset_id"] == asset_id
    from app.models import MediaAsset

    assert db.get(MediaAsset, asset_id).transcribe_status == "pending"
    db.close()


def test_post_process_rejects_bad_tier(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()
    assert client.post(f"/api/assets/{asset_id}/process", json={"tier": "l9"}).status_code == 422
    # 旧档位取值（非 basic/advanced）一律 422。
    assert client.post(f"/api/assets/{asset_id}/process", json={"tier": "middle"}).status_code == 422


def test_post_process_unknown_asset(client):
    assert client.post("/api/assets/999999/process", json={"tier": "advanced"}).status_code == 404


def test_post_process_force_category_validated(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()
    # 合法枚举 → 入队并透传
    r = client.post(f"/api/assets/{asset_id}/process", json={"tier": "advanced", "force_category": "commerce"})
    assert r.status_code == 200
    # 非法枚举 → 422
    assert (
        client.post(f"/api/assets/{asset_id}/process", json={"tier": "advanced", "force_category": "nope"}).status_code
        == 422
    )

    Session2 = sessionmaker(bind=_engine)
    db = Session2()
    jobs = _jobs(db, "understand_video")
    assert jobs[0].payload["force_category"] == "commerce"
    db.close()


def test_post_reclassify_enqueues_understand(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()

    r = client.post(f"/api/assets/{asset_id}/reclassify", json={})
    assert r.status_code == 200
    db = Session()
    jobs = _jobs(db, "understand_video")
    assert len(jobs) == 1
    db.close()


def test_post_reclassify_use_cover_false_string(client, _engine):
    """字符串 "false" 必须归一为 False，而不是被裸 bool() 判成 True。"""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()

    r = client.post(f"/api/assets/{asset_id}/reclassify", json={"use_cover": "false"})
    assert r.status_code == 200
    db = Session()
    jobs = _jobs(db, "understand_video")
    assert jobs[0].payload["use_cover"] is False
    db.close()


def test_post_reclassify_use_cover_true_and_force_category(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()

    r = client.post(
        f"/api/assets/{asset_id}/reclassify",
        json={"use_cover": True, "force_category": "commerce"},
    )
    assert r.status_code == 200
    db = Session()
    jobs = _jobs(db, "understand_video")
    assert jobs[0].payload["use_cover"] is True
    assert jobs[0].payload["force_category"] == "commerce"
    db.close()


def test_post_reclassify_rejects_unknown_category(client, _engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_engine)
    db = Session()
    _, asset_id = _seed_asset(db)
    db.close()
    assert (
        client.post(f"/api/assets/{asset_id}/reclassify", json={"force_category": "nope"}).status_code
        == 422
    )


def test_post_reclassify_unknown_asset(client):
    assert client.post("/api/assets/999999/reclassify", json={}).status_code == 404
