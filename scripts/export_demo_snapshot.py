#!/usr/bin/env python
"""导出静态演示站快照。

流程：
1. 指向一个独立的演示 SQLite（`--db`），建表并重置演示数据；
2. 用应用内 TestClient 直接调用只读 GET 接口，避免启动端口；
3. 把响应写成 `web/public/demo-data/snapshot.json`；
4. 复制演示视频与封面到 `web/public/demo-data/media/`。

不联网、不触碰真实库：默认数据库路径在 `/tmp` 下，可显式覆盖。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB = "/tmp/platform-demo-snapshot.db"


def _collect_paths(client) -> dict[str, object]:
    """按前端实际调用的路径抓取快照。"""
    snapshot: dict[str, object] = {}

    def put(path: str):
        resp = client.get(path)
        if resp.status_code == 200:
            # 快照键不带 query；前端按无参路径取数，具体查询条件由前端本地过滤。
            clean = path.split('?')[0]
            key = clean.replace('/api', '') if clean.startswith('/api') else clean
            snapshot[key] = resp.json()

    # 账号 / 平台
    put('/api/platforms')
    put('/api/accounts')
    # 总览
    put('/api/dashboard/summary')
    put('/api/analytics/creators?limit=8')
    put('/api/analytics/keywords')
    # 采集 / 关注
    put('/api/crawl-tasks')
    put('/api/subscriptions')
    put('/api/followed-creators')
    # 素材库
    put('/api/assets')
    put('/api/system/process-settings')
    # 处理队列
    put('/api/pipeline/jobs')
    put('/api/pipeline/summary')
    # 模型接入
    put('/api/model-access/providers')
    put('/api/model-access/models')
    put('/api/model-access/assignments')
    put('/api/model-access/calls')
    # AI 接入
    put('/api/ai/keys')
    put('/api/ai/call-logs')
    # 系统设置（只读展示）
    put('/api/system/storage-roots')
    put('/api/system/settings')
    put('/api/system/comment-settings')
    put('/api/system/breakdown-settings')
    put('/api/system/diagnostics')
    return snapshot


def _collect_runs_and_switches(client, snapshot: dict) -> None:
    """运行记录与账号开关：前端进入详情/设置面板时会读取。"""
    for task in snapshot.get('/crawl-tasks') or []:
        task_id = task.get('id')
        if task_id is not None:
            resp = client.get(f'/api/crawl-tasks/{task_id}/runs')
            if resp.status_code == 200:
                bucket = snapshot.setdefault('/crawl-tasks/{task_id}/runs', {})
                if isinstance(bucket, dict):
                    bucket[str(task_id)] = resp.json()

    for sub in snapshot.get('/subscriptions') or []:
        sub_id = sub.get('id')
        if sub_id is not None:
            resp = client.get(f'/api/subscriptions/{sub_id}/runs')
            if resp.status_code == 200:
                bucket = snapshot.setdefault('/subscriptions/{sub_id}/runs', {})
                if isinstance(bucket, dict):
                    bucket[str(sub_id)] = resp.json()

    for acc in snapshot.get('/accounts') or []:
        acc_id = acc.get('id')
        if acc_id is None:
            continue
        resp = client.get(f'/api/accounts/{acc_id}/sync-switches')
        if resp.status_code == 200:
            bucket = snapshot.setdefault('/accounts/{account_id}/sync-switches', {})
            if isinstance(bucket, dict):
                bucket[str(acc_id)] = resp.json()
        resp = client.get(f'/api/accounts/{acc_id}/remote-sources?page=1&page_size=20')
        if resp.status_code == 200:
            bucket = snapshot.setdefault('/accounts/{account_id}/remote-sources', {})
            if isinstance(bucket, dict):
                bucket[str(acc_id)] = resp.json()

    # 调用审计详情：前端按 attempt_group_id 展开单次调用链路。
    groups = sorted(
        {
            row.get('attempt_group_id')
            for row in snapshot.get('/model-access/calls') or []
            if row.get('attempt_group_id')
        }
    )
    for group in groups:
        resp = client.get(f'/api/model-access/calls/{group}')
        if resp.status_code == 200:
            bucket = snapshot.setdefault('/model-access/calls/{group_id}', {})
            if isinstance(bucket, dict):
                bucket[str(group)] = resp.json()


def _collect_details(client, snapshot: dict) -> None:
    """素材详情、转录、理解、评论、拆解与文章正文。"""
    assets = snapshot.get('/assets') or []
    for row in assets:
        asset_id = row.get('asset_id')
        if asset_id is None:
            continue
        for suffix, key in (
            ('', f'/assets/{{asset_id}}'),
            ('/article', '/assets/{asset_id}/article'),
            ('/understanding', '/assets/{asset_id}/understanding'),
            ('/breakdown', '/assets/{asset_id}/breakdown'),
            ('/comments?limit=50&offset=0', '/assets/{asset_id}/comments'),
        ):
            resp = client.get(f'/api/assets/{asset_id}{suffix}')
            if resp.status_code != 200:
                continue
            payload = resp.json()
            bucket = snapshot.setdefault(key, {})
            if isinstance(bucket, dict):
                bucket[str(asset_id)] = payload


def _copy_media(db_path: str, out_dir: Path) -> None:
    """复制演示视频到静态目录，供 <video> 直接引用。"""
    import sqlite3

    from app.config import settings

    media_dir = out_dir / 'media'
    media_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, relative_path, video_file FROM media_asset "
            "WHERE video_file != '' AND download_status = 'done'"
        ).fetchall()
    finally:
        conn.close()
    root = Path(settings.data_dir) / 'demo_storage'
    for asset_id, relative_path, video_file in rows:
        src = root / relative_path / video_file
        if src.is_file():
            shutil.copy2(src, media_dir / f'{asset_id}.mp4')


def _copy_covers(db_path: str, out_dir: Path) -> None:
    """复制演示封面到静态目录；没有缓存的素材生成一张占位封面。"""
    import sqlite3

    from app.config import settings

    media_dir = out_dir / 'media'
    media_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(settings.data_dir) / 'cover_cache'
    conn = sqlite3.connect(db_path)
    try:
        asset_ids = [row[0] for row in conn.execute('SELECT id FROM media_asset').fetchall()]
    finally:
        conn.close()
    for asset_id in asset_ids:
        target = media_dir / f'{asset_id}.png'
        if target.exists() and target.stat().st_size > 3000:
            continue
        cached = cache_dir / f'{asset_id}.bin'
        if cached.is_file() and cached.stat().st_size > 3000:
            shutil.copy2(cached, target)
            continue
        # 无封面时生成纯色占位 PNG，避免演示站出现破图。
        target.write_bytes(_placeholder_png())


def _placeholder_png() -> bytes:
    import struct
    import zlib

    width = height = 2
    raw = b''.join(b'\x00' + bytes((214, 205, 182)) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack('>I', len(data))
            + tag
            + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw))
        + chunk(b'IEND', b'')
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--out', default=str(ROOT / 'web' / 'public' / 'demo-data'))
    parser.add_argument('--reset', action='store_true', default=True)
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    os.environ['PLATFORM_DATABASE_URL'] = f'sqlite:///{db_path}'
    os.environ.setdefault('PLATFORM_CREDENTIAL_ENC_KEY', base64.b64encode(os.urandom(32)).decode())
    # 演示快照不放"假内容"：关掉 seed_demo 的假内容段，素材/文章全部来自真实抓取
    # （scripts/seed_demo_real.inject 从 real_pack 注入）。
    os.environ['PLATFORM_DEMO_FAKE_CONTENT'] = '0'

    from app.db import Base, SessionLocal, engine, init_db
    from app.main import app
    from fastapi.testclient import TestClient
    from scripts.seed_demo import seed

    init_db()
    session = SessionLocal()
    real_stats = {}
    try:
        seed(session, reset=True)
        # 真实内容注入（视频/文章的转录、分析、拆解）；缺数据包时静默跳过
        from scripts.seed_demo_real import inject as inject_real

        real_stats = inject_real(session)
        session.commit()
        # 管理员会话：直接签发 token，TestClient 带 Authorization 头。
        from app.core import security

        token = security.issue_token(session)
    finally:
        session.close()
    client = TestClient(app)
    client.headers.update({'Authorization': f'Bearer {token}'})

    snapshot = _collect_paths(client)
    _collect_details(client, snapshot)
    _collect_runs_and_switches(client, snapshot)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'snapshot.json').write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), 'utf-8'
    )
    _copy_media(str(db_path), out_dir)
    _copy_covers(str(db_path), out_dir)
    print(f'快照已写入 {out_dir / "snapshot.json"}')
    print(f'路径数：{len(snapshot)}；素材数：{len(snapshot.get("/assets") or [])}')
    if real_stats:
        print('真实内容注入：' + '；'.join(f'{k}={v}' for k, v in real_stats.items() if v))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
