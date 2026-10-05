from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class PlatformAccount(Base):
    """一个平台账号一行。status: active/expired/unchecked/error"""

    __tablename__ = "platform_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)  # bilibili/douyin/wechat_mp/xhs
    display_name: Mapped[str] = mapped_column(String(128), default="")
    external_id: Mapped[str] = mapped_column(String(128), default="")  # dedeuserid / sec_user_id 等
    credential_kind: Mapped[str] = mapped_column(String(16), default="cookie")  # cookie / browser
    status: Mapped[str] = mapped_column(String(16), default="unchecked")
    status_message: Mapped[str] = mapped_column(String(512), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    credential: Mapped["AccountCredential | None"] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )


class AccountCredential(Base):
    """凭证密文，与账号一对一。明文 JSON 结构按平台约定，见 modules/*。"""

    __tablename__ = "account_credential"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("platform_account.id"), unique=True)
    ciphertext: Mapped[str] = mapped_column(Text)  # base64(nonce + AES-GCM payload)
    enc_algo: Mapped[str] = mapped_column(String(32), default="aes-gcm-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    account: Mapped[PlatformAccount] = relationship(back_populates="credential")


class CredentialAuditLog(Base):
    __tablename__ = "credential_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(32))  # create/update/read/check/delete
    actor: Mapped[str] = mapped_column(String(64), default="admin")  # admin / api_key:<id> / scheduler
    detail: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SystemSetting(Base):
    """键值配置：admin_password_hash、存储目录等。"""

    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ============================================================
# P2：存储 / 订阅同步 / 内容与素材
# ============================================================


class StorageRoot(Base):
    """素材保存根目录。路径一律 storage_root + relative_path，兼容三系统。"""

    __tablename__ = "storage_root"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # 各系统路径：Linux/macOS 挂载路径、Windows 盘符或 UNC。留空表示该系统未配置。
    path: Mapped[str] = mapped_column(String(512))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Subscription(Base):
    """订阅源：某账号下的收藏夹 / 关注博主 / 合集。"""

    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("platform_account.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # favorite / creator / collection / like
    remote_id: Mapped[str] = mapped_column(String(128))  # 收藏夹ID / UP主mid / 合集ID
    title: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    subdir_template: Mapped[str] = mapped_column(String(256), default="{platform}/{creator}/{date}")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SyncRun(Base):
    """一次订阅同步的执行记录。"""

    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscription.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/success/error
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(512), default="")


class ContentItem(Base):
    """统一内容表：抓取或订阅得到的一条内容（视频/文章）。"""

    __tablename__ = "content_item"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)  # 平台侧内容ID
    content_type: Mapped[str] = mapped_column(String(16), default="video")  # video / article / image
    title: Mapped[str] = mapped_column(String(512), default="")
    author_name: Mapped[str] = mapped_column(String(128), default="")
    author_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    url: Mapped[str] = mapped_column(String(1024), default="")
    cover_url: Mapped[str] = mapped_column(String(1024), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16), default="")  # crawl / subscription
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # crawl_task.id 或 subscription.id
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContentMetricSnapshot(Base):
    """互动指标快照：每次抓取存一行，才能画趋势走势。"""

    __tablename__ = "content_metric_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_item.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    play: Mapped[int] = mapped_column(BigInteger, default=0)
    like: Mapped[int] = mapped_column(BigInteger, default=0)
    comment: Mapped[int] = mapped_column(BigInteger, default=0)
    favorite: Mapped[int] = mapped_column(BigInteger, default=0)
    share: Mapped[int] = mapped_column(BigInteger, default=0)


class MediaAsset(Base):
    """素材文件：内容下载到本地后的产物，跟踪下载/转录/分析三态。"""

    __tablename__ = "media_asset"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_item.id", ondelete="CASCADE"), index=True)
    storage_root_id: Mapped[int | None] = mapped_column(ForeignKey("storage_root.id"), nullable=True)
    relative_path: Mapped[str] = mapped_column(String(512), default="")  # 目录，相对 storage_root
    video_file: Mapped[str] = mapped_column(String(256), default="")
    audio_file: Mapped[str] = mapped_column(String(256), default="")
    download_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/downloading/done/error
    transcribe_status: Mapped[str] = mapped_column(String(16), default="pending")
    analyze_status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ============================================================
# P3：任务队列 / 转录
# ============================================================


class JobQueue(Base):
    """基于 PG 的任务队列。SELECT ... FOR UPDATE SKIP LOCKED 取任务，不引入 Redis。"""

    __tablename__ = "job_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)  # download / transcribe / analyze / sync / crawl
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued/running/done/error
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error: Mapped[str] = mapped_column(Text, default="")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Transcript(Base):
    """转录结果：分段 JSON + 全文。"""

    __tablename__ = "transcript"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("media_asset.id", ondelete="CASCADE"), unique=True)
    language: Mapped[str] = mapped_column(String(16), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    segments: Mapped[list] = mapped_column(JSON, default=list)  # [{start,end,text}]
    full_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ============================================================
# P5：关键词采集
# ============================================================


class CrawlTask(Base):
    """关键词采集任务。"""

    __tablename__ = "crawl_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    keyword: Mapped[str] = mapped_column(String(256))
    sort: Mapped[str] = mapped_column(String(16), default="popular")  # popular / latest
    limit_per_run: Mapped[int] = mapped_column(Integer, default=50)
    with_comments: Mapped[bool] = mapped_column(Boolean, default=False)
    to_candidate: Mapped[bool] = mapped_column(Boolean, default=True)  # 视频是否进下载候选池
    min_play: Mapped[int] = mapped_column(Integer, default=0)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CrawlRun(Base):
    """一次关键词采集执行记录。"""

    __tablename__ = "crawl_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(512), default="")


# ============================================================
# P6：分析
# ============================================================


class AnalysisResult(Base):
    """大模型分析结果：摘要 / 标签 / 结构化拆解。"""

    __tablename__ = "analysis_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("media_asset.id", ondelete="CASCADE"), unique=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    structured: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ============================================================
# P4：AI 接入
# ============================================================


class ApiKey(Base):
    """外部 AI/HTTP 客户端的 API Key。只存哈希，scope 控权。"""

    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(128), index=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")  # 展示用前缀，便于辨认
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # ["read","operate","admin"]
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiCallLog(Base):
    """API Key 调用日志：审计 + 可吊销依据。"""

    __tablename__ = "api_call_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(64))  # 工具名 / 路由
    params_summary: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="")  # ok / error / denied
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
