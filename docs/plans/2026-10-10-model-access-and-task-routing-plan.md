# 模型接入与按工作分配模型 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户用预置供应商和最少字段导入任意模型，并给文字分析、封面判类、视频理解三个工作槽位分别配置主备模型。

**Architecture:** 新增 AI 供应商/模型/工作分配/调用审计四张表；用 `model_protocols` 统一 OpenAI、Anthropic、Gemini 协议差异；用 `ModelAccessService` 负责能力探测、主备调用、失败审计与旧配置兼容。前端新增“模型接入”一级页面；旧设置页只保留入口和加工参数。

**Tech Stack:** FastAPI、SQLAlchemy 2.0、Alembic、httpx、Pillow、ffmpeg、Vue 3、Element Plus、pytest。

## Global Constraints

- 所有供应商访问密钥必须 AES-GCM 加密入库，响应、日志、审计不得出现明文。
- 模型能力以探测为准；视频能力只有真实视频探测通过或用户手工确认后可用。
- 自动推荐只能填空槽位，不得覆盖人工选择。
- 三个工作槽位固定为 `text_analysis`、`cover_classify`、`video_understanding`。
- 主模型失败可切备用一次；必须保留主备失败分类、原始报错与实际使用模型。
- 旧 `llm_*` / `process_*` 配置可迁移；迁移不得自动生成新加密密钥，不得为回滚保留明文 Key。
- `/api/model-access` 必须管理员 session 与 API Key 双轨，沿用 `sk-` Key 前缀。
- PostgreSQL 为生产主库；SQLite 迁移必须使用 batch 兼容。

---

### Task 1: 数据模型与迁移

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/0018_model_access.py`
- Test: `tests/test_model_access_models.py`
- Test: `tests/test_model_access_migration.py`

**Interfaces:**
- Produces: ORM `AiProvider`, `AiModel`, `ModelAssignment`, `ModelCallAttempt`; result tables get `routing` JSON.
- Produces: `AiProvider.config_revision`, `AiModel.capabilities`, `ModelAssignment.legacy_pending_validation`.
- Produces: `ModelCallAttempt.finished_at`.
- Consumes: `app.core.crypto.encrypt_credential/decrypt_credential`.

- [x] **Step 1: Write failing model tests**
- [x] **Step 2: Add ORM models and result columns**
- [x] **Step 3: Add migration 0018 following design sections 6-7**
- [x] **Step 4: Run focused tests**
- [x] **Step 5: Run `uv run pytest tests/test_model_access_models.py tests/test_migration_0018_model_access.py -q`**

### Task 2: 协议适配器与能力探测

**Files:**
- Create: `app/modules/model_protocols.py`
- Create: `app/services/model_probe.py`
- Test: `tests/test_model_protocols.py`
- Test: `tests/test_model_probe.py`

**Interfaces:**
- Produces: `list_models(provider)`, `chat(provider, model, request)`, `video_chat(...)`, `classify_error(exc)`.
- Produces: `probe_model(db, model_id, *, text=True, image=True, video=True)`.

- [x] **Step 1: Write failing protocol/probe tests**
- [x] **Step 2: Implement OpenAI / Anthropic / Gemini request builders and response parsers**
- [x] **Step 3: Implement deterministic red image and red-blue video probes**
- [x] **Step 4: Run focused tests**
- [x] **Step 5: Run `uv run pytest tests/test_model_protocols.py tests/test_model_probe.py -q`**

### Task 3: 供应商与模型 API

**Files:**
- Create: `app/api/model_access.py`
- Modify: `app/main.py`
- Modify: `pyproject.toml`
- Test: `tests/test_model_access_api.py`

**Interfaces:**
- Produces: `/api/model-access/providers`, `/models`, `/assignments`, `/calls` with admin+API Key auth.
- Consumes: `model_protocols.list_models`, `crypto.encrypt_credential`, `ModelAccessService`.

- [x] **Step 1: Write failing API tests for create provider, key redaction, import, assignment, auth**
- [x] **Step 2: Implement provider/model CRUD and discovery**
- [x] **Step 3: Implement work assignment API and automatic recommendation**
- [x] **Step 4: Register router and add Pillow dependency**
- [x] **Step 5: Run `uv run pytest tests/test_model_access_api.py -q`**

### Task 4: 统一调用网关与主备切换

**Files:**
- Create: `app/services/model_access_service.py`
- Test: `tests/test_model_access_routing.py`
- Test: `tests/test_model_access_attempt_session.py`

**Interfaces:**
- Produces: `ModelCallResult`, `ModelCallError`, `RoutingStage`, `ModelAccessService.call_with_fallback(...)`.
- Produces: `resolve_assignment(db, work_type)` and `capabilities_of(model)`.
- Consumes: `model_call_attempt`, `model_assignment`, `model_protocols`.

- [x] **Step 1: Write failing tests for primary success, fallback success, both fail, disabled, preflight failure**
- [x] **Step 2: Implement capability gating and assignment resolution**
- [x] **Step 3: Implement fallback and independent attempt factory lifecycle**
- [x] **Step 4: Implement `to_routing()` and failure summaries**
- [x] **Step 5: Run `uv run pytest tests/test_model_access_routing.py -q`**

### Task 5: 业务模块接入

**Files:**
- Modify: `app/modules/classify.py`
- Modify: `app/modules/vision.py`
- Modify: `app/modules/breakdown.py`
- Modify: `app/services/understand_service.py`
- Modify: `app/services/breakdown_service.py`
- Modify: `app/workers/handlers.py`
- Modify: `app/workers/runner.py`
- Test: `tests/test_model_routing_analysis.py`
- Test: `tests/test_model_routing_vision.py`
- Test: `tests/test_model_routing_breakdown.py`
- Test: `tests/test_model_routing_classify_best_effort.py`

**Interfaces:**
- Consumes: `ModelAccessService.call_with_fallback`.
- Produces: result `routing` with `text_analysis` or `cover_classify` + `video_understanding` stages.
- Produces: handler signature `handler(db, payload, job)`.

- [x] **Step 1: Write failing integration tests with fake models**
- [x] **Step 2: Route analyze, classify, vision, breakdown through gateway**
- [x] **Step 3: Preserve classify best-effort fallback behavior**
- [x] **Step 4: Pass queue job id into attempts**
- [x] **Step 5: Run focused integration tests**

### Task 6: 前端模型接入页

**Files:**
- Create: `web/src/pages/ModelAccess.vue`
- Modify: `web/src/router.ts`
- Modify: `web/src/layout/Layout.vue`
- Modify: `web/src/pages/Settings.vue`
- Test: `web/src/router.ts` build check

**Interfaces:**
- Consumes: `/api/model-access/*`.
- Produces: `已导入模型` / `工作分配` / `调用审计` 三个页签、三步添加模型弹窗、能力标签、主备下拉和调用审计表。

- [x] **Step 1: Write page component against API contracts**
- [x] **Step 2: Register route and navigation**
- [x] **Step 3: Remove model fields from Settings and link to model access**
- [x] **Step 4: Run `cd web && npm run build`**

### Task 6.1: 调用审计页

**Files:**
- Modify: `app/api/model_access.py`
- Modify: `web/src/pages/ModelAccess.vue`
- Modify: `tests/test_model_access_api.py`

**Interfaces:**
- Consumes: `GET /api/model-access/calls`、`X-Total-Count`。
- Produces: 工作/状态/模型/素材筛选、分页、主备角色、完整原始报错展开。

- [x] **Step 1: Add total count header and asset title to call attempts**
- [x] **Step 2: Build the audit tab with filters, paging and expandable errors**
- [x] **Step 3: Add API regression coverage**
- [x] **Step 4: Run focused backend tests and frontend build**

### Task 7: 旧配置迁移与兼容层

**Files:**
- Modify: `app/api/system.py`
- Modify: `app/services/understand_service.py`
- Modify: `app/modules/analysis.py`
- Test: `tests/test_model_migration.py`
- Test: `tests/test_model_migration_defaults.py`
- Test: `tests/test_model_migration_atomicity.py`
- Test: `tests/test_model_migration_crypto_ready.py`
- Test: `tests/test_model_migration_protocol.py`

**Interfaces:**
- Consumes: 0018 migration.
- Produces: compat settings API without plaintext key writes.

- [x] **Step 1: Write failing migration/compat tests**
- [x] **Step 2: Implement atomic legacy migration and completion marker rules**
- [x] **Step 3: Implement old settings GET/PUT compatibility without plaintext writes**
- [x] **Step 4: Run focused migration tests**

### Task 8: 端到端验收与收尾

**Files:**
- Modify: `docs/plans/README.md`
- Test: `tests/test_model_access_e2e.py`

**Interfaces:**
- Produces: fake provider E2E with encrypted key and fallback routing.

- [x] **Step 1: Add fake-provider E2E test**
- [x] **Step 2: Run `uv run pytest tests/test_model_access_e2e.py -q`**
- [x] **Step 3: Run backend suite `uv run pytest tests/ -q`**
- [x] **Step 4: Run frontend build `cd web && npm run build`**
- [x] **Step 5: Update plan index and report evidence**
