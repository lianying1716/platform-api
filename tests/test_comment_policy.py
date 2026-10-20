"""Crawl and subscription comment modes must consume one policy."""

import pytest
from sqlalchemy import select

from app.models import ContentItem, ContentMetricSnapshot, JobQueue


def _content(session, external_id="c1"):
    content = ContentItem(
        account_id=1,
        platform="douyin",
        external_id=external_id,
        content_type="video",
    )
    session.add(content)
    session.flush()
    session.add(ContentMetricSnapshot(content_id=content.id, like=0, comment=0))
    session.commit()
    return content


@pytest.mark.parametrize(
    "mode,expected",
    [("off", 0), ("gate", 0), ("force", 1)],
)
def test_comment_mode_is_consumed(session, mode, expected):
    from app.services.comment_gate import enqueue_comment_jobs

    content = _content(session)
    created = enqueue_comment_jobs(
        session,
        [content],
        platform="douyin",
        settings={"top_pct": 1.0, "budget": 10, "min_comments": 20, "min_likes": 100, "weight": 1},
        mode=mode,
    )
    assert created == expected
    assert len(session.scalars(select(JobQueue).where(JobQueue.job_type == "fetch_comments")).all()) == expected
