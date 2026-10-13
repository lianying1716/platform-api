# 升级模块真机验收：飞牛 NAS 发现与修复

**日期**：2026-10-13  
**场景**：飞牛 NAS（fnOS，x86_64）上把 platform-api 从源码 dev 构建切到正式版镜像，
再用网页一键升级验证升级链路。

## 结论

升级链路**基本可用**，但在真机（ZFS + 单文件 bind mount）上暴露两个真实缺陷，均已修复。

## 真机执行记录

1. 发布 `v1.0.0`（干净基线）与 `v1.0.1`（带存储目录可视化选择），CI 构建多架构镜像推 GHCR。
2. NAS 切到 `ghcr.io/lianying1716/platform-api:v1.0.0`（此前是本地 dev 源码构建），
   `db_revision=0019` 与 head 同步，updater 边车起在 `platform-updater:9200`。
3. 触发升级 `v1.0.0 → v1.0.1`（走 updater 引擎：备份 → 拉取 → 重建 → 健康校验 → pin）。
4. **关键步骤全部成功**：备份产出 `backup-v1.0.1-*.tar.gz`；pull/recreate/verify 通过，
   升级后 `/api/system/version` 报 `v1.0.1`，`db_in_sync=true`。
5. **最后一步 pin 失败**，并连带把回滚也拖失败（见下）。

## 缺陷 1：`.env` 是单文件 bind mount，`os.replace` 抛 EBUSY

- 现象：`pin` 步骤报
  `版本写入 .env 失败：[Errno 16] Device or resource busy: '/compose/.env.xxxx.tmp' -> '/compose/.env'`。
- 根因：compose 把宿主 `.env` 以**单文件** bind mount 进 updater 容器；
  `write_pinned_version` 用的是「同目录 tmp 文件 + `os.replace`」，而 rename 跨不过挂载点，
  ZFS 上返回 EBUSY。设计原本假定 `.env` 是普通文件。
- 影响：升级实际已生效（新版本在跑），却因"记不上版本"被判失败，用户看到升级失败；
  重启/compose up 还会按旧 `.env` 静默回退——正是 pin 要防的问题。
- 修复：`write_pinned_version` 先尝试 tmp+rename；命中 EBUSY（单文件 bind mount）时
  退回**原地截断写**（目标即挂载点本身），并清理临时文件。新增回归测试 `test_write_falls_back_when_target_is_bind_mount`。

## 缺陷 2：回滚把 pull 当硬前置，网络一抖就"回滚也失败"

- 现象：回滚报 `回滚拉取失败：... TLS handshake timeout`。
- 根因：`_do_rollback` 先 `docker compose pull` 再 `up -d`；旧镜像此刻通常已在本机，
  pull 纯属多余，却让一次瞬时网络抖动把可自愈的升级变成需人工恢复。
- 修复：回滚直接 `up -d`（用本机旧镜像）；仅当 up 失败才尝试 pull。失败信息同时带 up 与 pull 的 stderr。

## 遗留 / 后续

- 本次触发升级走的是 updater HTTP 接口（与网页「一键升级」按钮同一引擎）；网页按钮本身
  未点（缺 NAS 管理员密码），待补做 UI 端到端点击。
- 升级后 `.env` 需为 `PLATFORM_VERSION=v1.0.1`（修复前遗留为 v1.0.0，已手动纠正）。
