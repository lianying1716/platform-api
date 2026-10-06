"""开发模拟数据种子脚本测试（Task 5 / 设计 §B1）。

覆盖：
- 对测试库直接调用 ``seed(session)`` 两次：关键表非空，且第二次行数完全不变（幂等）；
- 凭证以密文入库，明文假值不落库（无真实感密钥明文）；
- ``seed(session, reset=True)`` 只清 demo 行，真实数据保留。
"""

import importlib.util
import sys
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parent.parent

# 关键表：种完后必须有数据（对应前端各页面）。
_KEY_MODELS = [
    "PlatformAccount",
    "AccountCredential",
    "Subscription",
    "SyncRun",
    "CrawlTask",
    "CrawlRun",
    "ContentItem",
    "ContentMetricSnapshot",
    "CreatorProfile",
    "RawPayload",
    "TaskLog",
    "MediaAsset",
    "Transcript",
    "AnalysisResult",
    "VideoUnderstanding",
    "ViralBreakdown",
    "Comment",
    "JobQueue",
]


def _load_seed_module():
    path = ROOT / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["seed_demo"] = module
    spec.loader.exec_module(module)
    return module


def _total(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_seed_demo_populates_and_is_idempotent(session):
    import app.models as models

    seed_demo = _load_seed_module()

    first = seed_demo.seed(session)
    # 关键表非空
    for name in _KEY_MODELS:
        model = getattr(models, name)
        assert _total(session, model) > 0, f"{name} 未被种子填充"
    assert first["content_item"] >= 30
    assert first["media_asset"] >= 20
    assert first["content_metric_snapshot"] >= first["content_item"] * 5

    # 第二次：幂等——行数完全不变
    before = {name: _total(session, getattr(models, name)) for name in _KEY_MODELS}
    second = seed_demo.seed(session)
    after = {name: _total(session, getattr(models, name)) for name in _KEY_MODELS}
    assert before == after, "第二次 seed 不应新增任何行"
    assert first == second


def test_seed_demo_credentials_are_encrypted(session):
    from app.core.crypto import decrypt_credential
    from app.models import AccountCredential

    seed_demo = _load_seed_module()
    seed_demo.seed(session)

    creds = session.scalars(select(AccountCredential)).all()
    assert creds, "应有账号凭证"
    for cred in creds:
        # 明文假值绝不能出现在密文里
        assert "DEMO_FAKE" not in cred.ciphertext
        assert cred.enc_algo == "aes-gcm-v1"
        plaintext = decrypt_credential(cred.ciphertext)
        assert any("DEMO_FAKE" in str(v) for v in plaintext.values())


def test_seed_demo_reset_keeps_real_rows(session):
    from app.models import PlatformAccount

    seed_demo = _load_seed_module()
    seed_demo.seed(session)

    real = PlatformAccount(platform="bilibili", display_name="真实主号（非 demo）", status="active")
    session.add(real)
    session.commit()
    real_id = real.id

    seed_demo.seed(session, reset=True)

    session.expire_all()
    kept = session.get(PlatformAccount, real_id)
    assert kept is not None, "reset 不应删除真实账号"
    assert kept.display_name == "真实主号（非 demo）"
    # demo 账号仍在（重建）
    demo_names = [a.display_name for a in session.scalars(select(PlatformAccount)).all()]
    assert any(n.startswith("[DEMO]") for n in demo_names)
