"""评论设置里的布尔项（with_sub_comments）读写：不能把 "False" 读成 True。"""


def test_bool_setting_reads_false_correctly(session):
    from app.models import SystemSetting
    from app.core.comment_settings import get_comment_settings

    session.add(SystemSetting(key="comment_fetch_sub_comments", value="False"))
    session.commit()
    assert get_comment_settings(session)["with_sub_comments"] is False


def test_bool_setting_reads_true_correctly(session):
    from app.models import SystemSetting
    from app.core.comment_settings import get_comment_settings

    session.add(SystemSetting(key="comment_fetch_sub_comments", value="True"))
    session.commit()
    assert get_comment_settings(session)["with_sub_comments"] is True


def test_bool_setting_default_false(session):
    from app.core.comment_settings import get_comment_settings

    assert get_comment_settings(session)["with_sub_comments"] is False


def test_api_accepts_bool(client, session):
    r = client.put("/api/system/comment-settings", json={"with_sub_comments": True})
    assert r.status_code == 200, r.text
    assert client.get("/api/system/comment-settings").json()["with_sub_comments"] is True
