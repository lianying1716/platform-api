from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class PlatformAccount(Base):
    """一个平台账号一行。status: active/expired/unchecked/error"""

    __tablename__ = "platform_account"
    __table_args__ = (
        Index(
            "uq_platform_account_platform_external_id",
            "platform",
            "external_id",
            unique=True,
            postgresql_where=text("is_system = false AND external_id <> ''"),
            sqlite_where=text("is_system = 0 AND external_id <> ''"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)  # bilibili/douyin/wechat_mp/xhs
    display_name: Mapped[str] = mapped_column(String(128), default="")
    external_id: Mapped[str] = mapped_column(String(128), default="")  # dedeuserid / sec_user_id 等
    credential_kind: Mapped[str] = mapped_column(String(16), default="cookie")  # cookie / browser
    status: Mapped[str] = mapped_column(String(16), default="unchecked")
    status_message: Mapped[str] = mapped_column(String(512), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # 历史迁移/系统导入的隐藏归属
    # 账号级「同步总开关」（沉默期）：False = 该账号所有同步/调用一律禁止（源头拦截）。
    # 与订阅级 enabled 是与门关系：订阅开着但总开关关掉，照样不排队、不执行。
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_crawl_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 采集公平轮换
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 账号级存储根：NULL = 用默认根目录。两个账户可共用同一根，也可各指一个。
    storage_root_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_root.id", ondelete="SET NULL"), nullable=True
    )
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
    # create/update/read/check/delete/verify（verify = 登录后/更新凭证后的真实探活）
    action: Mapped[str] = mapped_column(String(32))
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
    """追踪源：某账号下我跟踪的一个来源（博主 / 收藏夹 / 合集）。

    **这是"我跟踪了什么"的唯一真相**：是否开启同步看 ``enabled``，
    只同步新发布看 ``only_new``，上次同步时间看 ``last_sync_at``。
    博主类的行再按 ``(platform, remote_id)`` 去 ``creator_profile`` 补头像/粉丝等资料，
    资料与关系分开存，避免同一个开关在两个地方各存一份而漂移。
    """

    __tablename__ = "subscription"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "source_type", "remote_id",
            name="uq_subscription_account_source_remote",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("platform_account.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # favorite / creator / collection / like
    remote_id: Mapped[str] = mapped_column(String(128))  # 收藏夹ID / UP主mid / 合集ID
    title: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # creator 订阅专用：是否同步该博主名下的视频。
    # 与 ``enabled``（关注关系是否跟踪）是两件事——导入关注列表默认跟踪关系，
    # 但拉取博主投稿必须由用户手动开启（对齐抖小云的独立开关）。
    sync_videos: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    subdir_template: Mapped[str] = mapped_column(String(256), default="{platform}/{account}/{creator}/{date}")
    # 订阅级存储根覆盖：NULL = 继承账号绑定（再回退默认根）。
    storage_root_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_root.id", ondelete="SET NULL"), nullable=True
    )
    # 加工开关（本订阅级显式 opt-in；命中即绕过热度门控直接入队）。
    auto_transcribe: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_analyze: Mapped[bool] = mapped_column(Boolean, default=False)
    # 同步设置（迁移 0008）：过滤 / 截断 / 只新 / 画质 / 评论门控
    min_play: Mapped[int] = mapped_column(Integer, default=0)  # 最低播放量门槛，0=不限
    publish_after_days: Mapped[int] = mapped_column(Integer, default=0)  # 只同步最近 N 天，0=不限
    max_items: Mapped[int] = mapped_column(Integer, default=50)  # 单次同步最多入库条数
    only_new: Mapped[bool] = mapped_column(Boolean, default=True)  # 只入库新内容
    download_quality: Mapped[str] = mapped_column(String(16), default="best")  # best/1080/720/audio
    with_comments: Mapped[bool] = mapped_column(Boolean, default=False)  # 本批是否走评论门控
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 最后一次**尝试**同步的时间（无论成败）。用于失败退避：last_sync_at 只在成功时更新，
    # 拿它判到期会让持续失败的订阅被反复入队、在风控期反复打平台。
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContentSource(Base):
    """内容来源关系：同一账号内同一视频可来自多个订阅；取消订阅只停用来源。"""

    __tablename__ = "content_source"
    __table_args__ = (
        Index(
            "uq_content_source_content_subscription",
            "content_id",
            "subscription_id",
            unique=True,
            postgresql_where=text("subscription_id IS NOT NULL"),
            sqlite_where=text("subscription_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_item.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_account.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscription.id", ondelete="SET NULL"), nullable=True, index=True
    )
    link_kind: Mapped[str] = mapped_column(String(16), default="subscription")
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    # 全量同步（only_new=false）时被刷新的已有内容条数；只看 added 看不出全量是否生效。
    updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(512), default="")


class ContentItem(Base):
    """统一内容表：抓取或订阅得到的一条内容（视频/文章）。"""

    __tablename__ = "content_item"
    __table_args__ = (
        UniqueConstraint(
            "platform", "external_id", "account_id",
            name="uq_content_item_platform_external_account",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("platform_account.id", ondelete="RESTRICT"), index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)  # 平台侧内容ID
    content_type: Mapped[str] = mapped_column(String(16), default="video")  # video / article / image
    title: Mapped[str] = mapped_column(String(512), default="")
    author_name: Mapped[str] = mapped_column(String(128), default="")
    author_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    url: Mapped[str] = mapped_column(String(1024), default="")
    cover_url: Mapped[str] = mapped_column(String(1024), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)  # 视频时长，秒；判类/分档参考
    auto_category: Mapped[str] = mapped_column(String(32), default="")  # 自动判类结果（类型枚举）
    category_confidence: Mapped[float] = mapped_column(Float, default=0)
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
    process_tier: Mapped[str] = mapped_column(String(16), default="")  # ""/basic/advanced
    understand_status: Mapped[str] = mapped_column(String(16), default="pending")
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
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_account.id", ondelete="SET NULL"), nullable=True, index=True
    )  # 空 = 自动轮换；指定则固定账号
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
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_account.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    """大模型分析结果（速览）：摘要 / 标签。

    深度「为什么火 + 怎么复刻」报告在 ``ViralBreakdown``，两者职责不重叠。
    """

    __tablename__ = "analysis_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("media_asset.id", ondelete="CASCADE"), unique=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(200), default="")
    routing: Mapped[dict] = mapped_column(JSON, default=dict)
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


# ============================================================
# P5+：评论富集
# ============================================================


class Comment(Base):
    """平台评论（一级）。本版仅抖音；comment_id 与 content_id 联合唯一保证重复抓取幂等。"""

    __tablename__ = "comment"
    __table_args__ = (UniqueConstraint("content_id", "comment_id", name="uq_comment_content_cid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content_item.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    comment_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_comment_id: Mapped[str] = mapped_column(String(64), default="0")
    user_id: Mapped[str] = mapped_column(String(64), default="")
    nickname: Mapped[str] = mapped_column(String(128), default="")
    avatar: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    like_count: Mapped[int] = mapped_column(BigInteger, default=0)
    ip_location: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CommentFetchState(Base):
    """评论抓取状态：一作品一行；用于「不重抓」，last_score 供未来增量刷新。"""

    __tablename__ = "comment_fetch_state"

    content_id: Mapped[int] = mapped_column(ForeignKey("content_item.id", ondelete="CASCADE"), primary_key=True)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_score: Mapped[float] = mapped_column(Float, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)


# ============================================================
# P6：素材加工分档（视频理解）
# ============================================================


class VideoUnderstanding(Base):
    """高级档视频理解结果：一素材一行（asset_id 唯一）。"""

    __tablename__ = "video_understanding"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_asset.id", ondelete="CASCADE"), unique=True, index=True
    )
    category: Mapped[str] = mapped_column(String(32), default="other")  # 判类枚举（或 other）
    category_confidence: Mapped[float] = mapped_column(Float, default=0)
    category_reason: Mapped[str] = mapped_column(String(512), default="")
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)  # 置信度低于阈值，供人工复核（设计 §4）
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)  # 类型专属维度 → 值
    summary: Mapped[str] = mapped_column(Text, default="")
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(200), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)  # 含 video_tokens
    routing: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/doing/done/error
    error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ============================================================
# P6：爆款拆解报告
# ============================================================


class ViralBreakdown(Base):
    """爆款拆解报告：一素材一行（asset_id 唯一）。

    LLM 只产出定性字段（verdict/sections/comment_insights），
    data_context 等数字由后端确定性计算后一并落库。
    """

    __tablename__ = "viral_breakdown"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_asset.id", ondelete="CASCADE"), unique=True, index=True
    )
    score: Mapped[float] = mapped_column(Float, default=0)  # 0-100，LLM 评分
    verdict: Mapped[str] = mapped_column(Text, default="")  # 一句话结论：为什么火
    sections: Mapped[dict] = mapped_column(JSON, default=dict)  # 钩子/结构/节奏/文案/触发/CTA/复刻
    comment_insights: Mapped[dict] = mapped_column(JSON, default=dict)  # 评论洞察（主题/争议/提问）
    data_context: Mapped[dict] = mapped_column(JSON, default=dict)  # 确定性计算的互动上下文
    understanding_available: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否复用了 L3 理解
    model: Mapped[str] = mapped_column(String(200), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    routing: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/doing/done/error
    error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ============================================================
# P7：模型接入与按工作分配
# ============================================================


class AiProvider(Base):
    """模型供应商实例。访问密钥只存 AES-GCM 密文。"""

    __tablename__ = "ai_provider"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    preset_key: Mapped[str] = mapped_column(String(64), default="custom")
    protocol: Mapped[str] = mapped_column(String(32), default="openai")  # openai/anthropic/gemini
    base_url: Mapped[str] = mapped_column(String(512))
    credential_source: Mapped[str] = mapped_column(String(16), default="api_key")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_status: Mapped[str] = mapped_column(String(16), default="")
    last_test_error: Mapped[str] = mapped_column(Text, default="")
    # 余额与延迟：都只做**手动刷新**（点数字才打一次网），平时展示上次结果。
    # 可空 = 从未刷新过，前端显示「—」；0 会被误读成真实值，所以不用 0 兜底。
    balance_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    balance_error: Mapped[str] = mapped_column(String(200), default="")
    balance_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_error: Mapped[str] = mapped_column(String(200), default="")
    latency_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AiModel(Base):
    """已导入模型及其实测能力。"""

    __tablename__ = "ai_model"
    __table_args__ = (UniqueConstraint("provider_id", "model_key", name="uq_ai_model_provider_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("ai_provider.id", ondelete="CASCADE"), index=True
    )
    model_key: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    import_source: Mapped[str] = mapped_column(String(16), default="discovered")
    # 模型级协议覆盖：NULL = 继承供应商协议。protocol_source 记录来源
    # （inherit 未判定 / auto 后端实测判定 / manual 用户指定，manual 不被自动覆盖）。
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    protocol_source: Mapped[str] = mapped_column(String(16), default="inherit")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    probe_status: Mapped[str] = mapped_column(String(16), default="untested")
    probe_error: Mapped[str] = mapped_column(Text, default="")
    probed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ModelAssignment(Base):
    """三个固定工作槽位的主备模型。"""

    __tablename__ = "model_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_type: Mapped[str] = mapped_column(String(48), unique=True)
    primary_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_model.id", ondelete="SET NULL"), nullable=True
    )
    fallback_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_model.id", ondelete="SET NULL"), nullable=True
    )
    auto_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    legacy_pending_validation: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ModelCallAttempt(Base):
    """每次真实模型调用一行；审计独立于业务事务。"""

    __tablename__ = "model_call_attempt"
    __table_args__ = (
        Index("ix_model_call_attempt_group", "attempt_group_id"),
        Index("ix_model_call_attempt_work_created", "work_type", "created_at"),
        Index("ix_model_call_attempt_asset_created", "asset_id", "created_at"),
        Index("ix_model_call_attempt_model_created", "model_id", "created_at"),
        Index("ix_model_call_attempt_recovery", "status", "finished_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_group_id: Mapped[str] = mapped_column(String(64))
    work_type: Mapped[str] = mapped_column(String(48))
    operation: Mapped[str] = mapped_column(String(48))
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_provider.id", ondelete="SET NULL"), nullable=True
    )
    provider_name: Mapped[str] = mapped_column(String(120), default="")
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_model.id", ondelete="SET NULL"), nullable=True
    )
    model_key: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="running")
    error_class: Mapped[str] = mapped_column(String(32), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    request_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ============================================================
# 架构地基：创作者 / 原始数据 / 任务日志
# ============================================================


class CreatorProfile(Base):
    """创作者实体：对标账号分析、涨粉追踪、作者主页信息的落点。

    抓取/同步时按 `(platform, author_id)` upsert；`follower_count` 存当前值供趋势参考。
    """

    __tablename__ = "creator_profile"
    __table_args__ = (
        UniqueConstraint("platform", "author_id", name="uq_creator_profile_platform_author"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)  # bilibili/douyin/xhs/...
    author_id: Mapped[str] = mapped_column(String(128))  # 平台侧作者ID
    author_name: Mapped[str] = mapped_column(String(128), default="")
    avatar: Mapped[str] = mapped_column(String(512), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    follower_count: Mapped[int] = mapped_column(BigInteger, default=0)
    following_count: Mapped[int] = mapped_column(BigInteger, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    homepage_url: Mapped[str] = mapped_column(String(512), default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)  # 原始作者信息快照
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RawPayload(Base):
    """原始数据留存：平台一改版，抓到的原始 JSON 还在，可回溯、可重解析。"""

    __tablename__ = "raw_payload"
    __table_args__ = (Index("ix_raw_payload_platform_external_id", "platform", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(16))  # crawl/sync/import/subscription
    external_id: Mapped[str] = mapped_column(String(128), index=True)  # 平台侧内容ID
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_item.id", ondelete="CASCADE"), nullable=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskLog(Base):
    """结构化任务事件流：为 /api/events/stream（SSE）与排障打地基。"""

    __tablename__ = "task_log"
    __table_args__ = (Index("ix_task_log_job_type_created_at", "job_type", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_queue.id", ondelete="SET NULL"), nullable=True
    )
    job_type: Mapped[str] = mapped_column(String(32))  # download/transcribe/analyze/sync/crawl
    level: Mapped[str] = mapped_column(String(8), default="info")  # info/warn/error
    message: Mapped[str] = mapped_column(String(512), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(64), default="worker")  # worker/scheduler/api
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeletionAudit(Base):
    """素材删除审计：谁、何时、为何删除哪条素材；Agent 删除必须有此记录。"""

    __tablename__ = "deletion_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer, index=True)
    content_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="admin")
    reason: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FetchCache(Base):
    """通用抓取缓存：把平台读操作的结果按 TTL 缓存，避免频繁拉取触发风控。

    用途：关注列表 / 博主统计 / 博主投稿列表；以及 ``cooldown:*`` 键记录风控冷却。
    """

    __tablename__ = "fetch_cache"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
