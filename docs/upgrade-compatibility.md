# 版本兼容矩阵与升级指南

> 适用：`platform-api` 自托管（Docker Compose 或本地源码）。
> 设计依据：`../../docs/plans/2026-10-11-versioning-and-upgrade-design.md`。
> **升级前请先读本文第一屏。**

## 一句话

本项目的数据库迁移**只前进、不自动回退**。所谓"回滚"指的是**恢复升级前的备份**，
不是执行 Alembic downgrade（降级迁移会丢数据——0011/0012/0015/0019 含不可逆的数据合并与删列）。

## 版本规则

- 版本格式 `vMAJOR.MINOR.PATCH`，语义化版本。
- **patch 内**（v1.2.x → v1.2.y）：库结构不保证变化；若未改结构可直接换 tag 回退。
- **跨 minor / major**：库结构会变化；升级后**旧版本无法再读该库**，回退只能靠备份。
- breaking change 只允许出现在 **major**。

## 兼容矩阵

| 目标版本 | 最低可直升 | 跳板要求 | 改结构 | 可否原地回退 |
|---|---|---|---|---|
| v1.0.0 | —（首个正式版） | 无 | — | — |
| v1.x.y | v1.0.0 | 无 | 视版本 | patch 内可，跨 minor 否 |

> 新版本发布时在此追加一行；未列出的版本按"不确定 ⇒ 先备份"处理。

## 升级（Docker Compose）

```bash
# 0) 强制备份（脚本会校验产物非空；失败即中止，不会继续升级）
./scripts/backup.sh

# 1) 拉新镜像并升级（api 先迁移，worker 等 api 健康）
./scripts/upgrade.sh v1.3.0
```

或者手动等价操作：

```bash
PLATFORM_VERSION=v1.3.0 docker compose pull
PLATFORM_VERSION=v1.3.0 docker compose up -d
curl -s localhost:9100/api/system/version   # 确认版本与 db_revision
```

## 升级（本地源码）

```bash
MODE=local ./scripts/upgrade.sh v1.3.0
# 内部步骤：备份 → git checkout tag → uv sync → alembic upgrade head
# 之后重启 systemd：systemctl --user restart platform-api-dev platform-web-dev
```

## 回滚

```bash
# 方式一：patch 内且未改结构 —— 直接换回旧 tag
PLATFORM_VERSION=v1.2.0 docker compose up -d

# 方式二：改了结构 —— 恢复升级前的备份（唯一可靠方式）
./scripts/restore.sh backups/backup-v1.2.0-<时间戳>.tar.gz
```

## 升级失败时会发生什么（重要）

升级系统按下述契约设计，**不会出现"迁移做了一半、服务带着坏数据继续跑"**：

1. **原子性**：整批迁移在单个事务内；任一脚本失败 → 整体回滚，数据库停在升级前 revision。
2. **排他性**：迁移由 api 单容器执行，worker 等待；即使配置失误并发，PG advisory lock 也会串行化。
3. **门禁性**：迁移失败或版本对不上 → 容器**拒绝对外服务**（fail fast），不会带病运行。
4. **可恢复性**：升级前已强制备份，可用 `restore.sh` 回到升级前。

因此**升级失败的结果是"服务暂时不可用 + 数据完好"**，而不是"数据损坏 + 静默运行"。

## 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 容器反复重启，日志 `版本门禁未通过` | 代码与库版本不匹配 | 用匹配版本的镜像；或临时 `PLATFORM_ALLOW_UNKNOWN_DB=1` 仅排障 |
| 日志 `等待迁移锁超时` | 另一进程正在迁移 | 等其完成；持续失败则检查是否有卡死的迁移进程 |
| 升级后 `/api/system/version` 的 `db_in_sync=false` | 迁移未跑完或失败 | 查看 `docker compose logs platform-api`；必要时 `restore.sh` |
