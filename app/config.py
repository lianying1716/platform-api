from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局配置。环境变量前缀 PLATFORM_，例如 PLATFORM_PORT=9100。"""

    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    # 默认连本地开发用 PostgreSQL（容器映射 5433，与生产同构）；生产由 PLATFORM_DATABASE_URL 覆盖
    database_url: str = "postgresql+psycopg2://platform:platform@127.0.0.1:5433/platform"
    # 启动时是否用 SQLAlchemy create_all 自动建表。开发默认 True；
    # 生产（compose）置 false，建表完全交给 Alembic（docker-entrypoint.sh）。
    auto_create_tables: bool = True
    host: str = "0.0.0.0"
    port: int = 9100
    # 是否由后端托管前端构建产物（app/static）。
    # 开发默认 False：后端只提供 /api，前端走 Vite dev server(:5173)，
    #   避免"存在 app/static 就静默托管旧正式包"导致开发和正式包互相污染。
    # 生产（Docker）显式置 true。
    serve_frontend: bool = False
    # 凭证加密密钥来源（优先级见 core/crypto.py）：
    #   1) 环境变量 PLATFORM_CREDENTIAL_ENC_KEY（base64 的 32 字节）
    #   2) credential_key_file 指向的文件
    #   3) 都没有则在 data_dir/secret.key 生成（开发兜底）
    credential_enc_key: str = ""
    credential_key_file: Path | None = None
    # 账号定时体检间隔（分钟），0 表示关闭
    account_check_interval: int = 15
    # 订阅同步扫描间隔（分钟）：按 subscription.interval_minutes 到期即入队 sync。
    # 0 表示关闭（只保留手动同步）。
    subscription_scan_interval: int = 5
    # 扫码会话有效期（秒）。抖音扫码后常有"手机上二次确认"步骤，180s 偏紧，
    # 用户还没点确认就超时了，所以给到 5 分钟。
    qr_session_ttl: int = 300
    # worker 并发槽数（下载/转录重活，单机几十条/天，低并发即可）
    worker_concurrency: int = 2
    # 转录默认模型与设备（faster-whisper）
    whisper_model: str = "base"
    whisper_device: str = "auto"  # auto / cpu / cuda
    whisper_compute_type: str = "int8"
    # 外部大模型分析（P6）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # 关键词采集（P5）：单次抓取超时（全部平台已原生，不再依赖 MediaCrawler 子进程）
    crawl_timeout_seconds: int = 600

    model_config = {"env_prefix": "PLATFORM_", "env_file": ".env"}

    @field_validator("credential_key_file", mode="before")
    @classmethod
    def _empty_key_file_is_none(cls, value: object) -> object:
        # .env 里 PLATFORM_CREDENTIAL_KEY_FILE=（空值）会被解析成 Path('.')，
        # 进而在 crypto._load_key 触发 IsADirectoryError；统一按“未配置”处理。
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def resolved_database_url(self) -> str:
        return self.database_url


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
