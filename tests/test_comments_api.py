"""评论查询 API 测试：按点赞降序 + 分页。"""


def test_comments_api_sorted_and_paged(client, _engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import Comment, ContentItem, MediaAsset

    Session = sessionmaker(bind=_engine)
    db = Session()
    c = ContentItem(platform="douyin", external_id="aw1", content_type="video")
    db.add(c)
    db.flush()
    a = MediaAsset(content_id=c.id)
    db.add(a)
    db.flush()
    for i in range(3):
        db.add(
            Comment(
                content_id=c.id,
                platform="douyin",
                comment_id=f"c{i}",
                content=f"评论{i}",
                like_count=i,
            )
        )
    db.commit()
    asset_id = a.id
    db.close()

    r = client.get(f"/api/assets/{asset_id}/comments")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert [x["content"] for x in body["comments"]] == ["评论2", "评论1", "评论0"]
    r2 = client.get(f"/api/assets/{asset_id}/comments?limit=1&offset=1")
    assert [x["content"] for x in r2.json()["comments"]] == ["评论1"]
