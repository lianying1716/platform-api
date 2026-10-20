"""updater 边车 FastAPI：升级编排入口。

端点：
- GET  /health           存活探测
- POST /upgrade          触发升级（需密钥）→ 立即返回 accepted，后台线程执行
- GET  /upgrade/status    轮询进度（只读，无需密钥）

为什么后台执行 + 状态放这里：
升级会重建 platform-api 自身，主 API 进程随之退出。状态必须存活在不会重启的边车里，
前端才能在升级过程中持续轮询进度、看到"备份→拉取→重建→校验"的每一步。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.updater.engine import UpgradeBusy, UpgradeEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("platform.updater")

app = FastAPI(title="platform-updater", version="2.0.0")

# 只允许重建这两个服务；绝不含 db / updater 自己。
_UPGRADABLE_SERVICES = ["platform-api", "platform-worker"]
_COMPOSE_FILE = os.environ.get("UPDATER_COMPOSE_FILE", "/compose/docker-compose.yml")
_DATA_DIR = os.environ.get("UPDATER_DATA_DIR", "/data")
_BACKUP_DIR = os.environ.get("UPDATER_BACKUP_DIR", "/backups")
_ENV_FILE = os.environ.get("UPDATER_ENV_FILE", "/compose/.env")
_API_HEALTH_URL = os.environ.get("UPDATER_API_HEALTH_URL", "http://platform-api:9100/api/system/version")
_POSTGRES_SERVICE = os.environ.get("UPDATER_DB_SERVICE", "db")


def _compose(*args: str, env: dict | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", _COMPOSE_FILE, *args],
        capture_output=True, text=True, timeout=timeout, env=env or os.environ.copy(), check=False,
    )


def _present_services() -> list[str]:
    """返回 compose 里实际定义的可升级服务（容忍某个服务缺失，避免整体失败）。"""
    r = _compose("config", "--services")
    if r.returncode != 0:
        return list(_UPGRADABLE_SERVICES)
    defined = set(r.stdout.split())
    return [s for s in _UPGRADABLE_SERVICES if s in defined]


# ---------------- 真实依赖实现（注入给引擎） ----------------

def _do_backup(version: str) -> dict:
    """强制备份：pg_dump + data 卷 + manifest，产出带时间戳的 tar.gz。

    与 scripts/backup.sh 同契约：备份失败即返回 ok=False，引擎据此中止升级。
    """
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    work = os.path.join(_BACKUP_DIR, f".work-{ts}")
    os.makedirs(work, exist_ok=True)
    try:
        # 1) 数据库
        dump_path = os.path.join(work, "db.dump")
        # 注意：-Fc 是二进制格式，必须用二进制管道写文件，不能经 text 解码（否则乱码/报错）。
        with open(dump_path, "wb") as fh:
            dump = subprocess.run(
                [
                    "docker", "compose", "-f", _COMPOSE_FILE, "exec", "-T", _POSTGRES_SERVICE,
                    "pg_dump", "-U", os.environ.get("POSTGRES_USER", "platform"),
                    "-d", os.environ.get("POSTGRES_DB", "platform"), "-Fc",
                ],
                stdout=fh, stderr=subprocess.PIPE, timeout=600, check=False,
            )
        if dump.returncode != 0 or not os.path.getsize(dump_path):
            err = (dump.stderr or b"")[-300:].decode("utf-8", "replace")
            return {"ok": False, "error": f"pg_dump 失败：{err}"}

        # 2) data 目录（含 secret.key）。data 卷以只读方式挂进本容器，直接 tar 即可，
        #    不依赖宿主卷名推导（worktree/改名都不会出错）。
        data_tar = os.path.join(work, "data.tar.gz")
        if not os.path.isdir(_DATA_DIR):
            return {"ok": False, "error": f"data 目录不存在（{_DATA_DIR}），拒绝无备份升级"}
        vol = subprocess.run(
            ["tar", "czf", data_tar, "-C", _DATA_DIR, "."],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if vol.returncode != 0 or not os.path.exists(data_tar) or os.path.getsize(data_tar) == 0:
            return {"ok": False, "error": "data 目录备份失败（secret.key 兜底），已中止"}

        # 3) manifest + 打包
        import json

        with open(os.path.join(work, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": version, "created_at": ts, "includes": ["db.dump", "data.tar.gz"]}, fh)

        pkg = os.path.join(_BACKUP_DIR, f"backup-{version}-{ts}.tar.gz")
        tar = subprocess.run(
            ["tar", "czf", pkg, "-C", work, "."], capture_output=True, text=True, timeout=300, check=False
        )
        if tar.returncode != 0 or not os.path.exists(pkg):
            return {"ok": False, "error": f"打包备份失败：{tar.stderr[-200:]}"}
        return {"ok": True, "path": pkg}
    finally:
        subprocess.run(["rm", "-rf", work], check=False)


def _do_pull(version: str) -> dict:
    env = dict(os.environ, PLATFORM_VERSION=version)
    services = _present_services()
    if not services:
        return {"ok": False, "error": "未在 compose 中找到可升级服务"}
    r = _compose("pull", *services, env=env)
    return {"ok": r.returncode == 0, "error": r.stderr[-300:]}


def _do_recreate(version: str) -> dict:
    env = dict(os.environ, PLATFORM_VERSION=version)
    services = _present_services()
    if not services:
        return {"ok": False, "error": "未在 compose 中找到可升级服务"}
    r = _compose("up", "-d", "--no-deps", *services, env=env)
    return {"ok": r.returncode == 0, "error": r.stderr[-300:]}


def _do_health(version: str) -> dict:
    """Probe the API's structured version endpoint.

    A bare HTTP 200 is not enough: the container tag may not have switched, or
    migrations may still be pending.  Return evidence so the engine can decide.
    """
    import urllib.request
    import json

    try:
        with urllib.request.urlopen(_API_HEALTH_URL, timeout=5) as resp:
            if resp.status != 200:
                return {"reachable": False, "status": resp.status}
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return {
                "reachable": True,
                "status": 200,
                "app_version": str(body.get("app_version") or ""),
                "db_in_sync": body.get("db_in_sync") is True,
                "db_revision": body.get("db_revision") or "",
                "db_head": body.get("db_head") or "",
                "target_version": version,
            }
    except Exception:  # noqa: BLE001 探测失败即视为不健康，交由引擎回滚
        return {"reachable": False, "error": "version endpoint unreachable"}


def _do_pin(version: str) -> dict:
    """把目标版本写回 compose 同目录的 .env，作为"当前版本"的持久事实。

    没有这一步，升级后任何一次 `docker compose up -d` 或宿主机重启都会按默认
    tag 重建，静默切回旧版本——网页升级等于白做。
    """
    from app.updater.versionstate import write_pinned_version

    try:
        write_pinned_version(_ENV_FILE, version)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 写盘失败要冒泡成升级失败（引擎会回滚）
        return {"ok": False, "error": str(exc)}


def _do_rollback(version: str) -> dict:
    """回滚：把镜像 tag 切回旧版本并重建。version 为旧版本号。

    回滚**不把 pull 当硬前置**：旧镜像此刻通常还在本机（升级只是换了 tag），直接
    `up -d` 就能回来；若恰好不在再尝试拉取。否则网络一抖（实测 ghcr TLS handshake
    timeout）就会让"已经能回滚"变成"回滚也失败"，把可自愈的升级拖成需要人工恢复。
    """
    if not version:
        return {"ok": False, "error": "无可用旧版本号，需手动恢复备份"}
    env = dict(os.environ, PLATFORM_VERSION=version)
    services = _present_services() or list(_UPGRADABLE_SERVICES)
    up = _compose("up", "-d", "--no-deps", *services, env=env)
    if up.returncode != 0:
        # 本地没有旧镜像时才需要拉取；拉取失败再如实报错。
        pull = _compose("pull", *services, env=env)
        if pull.returncode != 0:
            return {"ok": False, "error": f"回滚失败：up 失败({up.stderr[-150:]})，拉取也失败({pull.stderr[-150:]})"}
        up = _compose("up", "-d", "--no-deps", *services, env=env)
    return {"ok": up.returncode == 0, "error": up.stderr[-200:]}


def _do_restore(backup_path: str) -> dict:
    """Restore the DB dump from a pre-upgrade backup.

    The manifest is the contract: only packages produced by ``_do_backup`` or
    ``scripts/backup.sh`` are accepted.  We restore the DB before switching the
    image tag back, because an old image must not start against a newer schema.
    """
    import json
    import tarfile

    if not backup_path or not os.path.isfile(backup_path):
        return {"ok": False, "error": f"备份不存在：{backup_path}"}
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            if "manifest.json" not in members or "db.dump" not in members:
                return {"ok": False, "error": "备份缺少 manifest.json 或 db.dump"}
            manifest = json.load(tar.extractfile(members["manifest.json"]))  # type: ignore[arg-type]
            if "db.dump" not in manifest.get("includes", []):
                return {"ok": False, "error": "manifest 未声明 db.dump，拒绝恢复"}
            dump = tar.extractfile(members["db.dump"])
            if dump is None:
                return {"ok": False, "error": "无法读取 db.dump"}
            payload = dump.read()
    except (OSError, tarfile.TarError, ValueError) as exc:
        return {"ok": False, "error": f"读取备份失败：{exc}"}
    if not payload:
        return {"ok": False, "error": "db.dump 为空"}

    # pg_restore --clean --if-exists makes the restore idempotent enough to
    # retry; it runs inside the same db service used for backups.
    restore = subprocess.run(
        [
            "docker", "compose", "-f", _COMPOSE_FILE, "exec", "-T", _POSTGRES_SERVICE,
            "pg_restore", "-U", os.environ.get("POSTGRES_USER", "platform"),
            "-d", os.environ.get("POSTGRES_DB", "platform"), "--clean", "--if-exists",
            "--no-owner", "--no-privileges",
        ],
        input=payload,
        capture_output=True,
        timeout=600,
        check=False,
    )
    if restore.returncode != 0:
        err = (restore.stderr or b"")[-300:].decode("utf-8", "replace")
        return {"ok": False, "error": f"pg_restore 失败：{err}"}
    return {"ok": True}


_engine = UpgradeEngine(
    backup=_do_backup, pull=_do_pull, recreate=_do_recreate,
    health=_do_health, rollback=_do_rollback, pin=_do_pin,
    restore=_do_restore,
)


def _current_version() -> str:
    try:
        from app.core.version import load_build_info

        return load_build_info().version
    except Exception:  # noqa: BLE001
        return ""


class UpgradeIn(BaseModel):
    version: str
    # The updater cannot infer whether a future release has irreversible
    # migrations; the release manifest must state it.  Defaulting to True keeps
    # old clients working, but callers should pass False when unsure.
    reversible: bool = True


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/upgrade/status")
def upgrade_status():
    """轮询进度（只读，无需密钥）——主 API 重启期间前端靠它跟踪。"""
    job = _engine.latest()
    return {"busy": _engine.is_busy(), "job": job.to_dict() if job else None}


@app.post("/upgrade")
def upgrade(body: UpgradeIn, x_updater_token: str = Header(default="")):
    import secrets

    from app.core.version_upgrade import is_valid_version

    if not settings.updater_token:
        raise HTTPException(status_code=409, detail="updater 未启用（未配置 UPDATER_TOKEN）")
    if not secrets.compare_digest(x_updater_token, settings.updater_token):
        raise HTTPException(status_code=401, detail="updater 鉴权失败")
    if not is_valid_version(body.version):
        raise HTTPException(status_code=422, detail=f"非法版本号：{body.version!r}")
    if _engine.is_busy():
        raise HTTPException(status_code=409, detail="已有升级在进行中，请稍候")

    from_version = _current_version()
    threading.Thread(
        target=_engine.start,
        args=(body.version,),
        kwargs={"from_version": from_version, "reversible": body.reversible},
        daemon=True,
    ).start()
    return {"accepted": True, "version": body.version, "from_version": from_version}
