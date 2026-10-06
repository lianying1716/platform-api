"""组合服务 understand_service：判类 → 选提示词 → vision → 写库（mock 判类/理解，不联网）。

覆盖：
- 正常链路：判类 commerce → 类型专属提示词 → 落 VideoUnderstanding + 回写 content/asset。
- force_category 跳过判类；classify 关闭跳过判类。
- 判类失败回退 other 且不阻断理解；未知类型归一 other。
- 提示词按类型区分（含类型 Focus 与类型专属维度）。
- 体积超限走压缩分支；视频缺失不硬拦。
- run_extract_frames：帧 upsert 幂等 + frames_status/process_tier 状态。
"""

import asyncio
import os

import pytest

import app.services.understand_service as svc


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    """服务按「storage_root 相对路径」落文件；无 storage_root 时会退化到 cwd。

    这里把 cwd 指到 tmp_path，避免测试在仓库根写 douyin/ 等现场目录。
    """
    monkeypatch.chdir(tmp_path)
    yield


def _seed(
    db,
    *,
    video_file: str = "",
    relative_path: str = "douyin/a/1",
    duration: int = 30,
    cover_url: str = "",
):
    from app.models import ContentItem, MediaAsset, SystemSetting, Transcript

    # 判类/理解链路需要 LLM 端点配置（DB 设置优先）；网络调用已被 mock，仅需非空。
    db.add(SystemSetting(key="llm_base_url", value="http://x/v1"))
    db.add(SystemSetting(key="llm_api_key", value="k"))
    c = ContentItem(
        platform="douyin",
        external_id="aw1",
        content_type="video",
        title="这条裙子我回购5次",
        author_name="小红",
        duration_sec=duration,
        cover_url=cover_url,
    )
    db.add(c)
    db.flush()
    a = MediaAsset(content_id=c.id, relative_path=relative_path, video_file=video_file)
    db.add(a)
    db.flush()
    db.add(Transcript(asset_id=a.id, full_text="评论区扣1，下单送腰带", language="zh", model="base"))
    db.commit()
    return c, a


def _fake_classify(result):
    async def fake(*a, **k):
        return result

    return fake


def _fake_understand(text):
    async def fake(*a, **k):
        return {"text": text, "usage": {"video_tokens": 10}, "model": "qwen3.8-flash", "provider": "qwen"}

    return fake


def test_run_understand_writes(monkeypatch, session):
    c, a = _seed(session)
    monkeypatch.setattr(
        svc,
        "classify",
        _fake_classify({"type": "commerce", "confidence": 0.9, "reason": "卖点+下单", "signals": ["链接"]}),
    )
    monkeypatch.setattr(
        svc.vision,
        "understand",
        _fake_understand('{"summary":"s","dimensions":{"product":"裙子"},"timeline":[{"t":1,"event":"展示"}]}'),
    )

    u = asyncio.run(svc.run_understand(session, a.id))

    assert u.category == "commerce"
    assert u.category_confidence == 0.9
    assert u.category_reason == "卖点+下单"
    assert u.summary == "s"
    assert u.dimensions == {"product": "裙子"}
    assert u.timeline == [{"t": 1, "event": "展示"}]
    assert u.prompt_version == "v1:commerce"
    assert u.status == "done"
    assert u.usage == {"video_tokens": 10}
    session.refresh(c)
    session.refresh(a)
    assert c.auto_category == "commerce"
    assert c.category_confidence == 0.9
    assert a.understand_status == "done"


def test_run_understand_force_category_skips_classify(monkeypatch, session):
    c, a = _seed(session)
    called = {"classify": False}

    async def fake_classify(*a, **k):
        called["classify"] = True
        return {"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}

    monkeypatch.setattr(svc, "classify", fake_classify)
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id, force_category="drama"))

    assert called["classify"] is False
    assert u.category == "drama"
    assert u.prompt_version == "v1:drama"


def test_run_understand_classify_failure_falls_back_to_other(monkeypatch, session):
    c, a = _seed(session)

    async def boom(*a, **k):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr(svc, "classify", boom)
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id))

    assert u.category == "other"
    assert u.category_confidence == 0.0
    assert "网络炸了" in u.category_reason
    assert u.status == "done"


def test_run_understand_classify_disabled(monkeypatch, session):
    from app.models import SystemSetting

    c, a = _seed(session)
    session.add(SystemSetting(key="process_classify_enabled", value="0"))
    session.commit()
    called = {"classify": False}

    async def fake_classify(*a, **k):
        called["classify"] = True
        return {"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}

    monkeypatch.setattr(svc, "classify", fake_classify)
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id))

    assert called["classify"] is False
    assert u.category == "other"


def test_run_understand_unknown_category_normalized_to_other(monkeypatch, session):
    c, a = _seed(session)
    monkeypatch.setattr(
        svc,
        "classify",
        _fake_classify({"type": "totally_new", "confidence": 0.5, "reason": "x", "signals": []}),
    )
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id))
    assert u.category == "other"


def test_build_prompt_is_type_aware(session):
    from app.core.process_settings import get_common_dimensions, get_taxonomy, get_type_prompts

    tax = get_taxonomy(session)
    prompts = get_type_prompts(session)
    dims = get_common_dimensions(session) + prompts["commerce"]["dimensions"]
    prompt = svc.build_prompt("commerce", tax["commerce"], prompts["commerce"]["focus"], dims, "转录文本")

    assert "带货" in prompt
    assert prompts["commerce"]["focus"] in prompt
    assert "product" in prompt and "商品与卖点" in prompt
    assert "null" in prompt
    dims_k = get_common_dimensions(session) + prompts["knowledge"]["dimensions"]
    prompt_k = svc.build_prompt("knowledge", tax["knowledge"], prompts["knowledge"]["focus"], dims_k, "")
    assert "product" not in prompt_k


def test_run_understand_oversize_video_triggers_compress(monkeypatch, session, tmp_path):
    """体积超限走压缩分支：只验证压缩钩子被调用（不真压/不真调模型）。"""
    from app.models import SystemSetting

    session.add(SystemSetting(key="process_video_max_mb", value="0.0001"))
    session.commit()
    c, a = _seed(session, video_file="v.mp4", relative_path="")
    target = svc._asset_video_path(session, a)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(b"\x00" * 4096)

    seen = {}

    def fake_compress(video_path, max_mb, work_dir):
        seen["called"] = True
        return video_path

    monkeypatch.setattr(svc, "_ensure_inline", fake_compress)
    monkeypatch.setattr(svc, "classify", _fake_classify({"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}))
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    asyncio.run(svc.run_understand(session, a.id))
    assert seen.get("called") is True


def test_run_understand_missing_video_does_not_hard_fail(monkeypatch, session):
    """视频缺失时不硬拦：真实运行时由 vision.understand 抛 FileNotFoundError。"""
    c, a = _seed(session, video_file="gone.mp4", relative_path="douyin/none")
    monkeypatch.setattr(svc, "classify", _fake_classify({"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}))
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id))
    assert u.status == "done"


def test_run_extract_frames_upserts_and_sets_tier(monkeypatch, session):
    c, a = _seed(session, video_file="v.mp4")

    def fake_extract(video_path, out_dir, *, max_frames, per_min, scene_threshold=0.3):
        os.makedirs(out_dir, exist_ok=True)
        frames = []
        for i in range(2):
            p = os.path.join(out_dir, f"frame_{i + 1:03d}.jpg")
            with open(p, "wb") as fh:
                fh.write(b"x")
            frames.append({"scene_index": i, "timestamp_sec": float(i), "path": p, "is_keyframe": True})
        return {"frames": frames, "count": len(frames)}

    monkeypatch.setattr(svc.frames, "extract", fake_extract)

    n = asyncio.run(svc.run_extract_frames(session, a.id))
    assert n == 2

    from app.models import AssetFrame

    rows = session.query(AssetFrame).filter_by(asset_id=a.id).order_by(AssetFrame.scene_index).all()
    assert [r.scene_index for r in rows] == [0, 1]
    assert all(r.path.endswith(".jpg") for r in rows)
    session.refresh(a)
    assert a.frames_status == "done"
    assert a.process_tier == "l2"

    asyncio.run(svc.run_extract_frames(session, a.id))
    rows2 = session.query(AssetFrame).filter_by(asset_id=a.id).all()
    assert len(rows2) == 2


def test_run_extract_frames_fewer_frames_removes_orphans(monkeypatch, session):
    c, a = _seed(session, video_file="v.mp4")
    calls = {"n": 0}

    def fake_extract(video_path, out_dir, *, max_frames, per_min, scene_threshold=0.3):
        os.makedirs(out_dir, exist_ok=True)
        n = 3 if calls["n"] == 0 else 1
        calls["n"] += 1
        frames = []
        for i in range(n):
            p = os.path.join(out_dir, f"frame_{i + 1:03d}.jpg")
            with open(p, "wb") as fh:
                fh.write(b"x")
            frames.append({"scene_index": i, "timestamp_sec": float(i), "path": p, "is_keyframe": True})
        return {"frames": frames, "count": len(frames)}

    monkeypatch.setattr(svc.frames, "extract", fake_extract)
    asyncio.run(svc.run_extract_frames(session, a.id))
    asyncio.run(svc.run_extract_frames(session, a.id))

    from app.models import AssetFrame

    rows = session.query(AssetFrame).filter_by(asset_id=a.id).all()
    assert len(rows) == 1


# --------------------------------------------------------------------------- 修复回归（Task 7 review）


class _FakeResp:
    def __init__(self, content: bytes, content_type: str = "image/jpeg"):
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return self._resp


def _patch_http(monkeypatch, resp):
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))


def test_ensure_cover_downloads_and_writes(monkeypatch, session, tmp_path):
    """Important#1：封面真正落盘——_ensure_cover 下载后返回本地路径。"""
    c, a = _seed(session, cover_url="http://img.test/c.png")
    _patch_http(monkeypatch, _FakeResp(b"\x89PNG-fake", "image/png"))

    work = str(tmp_path / "_cover")
    path = asyncio.run(svc._ensure_cover(c, work, 5))

    assert path is not None and path.endswith(".png")
    assert os.path.exists(path)
    with open(path, "rb") as fh:
        assert fh.read() == b"\x89PNG-fake"


def test_ensure_cover_no_url_returns_none(session, tmp_path):
    c, a = _seed(session, cover_url="")
    assert asyncio.run(svc._ensure_cover(c, str(tmp_path), 5)) is None


def test_ensure_cover_download_failure_falls_back(monkeypatch, session, tmp_path):
    """封面下载失败 → None（回退纯文本），不抛。"""
    c, a = _seed(session, cover_url="http://img.test/x.jpg")

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise RuntimeError("网络炸了")

    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _Boom())
    assert asyncio.run(svc._ensure_cover(c, str(tmp_path), 5)) is None


def test_ensure_cover_oversize_returns_none(monkeypatch, session, tmp_path):
    c, a = _seed(session, cover_url="http://img.test/big.jpg")
    _patch_http(monkeypatch, _FakeResp(b"x" * 4096, "image/jpeg"))
    assert asyncio.run(svc._ensure_cover(c, str(tmp_path), 0.0001)) is None


def test_run_understand_use_cover_passes_downloaded_path(monkeypatch, session, tmp_path):
    """Important#1 端到端：use_cover=True 时 classify 收到真实封面路径（不再恒为 None）。"""
    c, a = _seed(session, cover_url="http://img.test/c.jpg")
    _patch_http(monkeypatch, _FakeResp(b"cover-bytes", "image/jpeg"))

    seen = {}

    async def fake_classify(*args, **kwargs):
        seen["cover_path"] = kwargs.get("cover_path")
        seen["use_cover"] = kwargs.get("use_cover")
        return {"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}

    monkeypatch.setattr(svc, "classify", fake_classify)
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    asyncio.run(svc.run_understand(session, a.id, use_cover=True))
    assert seen["use_cover"] is True
    assert seen["cover_path"] and seen["cover_path"].endswith(".jpg")


def test_run_understand_use_cover_off_passes_none(monkeypatch, session):
    c, a = _seed(session, cover_url="http://img.test/c.jpg")
    seen = {}

    async def fake_classify(*args, **kwargs):
        seen["cover_path"] = kwargs.get("cover_path")
        return {"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []}

    monkeypatch.setattr(svc, "classify", fake_classify)
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    asyncio.run(svc.run_understand(session, a.id, use_cover=False))
    assert seen["cover_path"] is None


def test_run_understand_marks_low_confidence(monkeypatch, session):
    """Minor#3（设计 §4）：置信度 < 阈值 → low_confidence=True。"""
    c, a = _seed(session)
    monkeypatch.setattr(
        svc, "classify", _fake_classify({"type": "commerce", "confidence": 0.5, "reason": "x", "signals": []})
    )
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id))
    assert u.category_confidence == 0.5
    assert u.low_confidence is True


def test_run_understand_high_confidence_not_flagged(monkeypatch, session):
    c, a = _seed(session)
    monkeypatch.setattr(
        svc, "classify", _fake_classify({"type": "commerce", "confidence": 0.9, "reason": "x", "signals": []})
    )
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    u = asyncio.run(svc.run_understand(session, a.id))
    assert u.low_confidence is False


def test_run_understand_force_category_not_low_confidence(monkeypatch, session):
    c, a = _seed(session)
    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))
    u = asyncio.run(svc.run_understand(session, a.id, force_category="drama"))
    assert u.low_confidence is False


def test_run_understand_missing_content_raises_input_error(monkeypatch, session):
    """Minor#4：孤儿资产显式报错（UnderstandInputError），不靠兜底 catch 变 other。"""
    from app.models import MediaAsset, SystemSetting

    session.add(SystemSetting(key="llm_base_url", value="http://x/v1"))
    session.add(MediaAsset(content_id=999999, relative_path="douyin/orphan", video_file=""))
    session.commit()
    a = session.query(MediaAsset).filter_by(relative_path="douyin/orphan").one()

    monkeypatch.setattr(svc.vision, "understand", _fake_understand('{"summary":"s","dimensions":{}}'))

    with pytest.raises(svc.UnderstandInputError):
        asyncio.run(svc.run_understand(session, a.id))
    session.refresh(a)
    assert a.understand_status == "error"


def test_ensure_inline_second_pass_when_still_oversize(monkeypatch, session, tmp_path):
    """Minor#2：压一次仍超限 → 再降一档（两档），产出 <= 上限。"""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00" * 1000)
    calls = []

    def fake_run(args, capture_output=True, text=True):
        calls.append(args[args.index("-vf") + 1])
        out = args[-1]
        # 第一次产出仍超限，第二次产出合规
        size = 1000 if len(calls) == 1 else 10
        with open(out, "wb") as fh:
            fh.write(b"\x00" * size)

        class _P:
            returncode = 0
            stderr = ""

        return _P()

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    out = svc._ensure_inline(str(src), 0.0001, str(tmp_path / "_inline"))

    assert len(calls) == 2
    assert os.path.getsize(out) <= 0.0001 * svc._MB
