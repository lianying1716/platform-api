"""API 请求/响应模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- auth ----------
class AuthStatus(BaseModel):
    initialized: bool


class SetupRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


# ---------- accounts ----------
class PlatformInfo(BaseModel):
    platform: str
    label: str
    credential_kind: str
    supports_qr: bool
    credential_fields: list[dict]
    source_types: list[dict] = []
    download_qualities: list[dict] = []
    keyword_crawl: bool = False
    comments: bool = False
    follow_import: bool = False
    metrics: list[str] = []


class AccountOut(BaseModel):
    id: int
    platform: str
    platform_label: str = ""
    display_name: str
    external_id: str
    credential_kind: str
    status: str
    status_message: str
    last_checked_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    storage_root_id: int | None = None  # NULL = 使用默认根目录
    storage_root_name: str = ""  # 绑定的存储根名称（展示用）
    storage_root_path: str = ""  # 绑定的存储根路径（展示用）
    # 账号级同步总开关：False = 沉默期（禁止该账号所有同步/调用）。
    sync_enabled: bool = True
    credential_preview: dict[str, str] = {}  # 脱敏后的凭证字段

    model_config = {"from_attributes": True}


class AccountStorageUpdate(BaseModel):
    """给账号指定/清除存储根。storage_root_id=None 表示用默认根。"""

    storage_root_id: int | None = None


class AccountSyncToggle(BaseModel):
    """账号级同步总开关：False = 沉默期（禁止该账号所有同步/调用）。"""

    enabled: bool


class AccountCreate(BaseModel):
    platform: str
    display_name: str = ""
    credential: dict  # 明文字段，按平台 credential_fields 约定
    skip_check: bool = False  # True 时不立即探活（例如平台暂时风控）


class CredentialUpdate(BaseModel):
    credential: dict
    skip_check: bool = False


class CheckResponse(BaseModel):
    ok: bool
    status: str
    message: str


# ---------- QR login ----------
class QrLoginCreate(BaseModel):
    platform: str
    display_name: str = ""
    # 「重新扫码」：显式指定要覆盖的账号（扫码失败过的账号常没有 external_id，
    # 靠身份匹配会 409，用户就没法原地重试）。
    account_id: int | None = None


class QrLoginSession(BaseModel):
    session_id: str
    platform: str
    qr_image_base64: str
    expires_in: int
    # 屏幕上这张码**本身**的寿命（秒）。抖音单张码只活 ~59s 就会被登录页换新，
    # 与会话 TTL（expires_in，300s）不是一回事；前端倒计时应优先用它。
    qr_image_expires_in: int | None = None


class QrLoginStatus(BaseModel):
    # pending / scanned / confirmed / captcha（需电脑端人工过滑块）/ success / expired / error
    status: str
    message: str = ""
    account_id: int | None = None
    # 轮询遇到平台二维码自然过期时回传的新二维码（抖音约 60s 一换），前端直接换图继续。
    qr_image_base64: str | None = None
    # 随新图一起回传的"这张码还能活多久"，前端据此重置倒计时（抖音 ~59s）。
    qr_image_expires_in: int | None = None

# ---------- Transport responses shared by the UI ----------
# These models intentionally describe the public JSON shape instead of ORM
# rows.  FastAPI derives the OpenAPI schema from them, and the TypeScript
# generator uses that schema so removing a field breaks `npm run check`.


class AssetMetrics(BaseModel):
    play: int | None = None
    like: int | None = None
    comment: int | None = None
    favorite: int | None = None
    share: int | None = None


class AssetCard(BaseModel):
    asset_id: int
    content_id: int
    platform: str
    account_id: int | None = None
    content_type: str
    title: str
    author_name: str
    author_id: str
    author_avatar: str = ""
    author_avatar_url: str = ""
    author_home_url: str = ""
    cover_url: str = ""
    cover_allowed: bool = False
    has_cover: bool = False
    url: str = ""
    published_at: str | None = None
    created_at: str | None = None
    synced_at: str | None = None
    duration_sec: int = 0
    auto_category: str = ""
    category_confidence: float = 0
    source_kind: str = ""
    size_bytes: int = 0
    playable: bool = False
    tier: str = ""
    download_status: str
    error: str = ""
    transcribe_status: str
    analyze_status: str
    understand_status: str
    metrics: AssetMetrics | None = None


class AssetContentBrief(BaseModel):
    id: int
    platform: str
    title: str
    author_name: str
    author_id: str
    url: str
    published_at: str | None = None


class AssetFileInfo(BaseModel):
    relative_path: str
    video_file: str
    audio_file: str
    full_dir: str


class AssetStatusInfo(BaseModel):
    download: str
    transcribe: str
    analyze: str
    tier: str
    understand: str
    error: str


class AssetTranscript(BaseModel):
    language: str
    model: str
    segments: list[dict]
    full_text: str


class AssetAnalysis(BaseModel):
    summary: str
    tags: list[str]
    model: str


class AssetDetail(BaseModel):
    asset_id: int
    content: AssetContentBrief | None = None
    file: AssetFileInfo
    status: AssetStatusInfo
    metrics: AssetMetrics | None = None
    transcript: AssetTranscript | None = None
    analysis: AssetAnalysis | None = None


class AssetComment(BaseModel):
    comment_id: str
    nickname: str
    avatar: str
    content: str
    like_count: int
    ip_location: str
    created_at: str | None = None


class AssetCommentPage(BaseModel):
    total: int
    comments: list[AssetComment]


class DashboardAccountStats(BaseModel):
    total: int
    abnormal: int


class DashboardContentStats(BaseModel):
    total: int
    video: int
    article: int
    duration_sec: int


class DashboardJobStats(BaseModel):
    pending: int
    failed: int


class DashboardProcessing(DashboardJobStats):
    transcribed: int
    understood: int


class DashboardMetricCoverage(BaseModel):
    known: int
    total: int


class DashboardPlatform(BaseModel):
    platform: str
    total: int
    video: int
    article: int
    play: int | None = None
    like: int | None = None
    comment: int | None = None
    favorite: int | None = None
    metric_coverage: dict[str, DashboardMetricCoverage] = {}
    transcribed: int
    categorized: int


class DashboardCategory(BaseModel):
    key: str
    label: str
    count: int


class DashboardTrendPoint(BaseModel):
    date: str
    added: int


class DashboardHotItem(BaseModel):
    title: str
    platform: str
    author: str
    play: int


class DashboardStorageRoot(BaseModel):
    id: int
    name: str
    path: str
    is_default: bool
    available: bool
    detail: str
    used_bytes: int


class DashboardStorage(BaseModel):
    used_bytes: int
    by_platform: dict[str, int]
    roots: list[DashboardStorageRoot]


class DashboardSummary(BaseModel):
    accounts: DashboardAccountStats
    accounts_error: int
    crawled_today: int
    jobs_pending: int
    jobs_failed: int
    hot_assets: list[DashboardHotItem]
    today_crawled: int
    week_crawled: int
    jobs: DashboardJobStats
    assets_total: int
    content: DashboardContentStats
    platforms: list[DashboardPlatform]
    categories: list[DashboardCategory]
    processing: DashboardProcessing
    engagement: dict[str, int | None]
    metric_coverage: dict[str, DashboardMetricCoverage]
    storage: DashboardStorage
    trend: list[DashboardTrendPoint]
    hot: list[DashboardHotItem]


class QueueJob(BaseModel):
    id: int
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    priority: int
    error: str
    payload: dict
    target_title: str
    created_at: str | None = None
    locked_at: str | None = None
    run_after: str | None = None
    ai_schedule_held: bool
    ai_next_open_at: str | None = None
    ai_next_open_local: str | None = None
    updated_at: str | None = None


class QueueAlert(BaseModel):
    id: int
    kind: str
    severity: str
    status: str
    title: str
    message: str
    job_id: int | None = None
    job_type: str
    occurrences: int
    first_seen_at: str | None = None
    last_seen_at: str | None = None


class QueueAlertList(BaseModel):
    active: int
    alerts: list[QueueAlert]


PipelineSummaryOut = dict[str, dict[str, int]]


class AlertActionOut(BaseModel):
    id: int
    status: str


class RetryAllOut(BaseModel):
    retried: int


class AiScheduleWindowOut(BaseModel):
    days: list[int]
    start: str
    end: str


class AiScheduleControlledJobOut(BaseModel):
    job_type: str
    label: str


class AiScheduleOut(BaseModel):
    enabled: bool
    timezone: str
    windows: list[AiScheduleWindowOut]
    summary: list[str]
    is_open: bool
    next_open_at: str | None = None
    next_open_local: str | None = None
    controlled_jobs: list[AiScheduleControlledJobOut] = []


class AiScheduleUpdateOut(BaseModel):
    ok: bool = True
    schedule: AiScheduleOut


class JobStatusOut(BaseModel):
    id: int
    job_type: str
    status: str
    error: str
    attempts: int
    max_attempts: int
    run_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


# ---------- system / settings surfaces consumed by the UI ----------
class VersionOut(BaseModel):
    app_version: str
    git_commit: str
    build_time: str
    version_source: str
    db_revision: str
    db_head: str
    db_in_sync: bool


class UpdateCheckOut(BaseModel):
    ok: bool = True
    current: str = ""
    latest: str = ""
    available: bool = False
    url: str = ""
    name: str = ""
    commit: str = ""
    expected_commit: str = ""
    message: str = ""
    retry_at: str | None = None
    source: str = ""


class UpdateStatusOut(BaseModel):
    enabled: bool
    current: str


class UpgradeStepOut(BaseModel):
    step: str
    status: str
    detail: str = ""
    at: str | None = None


class UpgradeJobOut(BaseModel):
    version: str = ""
    from_version: str = ""
    expected_commit: str = ""
    commit_verified: bool = False
    status: str = "idle"
    error: str = ""
    backup_path: str = ""
    reversible: bool = True
    db_restored: bool = False
    data_restored: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    steps: list[UpgradeStepOut] = []


class UpgradeStatusOut(BaseModel):
    enabled: bool = False
    busy: bool = False
    job: UpgradeJobOut | None = None
    error: str = ""
    restarting: bool = False


class StorageRootOut(BaseModel):
    id: int
    name: str
    path: str
    is_default: bool
    available: bool
    detail: str


class StorageRootCreatedOut(BaseModel):
    id: int
    available: bool
    detail: str


class OrphanReportOut(BaseModel):
    orphan_count: int
    orphans: list[str]


class FsRootOut(BaseModel):
    path: str
    exists: bool
    writable: bool


class FsRootsOut(BaseModel):
    roots: list[FsRootOut]


class FsEntryOut(BaseModel):
    name: str
    path: str
    is_dir: bool
    writable: bool
    is_link: bool


class FsListOut(BaseModel):
    path: str
    parent: str | None = None
    sep: str
    roots: list[FsRootOut]
    entries: list[FsEntryOut]


class CommentSettingsOut(BaseModel):
    top_pct: float
    budget: int
    min_comments: int
    min_likes: int
    weight: float
    pages: int
    per_page: int
    top_n: int
    interval: float
    with_sub_comments: bool


class BreakdownSettingsOut(BaseModel):
    prompt: str
    viral_multiple: float
    min_play: int
    min_like: int
    min_engagement_rate: float
    publish_within_days: int
    match_rule: str
    auto_transcribe: bool
    auto_understand: bool
    auto_breakdown: bool


class BreakdownSettingsUpdateOut(BaseModel):
    ok: bool = True
    settings: BreakdownSettingsOut


class SyncSettingsOut(BaseModel):
    interval_min_minutes: int
    interval_max_minutes: int
    batch_min: int
    batch_max: int
    download_gap_min_seconds: float
    download_gap_max_seconds: float


class SyncSettingsUpdateOut(BaseModel):
    ok: bool = True
    settings: SyncSettingsOut


class ProcessDimensionOut(BaseModel):
    key: str
    name: str
    ask: str = ""


class ProcessSettingsOut(BaseModel):
    min_heat: int
    min_likes_floor: int
    budget: int
    top_pct: float
    weight_like: float
    weight_comment: float
    weight_play: float
    video_max_mb: float
    cover_max_mb: float
    low_confidence_threshold: float
    classify_enabled: bool
    use_cover: bool
    auto_transcribe: bool
    auto_analyze: bool
    taxonomy: dict[str, str] = {}
    type_prompts: dict[str, dict[str, Any]] = {}
    common_dimensions: list[ProcessDimensionOut] = []


class DiagnosticCheckOut(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    required: bool = True
    fix: str = ""


class DiagnosticsOut(BaseModel):
    checks: list[DiagnosticCheckOut]
    all_ok: bool
    required_ok: bool
    optional_failures: list[str] = []


# ---------- analytics / creator report ----------
class CreatorStatOut(BaseModel):
    author_id: str
    author_name: str
    platform: str
    content_count: int
    avatar: str = ""
    avatar_url: str = ""
    follower_count: int = 0
    verified: bool = False
    profile_id: int | None = None


class KeywordStatOut(BaseModel):
    task_id: int | None = None
    total_added: int = 0
    runs: int = 0


class MetricSnapshotOut(BaseModel):
    captured_at: str
    play: int | None = None
    like: int | None = None
    comment: int | None = None
    favorite: int | None = None
    share: int | None = None


class ReportItemOut(BaseModel):
    content_id: int
    asset_id: int | None = None
    title: str
    url: str
    platform: str
    published_at: str | None = None
    duration_sec: int = 0
    category: str
    play: int | None = None
    like: int | None = None
    comment: int | None = None
    favorite: int | None = None
    share: int | None = None
    engagement_rate: float | None = None
    viral: bool = False


class ReportTimespanOut(BaseModel):
    start: str
    end: str


class ReportOverviewOut(BaseModel):
    content_count: int
    snapshot_count: int
    avg_play: float
    median_play: float
    avg_like: float
    avg_comment: float
    avg_engagement_rate: float
    timespan: ReportTimespanOut | dict = {}


class ReportCadenceOut(BaseModel):
    by_weekday: list[dict]
    by_hour: list[dict]
    best_hours: list[int]


class ReportViralRulesOut(BaseModel):
    threshold_multiple: float
    count: int
    type_distribution: dict[str, int]
    avg_duration_sec: float
    avg_play: float
    top_hours: list[int]
    items: list[ReportItemOut]


class ReportPillarOut(BaseModel):
    category: str
    count: int
    share: float


class ReportBenchmarkOut(BaseModel):
    platform: str
    author_median_play: float
    platform_median_play: float
    author_vs_platform: float
    platform_content_count: int
    platform_snapshot_count: int


class ReportGapOut(BaseModel):
    category: str
    platform_median_play: float
    author_done: bool = False


class CreatorReportOut(BaseModel):
    author_id: str
    author_name: str
    platform: str
    overview: ReportOverviewOut
    cadence: ReportCadenceOut
    top: list[ReportItemOut]
    viral_rules: ReportViralRulesOut
    content_pillars: list[ReportPillarOut]
    benchmark: ReportBenchmarkOut
    gaps: list[ReportGapOut]


class CrawlTaskOut(BaseModel):
    id: int
    platform: str
    account_id: int | None = None
    keyword: str
    sort: str
    limit_per_run: int
    comment_mode: str
    to_candidate: bool
    min_play: int
    interval_minutes: int
    enabled: bool
    last_run_at: str | None = None
    last_attempt_at: str | None = None


class AssetArticle(BaseModel):
    asset_id: int
    title: str
    author_name: str
    platform: str
    url: str
    published_at: str | None = None
    cover_url: str
    html: str
    text: str


class AssetWorkflowStep(BaseModel):
    step: str
    status: str
    depends_on: list[str] = []
    blocked_by: list[str] = []
    job_id: int | None = None
    attempts: int = 0
    error: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class AssetWorkflow(BaseModel):
    run_id: int | None = None
    asset_id: int
    kind: str = "content_processing"
    status: str
    error: str = ""
    created_at: str | None = None
    finished_at: str | None = None
    steps: list[AssetWorkflowStep]


class UnderstandingOut(BaseModel):
    asset_id: int
    status: str = "pending"
    understand_status: str
    process_tier: str = ""
    category: str = ""
    category_confidence: float = 0
    category_reason: str = ""
    low_confidence: bool = False
    dimensions: dict = {}
    summary: str = ""
    timeline: list = []
    model: str = ""
    prompt_version: str = ""
    usage: dict = {}
    routing: dict = {}
    error: str = ""


class TaskLogOut(BaseModel):
    id: int
    level: str
    message: str
    detail: dict = {}
    actor: str
    created_at: str | None = None


class JobLogsOut(BaseModel):
    job_id: int
    logs: list[TaskLogOut]


class CrawlRunOut(BaseModel):
    id: int
    job_id: int | None = None
    status: str
    account_id: int | None = None
    fetched: int
    added: int
    error: str
    started_at: str | None = None
    heartbeat_at: str | None = None
    finished_at: str | None = None


class BreakdownPending(BaseModel):
    asset_id: int
    status: Literal["pending", "doing", "error"]
    error: str = ""
    pipeline: AssetWorkflow | None = None


class BreakdownReport(BaseModel):
    asset_id: int
    status: Literal["done"]
    score: float
    verdict: str = ""
    sections: dict = {}
    comment_insights: dict = {}
    data_context: dict = {}
    understanding_available: bool = False
    model: str = ""
    prompt_version: str = ""
    usage: dict = {}
    created_at: str | None = None
    pipeline: AssetWorkflow | None = None


BreakdownOut = BreakdownPending | BreakdownReport


class ModelCallAttemptOut(BaseModel):
    """One row of the model-call audit list.

    The endpoint keeps returning a JSON array for existing Skill/HTTP callers,
    while OpenAPI carries a concrete item type so the frontend can fail at
    typecheck time when a rendered field is renamed or removed.
    """

    id: int
    attempt_group_id: str
    work_type: str
    operation: str
    asset_id: int | None = None
    asset_title: str = ""
    job_id: int | None = None
    role: str
    provider_id: int | None = None
    provider_name: str = ""
    model_id: int | None = None
    model_key: str = ""
    status: str
    error_class: str = ""
    error_message: str = ""
    http_status: int | None = None
    duration_ms: int = 0
    usage: dict = {}
    request_meta: dict = {}
    created_at: str | None = None
    finished_at: str | None = None


# ---------- model access ----------
class ModelCapabilityOut(BaseModel):
    value: bool = False
    source: str = ""
    config_revision: int = 0
    protocol: str = ""
    detail: str = ""
    transport: str | None = None


class ModelOut(BaseModel):
    id: int
    provider_id: int
    provider_name: str = ""
    provider_protocol: str = ""
    protocol: str = ""
    protocol_source: str = "inherit"
    protocol_override: str | None = None
    provider_base_url: str = ""
    provider_enabled: bool = False
    model_key: str
    display_name: str = ""
    import_source: str = ""
    enabled: bool = True
    capabilities: dict[str, ModelCapabilityOut] = {}
    probe_status: str = "untested"
    probe_error: str = ""
    probe_started_at: str | None = None
    probed_at: str | None = None
    assigned_work_types: list[str] = []


class ProviderOut(BaseModel):
    id: int
    name: str
    preset_key: str = ""
    protocol: str
    base_url: str
    credential_source: str = "api_key"
    has_api_key: bool = False
    api_key_masked: str = ""
    config_revision: int = 1
    enabled: bool = True
    last_test_status: str = ""
    last_test_at: str | None = None
    last_test_error: str = ""
    balance_amount: float | None = None
    balance_error: str = ""
    balance_updated_at: str | None = None
    latency_ms: int | None = None
    latency_error: str = ""
    latency_updated_at: str | None = None


class SetupStateOut(BaseModel):
    stage: str
    action: str
    title: str
    ready: bool
    provider_count: int = 0
    enabled_provider_count: int = 0
    model_count: int = 0
    enabled_model_count: int = 0
    assigned_work_types: list[str] = []
    configured_work_types: list[str] = []
    missing_work_types: list[str] = []
    unverified_work_types: list[str] = []


class AssignmentOut(BaseModel):
    work_type: str
    primary_model_id: int | None = None
    primary_model: ModelOut | None = None
    fallback_model_id: int | None = None
    fallback_model: ModelOut | None = None
    auto_configured: bool = False
    legacy_pending_validation: bool = False
    configured: bool = False
    primary_configured: bool = False
    required_capabilities: list[str] = []


class AssignmentListOut(BaseModel):
    assignments: list[AssignmentOut]
    models: list[ModelOut]
    setup_state: SetupStateOut


class ModelsOut(BaseModel):
    models: list[ModelOut]


class ReconcileProbesOut(BaseModel):
    recovered: int = 0
    models: list[ModelOut]


class ModelImportOut(BaseModel):
    models: list[ModelOut]
    removed: list[str] = []
    kept_in_use: list[str] = []
    probe_requested: dict[str, bool] = {}


class ProviderImportOut(ModelImportOut):
    provider: ProviderOut


class ProviderTestOut(BaseModel):
    ok: bool
    message: str = ""


class ModelNamesOut(BaseModel):
    models: list[str]


class LegacyMigrationOut(BaseModel):
    detected: bool = False
    claimed: bool = False
    needs_api_key: bool = False
    pending_validation: list[str] = []
    message: str = ""


class OkOut(BaseModel):
    ok: bool = True


class AccountDeleteOut(BaseModel):
    ok: bool = True
    soft_deleted: bool = True
    kept_contents: int = 0


class RemoteSourceOut(BaseModel):
    remote_id: str
    title: str = ""


class SyncRunOut(BaseModel):
    id: int
    job_id: int | None = None
    started_at: str | None = None
    heartbeat_at: str | None = None
    finished_at: str | None = None
    status: str
    fetched: int = 0
    added: int = 0
    updated: int = 0
    error: str = ""


class SubscriptionDeletedOut(BaseModel):
    ok: bool = True
    deactivated_sources: int = 0
    kept_contents: int = 0


class FollowedCreatorOut(BaseModel):
    id: int
    account_id: int
    platform: str
    author_id: str
    author_name: str = ""
    avatar: str = ""
    avatar_url: str = ""
    home_url: str = ""
    signature: str = ""
    verified: bool = False
    follower_count: int = 0
    like_count: int = 0
    profile_id: int | None = None
    sync_enabled: bool = True
    sync_videos: bool = False
    full_sync: bool = False
    last_synced_at: str | None = None


class FollowedRefreshOut(BaseModel):
    count: int
    cached: bool = False
    cooling: bool = False


class UnfollowOut(BaseModel):
    ok: bool = True
    deactivated_sources: int = 0
    kept_contents: int = 0


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    rate_limit_per_min: int
    enabled: bool
    last_used_at: str | None = None
    created_at: str


class ApiKeyCreatedOut(BaseModel):
    id: int
    name: str
    api_key: str
    scopes: list[str]


class ApiCallLogOut(BaseModel):
    id: int
    key_id: int | None = None
    tool: str
    params_summary: str = ""
    status: str
    duration_ms: int = 0
    created_at: str


# ---------- asset action / export contracts ----------
class AssetDeleteOut(BaseModel):
    ok: bool
    asset_id: int
    audit_id: int
    job_id: int
    status: str


class JobRefOut(BaseModel):
    job_id: int | None = None
    status: str | None = None
    run_id: int | None = None
    tier: str | None = None
    ai_schedule_held: bool | None = None
    ai_next_open_at: str | None = None


class BulkRefreshOut(BaseModel):
    matched: int = 0
    job_ids: list[int] = []


class ExportDoc(BaseModel):
    title: str
    platform: str
    author: str
    url: str
    transcript: str
    summary: str
    tags: list[str]


class ExportOut(BaseModel):
    """Markdown export shape; ``format=json`` returns a bare ``ExportDoc[]``."""

    format: str
    content: str


class ArticleImportOut(BaseModel):
    asset_id: int
    content_id: int
    title: str
    author_name: str
    already_existed: bool


class AssetModelCallOut(ModelCallAttemptOut):
    pass
