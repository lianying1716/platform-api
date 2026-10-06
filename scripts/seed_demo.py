#!/usr/bin/env python
"""开发模拟数据种子脚本（幂等，Task 5 / 设计 §B1）。

用途：把**开发库**填满「像真的一样」的演示数据，让前端每个页面（总览 / 账号中心 /
关键词采集 / 订阅同步 / 素材库 / 处理队列 / 数据看板 / AI 接入）都有内容可看。

用法::

    python scripts/seed_demo.py            # 幂等：已种过则跳过，返回现有计数
    python scripts/seed_demo.py --reset    # 先删掉 demo 行，再重建

安全约定
--------
- **绝不联网**：所有 URL 都指向 demo.example.com，凭证是明显的假值 DEMO_FAKE_*。
- **只写 demo 数据**：每行都带哨兵——账号 display_name 前缀 [DEMO]、内容 extra.demo=True、
  队列/日志 payload/detail.demo=True、创作者 raw.demo=True、订阅 title 前缀 [DEMO]、
  采集任务 keyword 前缀 [demo]，以及 SystemSetting 键 demo_seed_version。
- --reset **只删**这些带哨兵的行，真实账号 / 订阅 / 内容一律不动。
- 凭证走 app.core.crypto.encrypt_credential 加密入库，明文不落库。
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 允许 `python scripts/seed_demo.py` 直接运行（此时 sys.path[0] 是 scripts/）。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, inspect, select  # noqa: E402

from app.core.crypto import encrypt_credential  # noqa: E402
from app.models import (  # noqa: E402
    AccountCredential,
    AnalysisResult,
    Comment,
    CommentFetchState,
    ContentItem,
    ContentMetricSnapshot,
    CrawlRun,
    CrawlTask,
    CreatorProfile,
    FollowedCreator,
    JobQueue,
    MediaAsset,
    PlatformAccount,
    RawPayload,
    Subscription,
    SyncRun,
    SystemSetting,
    TaskLog,
    Transcript,
    VideoUnderstanding,
    ViralBreakdown,
)

SENTINEL_KEY = "demo_seed_version"
SENTINEL_VALUE = "1"
PREFIX = "[DEMO]"
TASK_PREFIX = "[demo]"
RNG_SEED = 20261006
DEMO_HOST = "https://demo.example.com"

# 订阅新增字段（迁移 0008）；若模型/库还没有这些列则自动跳过。
_NEW_SUB_FIELDS = (
    "min_play",
    "publish_after_days",
    "max_items",
    "only_new",
    "download_quality",
    "with_comments",
)


def _fake_secret(tag: str) -> str:
    """明显的假凭证值——绝不含任何真实密钥。"""
    return f"DEMO_FAKE_{tag}_NOT_A_REAL_SECRET"


# ---------------------------------------------------------------------------
# 静态演示数据
# ---------------------------------------------------------------------------

_ACCOUNTS = [
    {
        "platform": "douyin",
        "suffix": "抖小云·主号",
        "status": "active",
        "external_id": "MS4wLjABAAAADEMOchef0001",
        "expires_in_days": 90,
        "status_message": "Cookie 有效（模拟数据）",
        "credential": {
            "cookie": _fake_secret("DOUYIN_COOKIE"),
            "sec_user_id": "MS4wLjABAAAADEMOchef0001",
            "my_user_id": "7100000000001",
        },
    },
    {
        "platform": "douyin",
        "suffix": "抖小云·备用号",
        "status": "expired",
        "external_id": "MS4wLjABAAAADEMObackup0002",
        "expires_in_days": -3,
        "status_message": "Cookie 已过期，请重新登录（模拟数据）",
        "credential": {
            "cookie": _fake_secret("DOUYIN_BACKUP_COOKIE"),
            "sec_user_id": "MS4wLjABAAAADEMObackup0002",
            "my_user_id": "7100000000002",
        },
    },
    {
        "platform": "bilibili",
        "suffix": "B站·主号",
        "status": "active",
        "external_id": "10000001",
        "expires_in_days": 30,
        "status_message": "SESSDATA 有效（模拟数据）",
        "credential": {
            "sessdata": _fake_secret("BILI_SESSDATA"),
            "bili_jct": _fake_secret("BILI_JCT"),
            "buvid3": _fake_secret("BILI_BUVID3"),
            "dedeuserid": "10000001",
            "ac_time_value": _fake_secret("BILI_AC_TIME"),
        },
    },
    {
        "platform": "bilibili",
        "suffix": "B站·小号",
        "status": "unchecked",
        "external_id": "10000099",
        "expires_in_days": None,
        "status_message": "尚未体检（模拟数据）",
        "credential": {
            "sessdata": _fake_secret("BILI_SMALL_SESSDATA"),
            "bili_jct": _fake_secret("BILI_SMALL_JCT"),
            "buvid3": _fake_secret("BILI_SMALL_BUVID3"),
            "dedeuserid": "10000099",
        },
    },
    {
        "platform": "xhs",
        "suffix": "小红书·种草号",
        "status": "active",
        "external_id": "xhs_demo_account_1",
        "expires_in_days": 15,
        "status_message": "Cookie 有效（模拟数据）",
        "credential": {
            "cookie": _fake_secret("XHS_COOKIE"),
            "web_session": _fake_secret("XHS_WEB_SESSION"),
            "a1": _fake_secret("XHS_A1"),
        },
    },
]

# 订阅模板：account_index 指向 _ACCOUNTS 下标
_SUBSCRIPTIONS = [
    {
        "account_index": 0,
        "source_type": "favorite",
        "remote_id": "fav_douyin_like_001",
        "title": f"{PREFIX} 抖音·我喜欢的视频",
        "enabled": True,
        "auto_download": True,
        "interval_minutes": 60,
        "subdir_template": "{platform}/{creator}/{date}",
        "min_play": 5000,
        "publish_after_days": 30,
        "max_items": 30,
        "only_new": True,
        "download_quality": "1080",
        "with_comments": True,
    },
    {
        "account_index": 0,
        "source_type": "creator",
        "remote_id": "MS4wLjABAAAADEMOchef0001",
        "title": f"{PREFIX} 抖音·关注 老饭骨",
        "enabled": True,
        "auto_download": True,
        "interval_minutes": 180,
        "subdir_template": "{platform}/{creator}/{date}_{title}",
        "min_play": 10000,
        "publish_after_days": 0,
        "max_items": 50,
        "only_new": True,
        "download_quality": "best",
        "with_comments": False,
    },
    {
        "account_index": 1,
        "source_type": "creator",
        "remote_id": "MS4wLjABAAAADEMOfit0002",
        "title": f"{PREFIX} 抖音·关注 帕梅拉",
        "enabled": False,
        "auto_download": False,
        "interval_minutes": 720,
        "subdir_template": "{platform}/{date}",
        "min_play": 0,
        "publish_after_days": 7,
        "max_items": 20,
        "only_new": False,
        "download_quality": "720",
        "with_comments": False,
    },
    {
        "account_index": 2,
        "source_type": "favorite",
        "remote_id": "fav_bili_tech_001",
        "title": f"{PREFIX} B站·技术收藏夹",
        "enabled": True,
        "auto_download": True,
        "interval_minutes": 120,
        "subdir_template": "{platform}/{creator}/{date}",
        "min_play": 1000,
        "publish_after_days": 60,
        "max_items": 40,
        "only_new": True,
        "download_quality": "1080",
        "with_comments": True,
    },
    {
        "account_index": 2,
        "source_type": "collection",
        "remote_id": "col_bili_10000001_1",
        "title": f"{PREFIX} B站·合集 影视飓风科普",
        "enabled": True,
        "auto_download": True,
        "interval_minutes": 240,
        "subdir_template": "{platform}/{creator}/{date}_{title}",
        "min_play": 20000,
        "publish_after_days": 0,
        "max_items": 50,
        "only_new": True,
        "download_quality": "best",
        "with_comments": False,
    },
    {
        "account_index": 3,
        "source_type": "creator",
        "remote_id": "10000002",
        "title": f"{PREFIX} B站·UP主 程序员鱼皮",
        "enabled": True,
        "auto_download": False,
        "interval_minutes": 1440,
        "subdir_template": "{platform}/{creator}/{date}",
        "min_play": 0,
        "publish_after_days": 0,
        "max_items": 10,
        "only_new": True,
        "download_quality": "audio",
        "with_comments": False,
    },
]

_CRAWL_TASKS = [
    {
        "platform": "douyin",
        "keyword": f"{TASK_PREFIX} 美食探店",
        "sort": "popular",
        "limit_per_run": 50,
        "with_comments": True,
        "to_candidate": True,
        "min_play": 10000,
        "interval_minutes": 1440,
        "enabled": True,
    },
    {
        "platform": "bilibili",
        "keyword": f"{TASK_PREFIX} AI 编程",
        "sort": "latest",
        "limit_per_run": 30,
        "with_comments": False,
        "to_candidate": True,
        "min_play": 1000,
        "interval_minutes": 720,
        "enabled": True,
    },
    {
        "platform": "xhs",
        "keyword": f"{TASK_PREFIX} 露营装备",
        "sort": "popular",
        "limit_per_run": 40,
        "with_comments": True,
        "to_candidate": True,
        "min_play": 500,
        "interval_minutes": 1440,
        "enabled": True,
    },
    {
        "platform": "douyin",
        "keyword": f"{TASK_PREFIX} 健身教程",
        "sort": "popular",
        "limit_per_run": 50,
        "with_comments": False,
        "to_candidate": True,
        "min_play": 2000,
        "interval_minutes": 2880,
        "enabled": False,
    },
]

_AUTHORS = {
    "douyin": [
        {
            "author_id": "MS4wLjABAAAADEMOchef0001",
            "author_name": "老饭骨·王刚",
            "followers": 8_600_000,
            "verified": True,
            "bio": "传承中华美食，专注家常菜教学",
            "avatar": f"{DEMO_HOST}/avatar/douyin_chef.jpg",
        },
        {
            "author_id": "MS4wLjABAAAADEMOfit0002",
            "author_name": "帕梅拉·健身日记",
            "followers": 3_200_000,
            "verified": True,
            "bio": "每天 10 分钟，跟着练就有效果",
            "avatar": f"{DEMO_HOST}/avatar/douyin_fit.jpg",
        },
        {
            "author_id": "MS4wLjABAAAADEMOcamp0003",
            "author_name": "露营的阿远",
            "followers": 460_000,
            "verified": False,
            "bio": "周末去哪玩？带你找宝藏露营地",
            "avatar": f"{DEMO_HOST}/avatar/douyin_camp.jpg",
        },
    ],
    "bilibili": [
        {
            "author_id": "10000001",
            "author_name": "影视飓风",
            "followers": 5_100_000,
            "verified": True,
            "bio": "用影像讲故事的技术宅",
            "avatar": f"{DEMO_HOST}/avatar/bili_ysjf.jpg",
        },
        {
            "author_id": "10000002",
            "author_name": "程序员鱼皮",
            "followers": 820_000,
            "verified": True,
            "bio": "编程学习 | 求职干货 | 项目实战",
            "avatar": f"{DEMO_HOST}/avatar/bili_yupi.jpg",
        },
        {
            "author_id": "10000003",
            "author_name": "科技美学",
            "followers": 2_400_000,
            "verified": True,
            "bio": "独立客观的数码评测",
            "avatar": f"{DEMO_HOST}/avatar/bili_kejimeixue.jpg",
        },
    ],
    "xhs": [
        {
            "author_id": "xhs_demo_1",
            "author_name": "小鹿的露营日记",
            "followers": 128_000,
            "verified": False,
            "bio": "轻量化露营 | 装备测评 | 周末去哪儿",
            "avatar": f"{DEMO_HOST}/avatar/xhs_deer.jpg",
        },
        {
            "author_id": "xhs_demo_2",
            "author_name": "城市美食猎人",
            "followers": 76_000,
            "verified": True,
            "bio": "走街串巷，只推好吃的",
            "avatar": f"{DEMO_HOST}/avatar/xhs_food.jpg",
        },
    ],
}

_CONTENT_TITLES = {
    "douyin": [
        "3分钟教你做出饭店级红烧肉",
        "夜市10块钱的炒面，老板一天卖300份",
        "空气炸锅做蛋挞，零失败教程",
        "第一次做佛跳墙，结果翻车了？",
        "巷子里的老字号小吃，排队一小时",
        "10分钟快手早餐，一周不重样",
        "挑战吃遍全城辣度天花板",
        "老饭骨教你吊高汤的秘诀",
        "减脂餐也能很好吃？",
        "爆炒花甲的3个关键步骤",
        "探店人均30的宝藏川菜馆",
        "红烧狮子头，软糯入味",
        "5块钱的早餐能有多好吃",
        "夏日解暑神器：绿豆冰沙",
        "新手也能做的提拉米苏",
        "路边摊的锅气是怎么来的",
    ],
    "bilibili": [
        "我用一周时间复刻了《原神》里的料理",
        "从零手写一个操作系统，我踩了哪些坑",
        "AI 编程真的能取代程序员吗？",
        "深度解析：B站算法为什么总推给你",
        "1000元组装一台能玩3A的电脑",
        "Vlog：程序员的真实一天",
        "影视飓风探班：一部电影的幕后",
        "开源项目从0到1万Star的全过程",
        "我用代码画出了银河系",
        "盘点2026年最值得学的编程语言",
        "3小时搞懂Transformer架构",
        "把家改造成赛博朋克风格",
        "深度测评：国产大模型哪家强",
        "我用AI做了一个完整的游戏",
        "程序员如何优雅地拒绝需求",
        "从0搭建一个高并发系统",
    ],
    "xhs": [
        "露营新手必看：装备清单大公开",
        "周末去了趟莫干山，治愈了",
        "人均100的露营火锅也太香了",
        "帐篷选购避坑指南",
        "露营拍照姿势大全",
        "北京周边5个宝藏露营地",
        "城市美食猎人：这家苍蝇馆子绝了",
        "一个人也要好好吃饭",
    ],
}

_CATEGORY_RULES = [
    (("红烧", "炒", "蛋挞", "佛跳墙", "小吃", "早餐", "川菜", "狮子头", "绿豆", "提拉米苏", "锅气", "高汤", "花甲", "探店", "美食", "料理"), "美食"),
    (("健身", "减脂", "帕梅拉"), "健身"),
    (("露营", "莫干山", "帐篷"), "户外"),
    (("编程", "代码", "AI", "算法", "Transformer", "大模型", "系统", "开源", "电脑", "Vlog"), "科技"),
    (("影视", "电影", "测评", "数码", "赛博"), "影视"),
]

_PLAY_BASE = {"douyin": (8_000, 1_500_000), "bilibili": (2_000, 600_000), "xhs": (800, 120_000)}

_COMMENT_TEXTS = [
    "照着做了，真的成功了！",
    "收藏了，周末就试试",
    "这也太香了吧，看饿了",
    "求问食材在哪里买？",
    "博主讲的太细致了，感谢",
    "哈哈最后那个翻车太真实了",
    "已经三连，期待下期",
    "这个技巧我用了很多年，确实好用",
    "看完立刻下单了装备",
    "有没有平替推荐？",
]

_NICKNAMES = [
    "爱吃的小王",
    "厨房小白菜",
    "隔壁老李",
    "数码发烧友",
    "周末去哪儿",
    "打工人日记",
    "理性种草机",
    "追风少年",
    "认真生活的人",
    "路过的网友",
]

_IP_LOCATIONS = ["北京", "上海", "广东", "浙江", "四川", "江苏", "山东", "湖北", ""]


def _category_of(title: str) -> str:
    for keywords, category in _CATEGORY_RULES:
        if any(k in title for k in keywords):
            return category
    return "生活"


# ---------------------------------------------------------------------------
# 幂等 / 哨兵
# ---------------------------------------------------------------------------

def _table_columns(session, table: str) -> set[str]:
    """当前库中某表的列名；拿不到则返回空集（调用方据此跳过缺失列）。

    必须用 ``session.connection()``（复用会话当前连接）而非
    ``inspect(session.get_bind())``：后者会向连接池另借一个连接，归还时触发
    rollback，把同一事务里已 flush 但未提交的插入一并回滚（StaticPool 内存
    SQLite 上尤其致命）。
    """
    try:
        return {col["name"] for col in inspect(session.connection()).get_columns(table)}
    except Exception:  # noqa: BLE001  表不存在等情况
        return set()


def _demo_sets(session) -> dict[str, set[int]]:
    """按哨兵收集所有 demo 行的主键。"""
    content_ids = {
        c.id for c in session.scalars(select(ContentItem)).all() if (c.extra or {}).get("demo")
    }
    account_ids = {
        a.id
        for a in session.scalars(select(PlatformAccount)).all()
        if (a.display_name or "").startswith(PREFIX)
    }
    # 只取 (id, title)：订阅表可能还没有迁移 0008 的新列，整行 SELECT 会在
    # 旧库上报 UndefinedColumn；这里只读哨兵需要的列，保证脚本能跑。
    sub_ids = {
        sid
        for sid, title in session.execute(select(Subscription.id, Subscription.title)).all()
        if (title or "").startswith(PREFIX)
    }
    task_ids = {
        t.id for t in session.scalars(select(CrawlTask)).all() if (t.keyword or "").startswith(TASK_PREFIX)
    }
    job_ids = {
        j.id for j in session.scalars(select(JobQueue)).all() if (j.payload or {}).get("demo")
    }
    asset_ids = {
        m.id for m in session.scalars(select(MediaAsset)).all() if m.content_id in content_ids
    }
    return {
        "content_ids": content_ids,
        "account_ids": account_ids,
        "sub_ids": sub_ids,
        "task_ids": task_ids,
        "job_ids": job_ids,
        "asset_ids": asset_ids,
    }


def _purge(session) -> None:
    """删除所有带哨兵的 demo 行；真实数据不动。"""
    d = _demo_sets(session)
    cids = list(d["content_ids"]) or [-1]
    aids = list(d["asset_ids"]) or [-1]
    accs = list(d["account_ids"]) or [-1]
    subs = list(d["sub_ids"]) or [-1]
    tasks = list(d["task_ids"]) or [-1]
    keep = {"synchronize_session": False}

    # 依赖内容 / 素材的子表（先删）
    session.query(Comment).filter(Comment.content_id.in_(cids)).delete(**keep)
    session.query(CommentFetchState).filter(CommentFetchState.content_id.in_(cids)).delete(**keep)
    session.query(ContentMetricSnapshot).filter(ContentMetricSnapshot.content_id.in_(cids)).delete(**keep)
    session.query(Transcript).filter(Transcript.asset_id.in_(aids)).delete(**keep)
    session.query(AnalysisResult).filter(AnalysisResult.asset_id.in_(aids)).delete(**keep)
    session.query(VideoUnderstanding).filter(VideoUnderstanding.asset_id.in_(aids)).delete(**keep)
    session.query(ViralBreakdown).filter(ViralBreakdown.asset_id.in_(aids)).delete(**keep)
    session.query(MediaAsset).filter(MediaAsset.content_id.in_(cids)).delete(**keep)
    session.query(RawPayload).filter(RawPayload.content_id.in_(cids)).delete(**keep)
    session.query(ContentItem).filter(ContentItem.id.in_(cids)).delete(**keep)

    # 订阅 / 采集
    session.query(SyncRun).filter(SyncRun.subscription_id.in_(subs)).delete(**keep)
    session.query(Subscription).filter(Subscription.id.in_(subs)).delete(**keep)
    session.query(CrawlRun).filter(CrawlRun.task_id.in_(tasks)).delete(**keep)
    session.query(CrawlTask).filter(CrawlTask.id.in_(tasks)).delete(**keep)

    # 账号
    session.query(FollowedCreator).filter(FollowedCreator.account_id.in_(accs)).delete(**keep)
    session.query(AccountCredential).filter(AccountCredential.account_id.in_(accs)).delete(**keep)
    session.query(PlatformAccount).filter(PlatformAccount.id.in_(accs)).delete(**keep)

    # 无外键哨兵的行（逐行删，量小）
    for row in session.scalars(select(RawPayload)).all():
        if (row.payload or {}).get("demo"):
            session.delete(row)
    for row in session.scalars(select(CreatorProfile)).all():
        if (row.raw or {}).get("demo"):
            session.delete(row)
    for row in session.scalars(select(TaskLog)).all():
        if (row.detail or {}).get("demo"):
            session.delete(row)
    for row in session.scalars(select(JobQueue)).all():
        if (row.payload or {}).get("demo"):
            session.delete(row)

    session.query(SystemSetting).filter(SystemSetting.key == SENTINEL_KEY).delete(**keep)
    session.flush()


def _cnt(session, model, *conds) -> int:
    stmt = select(func.count()).select_from(model)
    if conds:
        stmt = stmt.where(*conds)
    return int(session.scalar(stmt) or 0)


def _counts(session) -> dict[str, int]:
    d = _demo_sets(session)
    cids = list(d["content_ids"]) or [-1]
    aids = list(d["asset_ids"]) or [-1]
    accs = list(d["account_ids"]) or [-1]
    subs = list(d["sub_ids"]) or [-1]
    tasks = list(d["task_ids"]) or [-1]
    return {
        "platform_account": len(d["account_ids"]),
        "account_credential": _cnt(session, AccountCredential, AccountCredential.account_id.in_(accs)),
        "subscription": len(d["sub_ids"]),
        "sync_run": _cnt(session, SyncRun, SyncRun.subscription_id.in_(subs)),
        "crawl_task": len(d["task_ids"]),
        "crawl_run": _cnt(session, CrawlRun, CrawlRun.task_id.in_(tasks)),
        "content_item": len(d["content_ids"]),
        "content_metric_snapshot": _cnt(
            session, ContentMetricSnapshot, ContentMetricSnapshot.content_id.in_(cids)
        ),
        "creator_profile": _cnt(session, CreatorProfile),
        "followed_creator": _cnt(
            session, FollowedCreator, FollowedCreator.account_id.in_(accs)
        ),
        "raw_payload": _cnt(session, RawPayload),
        "task_log": _cnt(session, TaskLog),
        "media_asset": len(d["asset_ids"]),
        "transcript": _cnt(session, Transcript, Transcript.asset_id.in_(aids)),
        "analysis_result": _cnt(session, AnalysisResult, AnalysisResult.asset_id.in_(aids)),
        "video_understanding": _cnt(
            session, VideoUnderstanding, VideoUnderstanding.asset_id.in_(aids)
        ),
        "viral_breakdown": _cnt(session, ViralBreakdown, ViralBreakdown.asset_id.in_(aids)),
        "comment": _cnt(session, Comment, Comment.content_id.in_(cids)),
        "job_queue": len(d["job_ids"]),
        "system_setting": _cnt(session, SystemSetting, SystemSetting.key == SENTINEL_KEY),
    }


# ---------------------------------------------------------------------------
# 造数
# ---------------------------------------------------------------------------

def _create_accounts(session, now, rng) -> list[PlatformAccount]:
    accounts = []
    for spec in _ACCOUNTS:
        acc = PlatformAccount(
            platform=spec["platform"],
            display_name=f"{PREFIX} {spec['suffix']}",
            external_id=spec["external_id"],
            credential_kind="cookie",
            status=spec["status"],
            status_message=spec["status_message"],
            last_checked_at=now - timedelta(hours=rng.randint(1, 48)),
            expires_at=(
                now + timedelta(days=spec["expires_in_days"])
                if spec["expires_in_days"] is not None
                else None
            ),
        )
        acc.credential = AccountCredential(
            ciphertext=encrypt_credential(spec["credential"]), enc_algo="aes-gcm-v1"
        )
        session.add(acc)
        accounts.append(acc)
    session.flush()
    return accounts


def _create_subscriptions(session, accounts, now, rng) -> list[Subscription]:
    cols = _table_columns(session, "subscription")
    subs = []
    for spec in _SUBSCRIPTIONS:
        acc = accounts[spec["account_index"]]
        kwargs = dict(
            account_id=acc.id,
            platform=acc.platform,
            source_type=spec["source_type"],
            remote_id=spec["remote_id"],
            title=spec["title"],
            enabled=spec["enabled"],
            auto_download=spec["auto_download"],
            interval_minutes=spec["interval_minutes"],
            subdir_template=spec["subdir_template"],
            last_sync_at=now - timedelta(minutes=rng.randint(5, 600)),
        )
        for field in _NEW_SUB_FIELDS:
            if hasattr(Subscription, field) and (not cols or field in cols):
                kwargs[field] = spec[field]
        sub = Subscription(**kwargs)
        session.add(sub)
        subs.append(sub)
    session.flush()

    # 同步历史：成功 + 失败混合
    for i, sub in enumerate(subs):
        for j in range(2):
            ok = (i + j) % 4 != 3
            started = now - timedelta(hours=(i + 1) * 6 + j * 3)
            session.add(
                SyncRun(
                    subscription_id=sub.id,
                    started_at=started,
                    finished_at=started + timedelta(minutes=rng.randint(1, 5)),
                    status="success" if ok else "error",
                    fetched=rng.randint(8, 60) if ok else rng.randint(0, 5),
                    added=rng.randint(2, 18) if ok else 0,
                    error="" if ok else "远端接口返回 403：Cookie 可能已失效（模拟数据）",
                )
            )
    session.flush()
    return subs


def _create_crawl_tasks(session, now, rng) -> list[CrawlTask]:
    tasks = []
    for spec in _CRAWL_TASKS:
        task = CrawlTask(
            platform=spec["platform"],
            keyword=spec["keyword"],
            sort=spec["sort"],
            limit_per_run=spec["limit_per_run"],
            with_comments=spec["with_comments"],
            to_candidate=spec["to_candidate"],
            min_play=spec["min_play"],
            interval_minutes=spec["interval_minutes"],
            enabled=spec["enabled"],
            last_run_at=now - timedelta(hours=rng.randint(1, 40)),
        )
        session.add(task)
        tasks.append(task)
    session.flush()
    for i, task in enumerate(tasks):
        for j in range(2):
            ok = not (i == 2 and j == 1)
            started = now - timedelta(hours=(i + 1) * 8 + j * 2)
            session.add(
                CrawlRun(
                    task_id=task.id,
                    started_at=started,
                    finished_at=started + timedelta(minutes=rng.randint(2, 12)),
                    status="success" if ok else "error",
                    fetched=rng.randint(20, 120) if ok else rng.randint(0, 6),
                    added=rng.randint(3, 30) if ok else 0,
                    error="" if ok else "抓取超时：目标页面无响应（模拟数据）",
                )
            )
    session.flush()
    return tasks


def _create_contents(session, rng, now, subs, tasks) -> list[ContentItem]:
    sub_by_platform: dict[str, Subscription] = {}
    for sub in subs:
        sub_by_platform.setdefault(sub.platform, sub)
    task_by_platform: dict[str, CrawlTask] = {}
    for task in tasks:
        task_by_platform.setdefault(task.platform, task)

    contents: list[ContentItem] = []
    for platform, titles in _CONTENT_TITLES.items():
        authors = _AUTHORS[platform]
        for i, title in enumerate(titles):
            author = authors[i % len(authors)]
            if platform == "xhs":
                content_type = "article" if i < 2 else "image"
            else:
                content_type = "video"
            published = now - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 23))
            duration = (
                0
                if content_type != "video"
                else rng.choice([45, 60, 90, 120, 180, 240, 300, 420, 600])
            )
            use_crawl = i % 3 == 0 and platform in task_by_platform
            source_kind = (
                "crawl" if use_crawl else ("subscription" if platform in sub_by_platform else "crawl")
            )
            if source_kind == "crawl":
                source_id = task_by_platform[platform].id
            else:
                source_id = sub_by_platform[platform].id
            external_id = f"{platform}-demo-{i + 1:03d}"
            content = ContentItem(
                platform=platform,
                external_id=external_id,
                content_type=content_type,
                title=title,
                author_name=author["author_name"],
                author_id=author["author_id"],
                url=f"{DEMO_HOST}/{platform}/{external_id}",
                cover_url=f"{DEMO_HOST}/cover/{platform}_{i + 1:03d}.jpg",
                published_at=published,
                duration_sec=duration,
                auto_category=_category_of(title),
                category_confidence=round(rng.uniform(0.6, 0.95), 2),
                source_kind=source_kind,
                source_id=source_id,
                extra={"demo": True, "seed_index": i},
            )
            session.add(content)
            contents.append(content)
    session.flush()
    return contents


def _create_creators(session, now) -> None:
    for platform, authors in _AUTHORS.items():
        for author in authors:
            session.add(
                CreatorProfile(
                    platform=platform,
                    author_id=author["author_id"],
                    author_name=author["author_name"],
                    avatar=author["avatar"],
                    bio=author["bio"],
                    follower_count=author["followers"],
                    following_count=max(1, author["followers"] // 50),
                    verified=author["verified"],
                    homepage_url=f"{DEMO_HOST}/{platform}/u/{author['author_id']}",
                    last_synced_at=now - timedelta(hours=6),
                    raw={"demo": True},
                )
            )
    session.flush()


def _create_followed_creators(session, accounts, now, rng) -> list[FollowedCreator]:
    """给抖音/B站 demo 账号种入关注博主（卡片页数据源）。

    只挂到 active 的账号；头像/昵称直接用 _AUTHORS 的「真实感」演示值（脚本不联网）。
    """
    out: list[FollowedCreator] = []
    for acc in accounts:
        if acc.platform not in ("douyin", "bilibili"):
            continue
        if acc.status != "active":
            continue
        for idx, author in enumerate(_AUTHORS.get(acc.platform, [])):
            followers = author["followers"]
            out.append(
                FollowedCreator(
                    account_id=acc.id,
                    platform=acc.platform,
                    author_id=author["author_id"],
                    author_name=author["author_name"],
                    avatar=author["avatar"],
                    home_url=(
                        f"https://space.bilibili.com/{author['author_id']}"
                        if acc.platform == "bilibili"
                        else f"https://www.douyin.com/user/{author['author_id']}"
                    ),
                    signature=author["bio"],
                    verified=author["verified"],
                    follower_count=followers,
                    like_count=followers * rng.randint(3, 9),
                    # 第一个博主默认开启同步（演示两种开关状态）
                    sync_enabled=idx == 0,
                    full_sync=idx == 0,
                    last_synced_at=now - timedelta(hours=rng.randint(1, 30)),
                    raw={"demo": True},
                )
            )
    session.add_all(out)
    session.flush()
    # 一致性：sync_enabled=True 的博主应有一条启用的 creator 订阅（与 API 语义一致）。
    existing = {
        (s.account_id, s.remote_id): s
        for s in session.scalars(select(Subscription)).all()
        if s.source_type == "creator"
    }
    for fc in out:
        if not fc.sync_enabled:
            continue
        sub = existing.get((fc.account_id, fc.author_id))
        if sub is None:
            session.add(
                Subscription(
                    account_id=fc.account_id,
                    platform=fc.platform,
                    source_type="creator",
                    remote_id=fc.author_id,
                    title=f"{PREFIX} {fc.platform}·关注 {fc.author_name}",
                    enabled=True,
                    only_new=not fc.full_sync,
                )
            )
        else:
            sub.enabled = True
            sub.only_new = not fc.full_sync
    session.flush()
    return out


def _create_metrics(session, rng, now, contents) -> None:
    for content in contents:
        lo, hi = _PLAY_BASE[content.platform]
        base = rng.randint(lo, hi)
        points = rng.randint(5, 7)
        for j in range(points):
            ratio = (j + 1) / points
            play = int(base * ratio * rng.uniform(0.92, 1.08))
            like = int(play * rng.uniform(0.03, 0.09))
            comment = int(play * rng.uniform(0.002, 0.01))
            favorite = int(play * rng.uniform(0.01, 0.04))
            share = int(play * rng.uniform(0.003, 0.02))
            captured = now - timedelta(days=(points - 1 - j) * 2, hours=rng.randint(0, 12))
            session.add(
                ContentMetricSnapshot(
                    content_id=content.id,
                    captured_at=captured,
                    play=play,
                    like=like,
                    comment=comment,
                    favorite=favorite,
                    share=share,
                )
            )
    session.flush()


def _create_assets(session, rng, now, contents) -> list[MediaAsset]:
    videos = [c for c in contents if c.content_type == "video"][:25]
    download_cycle = ["done", "done", "done", "done", "done", "done", "downloading", "pending", "error", "done"]
    assets: list[MediaAsset] = []
    for idx, content in enumerate(videos):
        download_status = download_cycle[idx % len(download_cycle)]
        # 分档：advanced 走视频理解，basic 走转录+分析，其余未分档。
        if idx % 4 == 0:
            process_tier = "advanced"
        elif idx % 4 == 1:
            process_tier = "basic"
        else:
            process_tier = ""

        if download_status != "done":
            transcribe_status = "pending"
            analyze_status = "pending"
            understand_status = "pending" if process_tier == "advanced" else ""
        elif process_tier == "advanced":
            # 高级档：完整走通下载 → 转录 → 分析 → 视频理解
            transcribe_status = "done"
            analyze_status = "done"
            understand_status = "done"
        elif process_tier == "basic":
            transcribe_status = "done"
            analyze_status = ["done", "pending"][(idx // 4) % 2]
            understand_status = ""
        else:
            transcribe_status = ["done", "done", "pending", "error"][idx % 4]
            analyze_status = "pending"
            understand_status = ""
        relative_path = f"demo/{content.platform}/{content.author_id}/{content.external_id}"
        asset = MediaAsset(
            content_id=content.id,
            storage_root_id=None,
            relative_path=relative_path,
            video_file=f"{content.external_id}.mp4" if download_status == "done" else "",
            audio_file=f"{content.external_id}.m4a" if transcribe_status == "done" else "",
            download_status=download_status,
            transcribe_status=transcribe_status,
            analyze_status=analyze_status,
            process_tier=process_tier,
            understand_status=understand_status,
            error="下载失败：连接超时（模拟数据）" if download_status == "error" else "",
        )
        session.add(asset)
        assets.append(asset)
    session.flush()
    return assets


def _segments(rng, title: str) -> list[dict]:
    lines = [
        f"大家好，今天聊聊{title}。",
        "先说结论，这个方法亲测有效。",
        "第一步，准备好基础材料。",
        "第二步，注意火候和节奏。",
        "第三步，收尾的时候别着急。",
        "如果觉得有用，记得点赞收藏。",
    ]
    segs = []
    t = 0.0
    for line in lines:
        dur = round(rng.uniform(3.5, 8.0), 1)
        segs.append({"start": round(t, 1), "end": round(t + dur, 1), "text": line})
        t += dur
    return segs


def _create_processing(session, rng, now, assets, contents) -> None:
    content_by_id = {c.id: c for c in contents}
    for asset in assets:
        content = content_by_id[asset.content_id]
        if asset.transcribe_status == "done":
            segs = _segments(rng, content.title)
            session.add(
                Transcript(
                    asset_id=asset.id,
                    language="zh",
                    model="faster-whisper/base",
                    segments=segs,
                    full_text="".join(s["text"] for s in segs),
                )
            )
        if asset.analyze_status == "done":
            session.add(
                AnalysisResult(
                    asset_id=asset.id,
                    summary=(
                        f"《{content.title}》内容结构清晰，前3秒给出强钩子，"
                        "中段用具体步骤推进，结尾引导互动。"
                    ),
                    tags=[content.auto_category, "短视频", "教程"],
                    structured={
                        "hook": "开门见山抛出结论",
                        "key_points": ["准备材料", "关键步骤", "收尾技巧"],
                        "cta": "点赞收藏，下期见",
                        "audience": "对短视频创作感兴趣的普通用户",
                    },
                    model="gpt-4o-mini",
                    prompt_version="v1",
                )
            )
        if asset.process_tier == "advanced" and asset.understand_status == "done":
            session.add(
                VideoUnderstanding(
                    asset_id=asset.id,
                    category=content.auto_category,
                    category_confidence=round(rng.uniform(0.7, 0.96), 2),
                    category_reason=f"标题与画面均指向「{content.auto_category}」主题",
                    low_confidence=False,
                    dimensions={
                        "拍摄手法": "手持 + 特写",
                        "节奏": "快剪，平均 2 秒一切",
                        "情绪": "轻松、亲切",
                        "场景": "室内",
                    },
                    summary=f"视频围绕「{content.title}」展开，信息密度高，适合新手跟做。",
                    timeline=[
                        {"start": 0, "end": 3, "label": "强钩子"},
                        {"start": 3, "end": 20, "label": "步骤拆解"},
                        {"start": 20, "end": 40, "label": "成品展示"},
                        {"start": 40, "end": 50, "label": "引导互动"},
                    ],
                    model="gpt-4o-mini",
                    prompt_version="v1",
                    usage={"video_tokens": rng.randint(8000, 40000)},
                    status="done",
                )
            )
    session.flush()

    # 爆款拆解：挑最新播放最高的 3 条已下载视频
    done_assets = [a for a in assets if a.download_status == "done"]
    ranked = sorted(
        done_assets,
        key=lambda a: (
            session.scalar(
                select(func.max(ContentMetricSnapshot.play)).where(
                    ContentMetricSnapshot.content_id == a.content_id
                )
            )
            or 0
        ),
        reverse=True,
    )[:3]
    for asset in ranked:
        content = content_by_id[asset.content_id]
        session.add(
            ViralBreakdown(
                asset_id=asset.id,
                score=round(rng.uniform(72, 94), 1),
                verdict=f"「{content.title}」靠强钩子 + 明确步骤 + 情绪价值完成传播。",
                sections={
                    "hook": "前3秒用结果/反差抓注意力",
                    "structure": "结论先行 → 步骤拆解 → 成品展示 → 引导互动",
                    "rhythm": "快剪，信息不拖沓",
                    "copywriting": "口语化、有具体数字",
                    "trigger": "实用价值 + 情绪共鸣",
                    "cta": "点赞收藏",
                    "replicable": "可复用：同题材换品类，保留钩子结构",
                },
                comment_insights={
                    "themes": ["求教程", "求平替", "晒成果"],
                    "controversy": "部分评论质疑可复制性",
                    "questions": ["食材在哪买？", "有没有更省钱的方案？"],
                },
                data_context={"play": 0, "like": 0, "comment": 0, "viral_multiple": 0},
                understanding_available=asset.understand_status == "done",
                model="gpt-4o-mini",
                prompt_version="v1",
                usage={"total_tokens": rng.randint(1500, 6000)},
                status="done",
            )
        )
    session.flush()


def _create_comments(session, rng, now, contents) -> None:
    targets = [c for c in contents if c.platform == "douyin"][:6]
    for content in targets:
        for k in range(rng.randint(3, 6)):
            comment_id = f"demo_cmt_{content.external_id}_{k + 1}"
            session.add(
                Comment(
                    content_id=content.id,
                    platform=content.platform,
                    comment_id=comment_id,
                    parent_comment_id="0",
                    user_id=f"demo_user_{rng.randint(1000, 9999)}",
                    nickname=rng.choice(_NICKNAMES),
                    avatar=f"{DEMO_HOST}/avatar/commenter_{rng.randint(1, 20)}.jpg",
                    content=rng.choice(_COMMENT_TEXTS),
                    like_count=rng.randint(3, 4200),
                    ip_location=rng.choice(_IP_LOCATIONS),
                    created_at=now - timedelta(hours=rng.randint(1, 300)),
                )
            )
    session.flush()


def _create_jobs_logs(session, rng, now, contents, assets) -> None:
    # 原始数据留存
    for content in contents[:10]:
        session.add(
            RawPayload(
                platform=content.platform,
                source="crawl" if content.source_kind == "crawl" else "subscription",
                external_id=content.external_id,
                payload={"demo": True, "title": content.title, "author": content.author_name},
                content_id=content.id,
                captured_at=content.published_at or now,
            )
        )
    session.flush()

    # 队列：queued / running / done / error 混合
    job_specs = [
        ("download", "queued", {"asset_hint": "pending"}),
        ("download", "running", {"content_id": contents[0].id}),
        ("transcribe", "queued", {"asset_id": assets[0].id}),
        ("transcribe", "done", {"asset_id": assets[1].id}),
        ("analyze", "queued", {"asset_id": assets[2].id}),
        ("analyze", "done", {"asset_id": assets[3].id}),
        ("sync", "done", {"subscription_hint": "douyin"}),
        ("crawl", "running", {"task_hint": "douyin"}),
        ("download", "error", {"content_id": contents[4].id}),
        ("analyze", "error", {"asset_id": assets[5].id}),
        ("download", "done", {"content_id": contents[6].id}),
        ("crawl", "queued", {"task_hint": "xhs"}),
    ]
    jobs = []
    for i, (job_type, status, payload) in enumerate(job_specs):
        job = JobQueue(
            job_type=job_type,
            payload={"demo": True, **payload},
            status=status,
            priority=100 if job_type in ("download", "transcribe", "analyze") else 120,
            attempts=1 if status in ("running", "error") else 0,
            max_attempts=3,
            error="处理失败：模型服务超时（模拟数据）" if status == "error" else "",
            locked_at=now - timedelta(minutes=5) if status == "running" else None,
            run_after=now - timedelta(minutes=rng.randint(1, 60)),
        )
        session.add(job)
        jobs.append(job)
    session.flush()

    # 结构化任务日志
    log_specs = [
        ("download", "info", "开始下载视频", jobs[1].id),
        ("download", "info", "下载完成，写入素材库", jobs[10].id),
        ("download", "error", "下载失败：连接超时", jobs[8].id),
        ("transcribe", "info", "转录完成，生成 6 段", jobs[3].id),
        ("analyze", "info", "分析完成：摘要 + 标签", jobs[5].id),
        ("analyze", "error", "分析失败：模型返回空", jobs[9].id),
        ("sync", "info", "订阅同步完成，新增 12 条", jobs[6].id),
        ("crawl", "info", "关键词采集进行中", jobs[7].id),
        ("crawl", "warn", "部分页面结构变化，已降级解析", None),
        ("sync", "error", "同步失败：Cookie 已失效", None),
    ]
    for i, (job_type, level, message, job_id) in enumerate(log_specs):
        session.add(
            TaskLog(
                job_id=job_id,
                job_type=job_type,
                level=level,
                message=message,
                detail={"demo": True, "seq": i},
                actor="worker" if job_type != "sync" else "scheduler",
                created_at=now - timedelta(minutes=(len(log_specs) - i) * 7),
            )
        )
    session.flush()


def _create(session) -> None:
    rng = random.Random(RNG_SEED)
    now = datetime.utcnow()
    accounts = _create_accounts(session, now, rng)
    subs = _create_subscriptions(session, accounts, now, rng)
    tasks = _create_crawl_tasks(session, now, rng)
    contents = _create_contents(session, rng, now, subs, tasks)
    _create_creators(session, now)
    _create_followed_creators(session, accounts, now, rng)
    _create_metrics(session, rng, now, contents)
    assets = _create_assets(session, rng, now, contents)
    _create_processing(session, rng, now, assets, contents)
    _create_comments(session, rng, now, contents)
    _create_jobs_logs(session, rng, now, contents, assets)
    session.add(SystemSetting(key=SENTINEL_KEY, value=SENTINEL_VALUE))
    session.flush()


def seed(session, reset: bool = False) -> dict[str, int]:
    """幂等种入开发模拟数据，返回各表 demo 行计数。

    - 已存在哨兵且 reset=False → 直接返回现有计数（不重复造数）。
    - reset=True → 先删 demo 行（保留真实数据）再重建。
    - 无哨兵（首次或上次异常中断）→ 先清残留 demo 行再重建。
    """
    sentinel = session.get(SystemSetting, SENTINEL_KEY)
    if reset or sentinel is None:
        _purge(session)
        session.expunge_all()
        session.flush()
        _create(session)
        session.commit()
    return _counts(session)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="种入开发模拟数据（幂等，绝不联网）。")
    parser.add_argument("--reset", action="store_true", help="先删掉 demo 行再重建（真实数据不动）")
    args = parser.parse_args(argv)

    from app.db import SessionLocal, init_db

    init_db()
    session = SessionLocal()
    try:
        counts = seed(session, reset=args.reset)
    finally:
        session.close()

    print("开发模拟数据已就绪（demo）：")
    for key in sorted(counts):
        print(f"  {key:24s} {counts[key]}")
    print(f"  {'哨兵':22s} {SENTINEL_KEY}={SENTINEL_VALUE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
