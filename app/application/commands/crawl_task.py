"""Shared crawl-task command schema and validation for HTTP, Skills, and UI."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.domain.capabilities import CRAWL_PLATFORMS
from app.models import CrawlTask, PlatformAccount


class CrawlTaskCommand(BaseModel):
    platform: str
    keyword: str = Field(min_length=1, max_length=256)
    account_id: int | None = None
    sort: str = "popular"
    limit_per_run: int = Field(50, ge=1, le=500)
    comment_mode: str = "off"
    to_candidate: bool = True
    min_play: int = Field(0, ge=0)
    interval_minutes: int = Field(1440, ge=10, le=10080)
    enabled: bool = True

    @field_validator("platform")
    @classmethod
    def _check_platform(cls, value: str) -> str:
        if value not in CRAWL_PLATFORMS:
            raise ValueError(f"不支持的采集平台: {value}")
        return value

    @field_validator("keyword")
    @classmethod
    def _strip_keyword(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("关键词不能为空")
        return value

    @field_validator("sort")
    @classmethod
    def _check_sort(cls, value: str) -> str:
        if value not in ("popular", "latest"):
            raise ValueError("sort 只能是 popular 或 latest")
        return value

    @field_validator("comment_mode")
    @classmethod
    def _check_comment_mode(cls, value: str) -> str:
        if value not in ("off", "gate", "force"):
            raise ValueError("comment_mode 必须是 off/gate/force 之一")
        return value

    @model_validator(mode="before")
    def _check_account_platform(cls, data):
        # Account existence is checked against the session in ``validate_account``;
        # keep this model pure so OpenAPI and Skills expose identical constraints.
        account_id = data.get("account_id") if isinstance(data, dict) else None
        if account_id is not None and int(account_id) <= 0:
            raise ValueError("account_id 必须是正整数")
        return data

    def validate_account(self, db: Session) -> None:
        if self.account_id is None:
            return
        account = db.get(PlatformAccount, self.account_id)
        if (
            account is None
            or account.is_system
            or account.platform != self.platform
            or account.status != "active"
            or not bool(account.sync_enabled)
        ):
            raise ValueError("指定账号不存在、平台不匹配或不是可用账号")

    def to_model_kwargs(self) -> dict:
        return self.model_dump()


def apply_crawl_task_command(db: Session, task: CrawlTask, command: CrawlTaskCommand) -> CrawlTask:
    command.validate_account(db)
    for key, value in command.model_dump().items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    return task


def create_crawl_task(db: Session, command: CrawlTaskCommand) -> CrawlTask:
    command.validate_account(db)
    task = CrawlTask(**command.to_model_kwargs())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
