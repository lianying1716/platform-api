from pathlib import Path

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
    # 凭证加密密钥来源（优先级见 core/crypto.py）：
    #   1) 环境变量 PLATFORM_CREDENTIAL_ENC_KEY（base64 的 32 字节）
    #   2) credential_key_file 指向的文件
    #   3) 都没有则在 data_dir/secret.key 生成（开发兜底）
    credential_enc_key: str = ""
    credential_key_file: Path | None = None
    # 账号定时体检间隔（分钟），0 表示关闭
    account_check_interval: int = 15
    # 扫码会话有效期（秒）
    qr_session_ttl: int = 180
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
    # 关键词采集（P5）：MediaCrawler 目录与子进程超时
    mediacrawler_dir: str = ""
    crawl_timeout_seconds: int = 600

    model_config = {"env_prefix": "PLATFORM_"}

    @property
    def resolved_database_url(self) -> str:
        return self.database_url


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
