# 存储目录可视化选择（SP 设计）

**状态**：已实现（方案 A）· 后端 `app/core/fs_browse.py` + `GET /api/system/fs/{roots,list}`；
前端 `web/src/pages/Settings.vue` 目录浏览器；Docker 见 `docker-compose.yml` / `.env.example` 的
`PLATFORM_BROWSE_ROOTS` 与 bind mount 注释。
**日期**：2026-10-13

## 背景 / 问题

「系统设置 → 存储目录 → 新增根目录」现在只能手输一个绝对路径。用户希望像本机软件那样
"读取当前系统的文件夹、可视化点选"。但星采是**纯 HTTP 服务**（FastAPI + Vue，非 Electron 壳），
浏览器拿不到宿主机文件系统，也弹不出操作系统选择框，所以只能：

后端提供一个**只读列目录**接口，前端在弹窗里画一个迷你文件管理器。

## 关键约束：路径是给谁用的

真正读写素材的是 **worker 进程**（`app/workers/handlers.py`），不是浏览器、也未必是打开页面的
那台机器。所以 `storage_root.path` 必须是 **backend/worker 进程可见**的文件系统里真实存在的路径。
`app/core/storage.py::probe_root()` 已经建立这个假设（非 Windows 主机拿到 `Z:\...` 返回"无法校验"）。
四种环境差异全部从这一点长出来。

## 四种环境

- **Linux / macOS 裸机**：后端进程在本机，`os.scandir` 直接遍历。macOS 注意 TCC：`~/Desktop`、
  `~/Documents`、`~/Downloads`、外接卷首次访问会触发授权，被拒要如实返回"系统未授权"而非"不存在"。
- **Windows 裸机**：盘符枚举 + UNC（`\\NAS\share`）。路径风格交给 `PureWindowsPath`（已在用）。
- **Docker**：容器默认只看得到容器内文件系统。宿主候选目录必须先 **bind mount** 进容器
  （如 `- ${STORAGE_NAS}:/mnt/nas`），用户选到的是"容器内挂载点"。**白名单** `PLATFORM_BROWSE_ROOTS`
  只列挂载进来的根，绝不放整个容器文件系统出去（用户已确认选此方案）。

## 设计

### 后端

- 新增模块 `app/core/fs_browse.py`：
  - `allowed_roots()`：优先 `PLATFORM_BROWSE_ROOTS`；未配置时——容器内**只放行 `/data`**（安全默认），
    裸机回退到平台默认根（POSIX：`/`、`/Volumes`、`/mnt`、`/media`、`$HOME`；Windows：可用盘符 + UNC）。
  - `list_dir(path)`：只列**子目录**（选存储根本质是选目录），返回 `{path, parent, sep, roots, entries}`；
    每项含 `{name, path, is_dir, writable, is_link}`。只读、只回目录项与可写位，**绝不回文件内容**。
  - 越权防护：`os.path.realpath` 解析后用 `startswith(root + sep)` 做包含判断 → 拒绝软链越权；
    非法/越界路径抛 `BrowseError`。父目录越界则不提供"上一级"。
- 接口（挂在 `app/api/system.py`，复用 `security.require_admin`）：
  - `GET /api/system/fs/roots` → 允许浏览的根清单（含 `exists`/`writable`）。
  - `GET /api/system/fs/list?path=...` → 列目录；`path` 空则回第一个根；越界返回 400。
- `POST /api/system/storage-roots` 保存后回带 `available/detail`（调用现有 `probe_root`），供前端软提示；
  **不阻塞保存**（非 Windows 主机配置 Windows 路径仍合法，只是"存了待校验"）。

### 前端（`web/src/pages/Settings.vue`）

- 「新增存储根目录」弹窗的路径输入旁加「浏览…」按钮。
- 新增「选择目录」弹窗：根快捷入口 + 面包屑 + 目录列表（点击下钻）+ 上一级 + 手输路径兜底 +
  「选用此目录」回填 `rootForm.path`。沿用现有 `.ui-*` 样式。

### Docker 配置

- `docker-compose.yml` 里给 api/worker 增加**注释示例**的 bind mount + `PLATFORM_BROWSE_ROOTS`；
  `.env.example` 增加 `PLATFORM_BROWSE_ROOTS` 与 `STORAGE_*` 说明。

## 不做 / 边界

- 不让浏览器整盘遍历容器文件系统（已排除方案 2）。
- 不改落盘红线：真正写盘仍走 `storage_root + relative_path`，外部 API 依旧不收绝对输出路径；
  列目录只是管理员挑路径用的 UI 辅助接口。
- 不引入 Electron 壳 / File System Access API（拿不到绝对路径字符串，且无桌面壳）。

## 验收

- 后端：`uv run pytest tests/test_storage_browse_api.py -q` 绿；`uv run pytest tests/ -q` 不回归。
- 前端：`cd web && npm run build` 通过。
- 浏览器（Playwright，真事件）：在 Vite :5173 打开设置页，新增根目录 → 浏览 → 下钻 → 选用 → 保存，
  截图留证。

### 验收结果（2026-10-13）

- 后端：`tests/test_storage_browse_api.py` 9 passed；全量 `uv run pytest tests/ -q` → 756 passed, 1 skipped。
- 前端：`cd web && npm run build` 通过。
- 浏览器（Playwright 真事件，独立预览实例：临时 SQLite + `PLATFORM_BROWSE_ROOTS=/tmp/xsai-browse-demo`，
  未触碰开发服）：设置页 → 新增根目录 → 浏览 → 下钻 `nas-media` → 「选用此目录」自动回填路径与名称
  → 保存成功，列表显示「可用 可读写」；越界 `/etc` 返回 400。
  截图：`docs/plans/evidence/2026-10-13-storage-path-picker/`（`02/03/04-*.png`）。
