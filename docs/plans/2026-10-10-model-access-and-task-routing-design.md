# 模型接入与按工作分配模型 · 设计

> 日期：2026-10-10
> 范围：`platform-api`
> 状态：设计已确认，待实现
> 关联：星算只需普通 API Key 导入，不实现一键授权。

## 1. 背景与目标

当前平台只有一个全局大模型配置（`llm_base_url` / `llm_api_key` / `llm_model`），
视频理解又单独使用 `process_provider` / `process_classify_model` / `process_vision_model`。
这会带来四个问题：

1. 用户为了接入不同供应商，必须自己理解 Base URL、模型名、OpenAI 兼容协议和视频输入差异。
2. 内容分析、封面判类、视频理解、爆款拆解共用或隐式复用同一个模型，无法按工作选择更合适的模型。
3. 模型能力只靠用户手填或供应商元数据判断；视频输入尤其不可信，错误会拖到任务运行时才暴露。
4. 供应商访问密钥目前以明文 KV 存在 `system_setting`，不符合“所有凭证 AES-GCM 加密入库”的架构红线。

本次目标：

- 提供一个简单直白的“模型接入”模块，让用户用最少步骤导入任意 OpenAI 兼容模型，并提供常见供应商预设。
- 提供“已导入模型”列表，用户能直观看到每个模型能做什么、当前负责什么工作、最近是否检测通过。
- 把模型工作收敛为三个固定槽位：文字分析、封面判类、视频理解。
- 每个槽位支持一个主模型和一个可选备用模型；主模型失败时可以切备用，但必须保留失败原因与原始报错。
- 由系统根据能力自动推荐工作分配，但绝不覆盖用户已经手工选择的配置。
- 本期提供星算AI推荐入口和预置供应商，使用普通访问密钥导入。

## 2. 非目标

本阶段明确不做：

- 不实现星算 OAuth / 设备码一键授权。星算只做推荐入口、预置 Base URL、官网跳转和普通密钥导入。
- 不做跨多个模型自动择优、投票、结果质量比较或自动路由到最便宜模型。
- 不做模型费用预算、余额告警、成本预测；本阶段只记录每次调用的 usage。
- 不做多用户、角色或项目级模型权限；平台仍是单管理员模式。
- 不做本地模型运行时管理；本地部署的 OpenAI 兼容服务通过“自定义供应商”接入。
- 不把四个内部任务都暴露给用户。用户只配置三个工作槽位：
  - `text_analysis`：内容分析与爆款拆解。
  - `cover_classify`：文本判类 + 可选封面图。
  - `video_understanding`：真实视频理解。

## 3. 用户侧概念

界面上只保留三个用户必须理解的概念：

### 3.1 供应商

模型服务的来源。常见供应商提供预设，选择后自动带出 Base URL 和协议。

第一版内置预设：

| 预设 | 默认协议 | Base URL | 典型用途 |
|---|---|---|---|
| 星算AI（推荐） | OpenAI 兼容 | `https://api.xsai5.xyz/v1` | 项目已做模型调用深度优化的默认选择；提供官网跳转与普通密钥导入 |
| OpenAI | OpenAI 兼容 | `https://api.openai.com/v1` | 文本、图片输入 |
| DeepSeek | OpenAI 兼容 | `https://api.deepseek.com/v1` | 文本 |
| Kimi（Moonshot） | OpenAI 兼容 | `https://api.moonshot.cn/v1` | 文本、图片输入 |
| 通义千问（DashScope 兼容模式） | OpenAI 兼容 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 文本、图片输入、部分视频输入 |
| 智谱 GLM | OpenAI 兼容 | `https://open.bigmodel.cn/api/paas/v4` | 文本、图片输入 |
| OpenRouter | OpenAI 兼容 | `https://openrouter.ai/api/v1` | 多供应商聚合 |
| Anthropic | Anthropic 原生 | `https://api.anthropic.com` | 文本、图片输入 |
| Gemini | Gemini 原生 | `https://generativelanguage.googleapis.com` | 文本、图片输入、视频输入 |
| 自定义 | 可选 OpenAI 兼容 / Anthropic 原生 / Gemini 原生 | 用户填写 | 本地模型、自建网关、其他兼容服务 |

模型接入页顶部固定展示一条星算AI推荐说明，文案说明其已为本项目的视频理解、结构化输出和主备模型切换链路做适配，并提供官网跳转。推荐不阻断其他供应商接入，新增模型时仍可在预设列表中自由切换。

“协议”默认折叠在高级选项里。普通用户只选预设、填访问密钥、选模型。

> 这里的“访问密钥”指模型供应商签发的凭据，不是平台自己的 AI 接入 API Key。
> 界面文案使用“访问密钥”，避免与「AI 接入 → API Key」混淆。

### 3.2 已导入模型

用户从供应商模型清单中选中，或手工输入后导入的模型。一个模型必须属于一个供应商。

模型列表只用人话展示三类能力：

- **文字**：能完成文本分析并稳定输出结构化结果。
- **看图**：能把图片作为输入，用于封面判类。
- **看视频**：能真实读取视频画面，而不是仅接受视频文件或只处理封面。

详细探测结果、最近错误、协议类型等放在展开项里，不占默认视线。

### 3.3 工作分配

三个固定工作槽位，每个槽位可配置：

- **主模型**：默认执行该工作的模型。
- **备用模型（可选）**：主模型失败时使用的兜底模型。

槽位说明使用任务语言，不使用协议或模型参数语言：

| 工作槽位 | 界面名称 | 人话要求 |
|---|---|---|
| `text_analysis` | 文字分析 | 读文字，输出摘要、标签或结构化拆解 |
| `cover_classify` | 封面判类 | 读文字，也能看封面图，输出分类结果 |
| `video_understanding` | 视频理解 | 真正看视频画面，结合转录输出结构化理解 |

## 4. 设计原则

1. **默认简单，高级可改**：系统自动推荐并填充三个槽位；用户只改自己想改的部分。
2. **自动推荐不覆盖人工配置**：任何自动逻辑都只能填空槽位，不能改写已由用户确认的分配。
3. **能力以实测为准**：供应商元数据和模型名只用于预筛选；文字、图片、视频能力最终以平台探测结果为准。
4. **失败必须可见**：备用模型可以兜底，但主模型失败分类、原始报错、实际使用模型都必须入库并可查看。
5. **凭证只进不出**：供应商访问密钥使用现有 AES-GCM 工具加密入库；任何列表、详情、日志、报错都不得回显明文。
6. **任务层不关心供应商差异**：内容分析、判类、视频理解、拆解只声明工作类型和输入，由模型网关选择协议与端点。
7. **第一版不做自动择优**：用户已明确指定主备模型，系统不擅自尝试第三个模型，也不因模型更便宜而换路。

## 5. 总体架构

```text
前端：模型接入页
  ├── 已导入模型（列表 / 添加模型 / 能力检测）
  └── 工作分配（文字分析 / 封面判类 / 视频理解）
          │
          ▼
API：/api/model-access/*
  ├── provider_svc：供应商、Key 加密、模型清单拉取、连接测试
  ├── model_svc：导入模型、能力状态、启用/停用
  ├── assignment_svc：三个工作槽位、自动推荐、人工覆盖
  └── call_audit_svc：调用尝试、失败分类、原始报错、usage
          │
          ▼
服务层：model_access_service
  ├── resolve_assignment(db, work_type)
  ├── call_with_fallback(...)
  └── probe_model(...)
          │
          ▼
模块层：model_protocols
  ├── OpenAI-compatible chat/completions
  ├── Anthropic messages
  ├── Gemini generateContent
  └── 视频输入 transport：openai_video_url / gemini_inline
          │
          ▼
外部模型供应商
```

业务模块的职责边界：

- `analysis.py`：构造分析提示词，调用文字分析工作，不再自行解析全局 `llm_*`。
- `classify.py`：构造判类提示词，调用封面判类工作，支持有无封面两种输入。
- `vision.py`：构造视频理解请求，调用视频理解工作；协议差异交给 `model_protocols`。
- `breakdown.py`：构造拆解提示词，调用文字分析工作。
- `model_access_service`：唯一负责解析工作分配、执行主备切换和写调用审计。
- `model_protocols`：唯一负责不同供应商协议的请求体、鉴权头、响应解析和错误归类。

## 6. 数据模型

新增四张表，沿用现有单数表名风格。

### 6.1 `ai_provider`

供应商实例。一个供应商可以导入多个模型。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | 主键 |
| `name` | varchar(120) | 用户可读名称，如“我的 OpenAI” |
| `preset_key` | varchar(64) | 预设标识；自定义为 `custom` |
| `protocol` | varchar(32) | `openai` / `anthropic` / `gemini` |
| `base_url` | varchar(512) | API 根地址；是否含 `/v1` 由协议适配器归一 |
| `credential_source` | varchar(16) | 固定 `api_key` |
| `api_key_encrypted` | text nullable | AES-GCM 密文；不得有明文列 |
| `config_revision` | int | 连接配置版本；`base_url` 或 `protocol` 变化时 +1，用于使旧探测失效 |
| `enabled` | bool | 停用后不可用于新调用 |
| `last_test_at` | datetime nullable | 最近连接测试时间 |
| `last_test_status` | varchar(16) | `ok` / `error` / 空 |
| `last_test_error` | text | 最近连接测试错误，脱敏后保存 |
| `created_at` / `updated_at` | datetime | 时间戳 |

约束：

- 同一 `preset_key + base_url` 不强制唯一。用户可能对同一供应商使用不同账号或不同 Key。
- `api_key_encrypted` 通过 `app.core.crypto.encrypt_credential({"api_key": ...})` 写入。
- `credential_source` 只允许 `api_key`，不预留其他来源。
- `enabled=false` 是硬停用：其下所有模型即使自身 `enabled=true` 也不得用于任何新调用、推荐或分配校验。
- API 响应只返回 `has_api_key` 和 `api_key_masked`。

### 6.2 `ai_model`

已导入模型。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | 主键 |
| `provider_id` | FK `ai_provider.id` ON DELETE CASCADE | 所属供应商 |
| `model_key` | varchar(200) | 供应商侧模型 ID；上游可能超过 64 字符 |
| `display_name` | varchar(200) | 展示名，默认等于 `model_key` |
| `import_source` | varchar(16) | `discovered` / `manual` |
| `enabled` | bool | 停用后不可用于新调用 |
| `capabilities` | JSON | 能力状态与探测详情 |
| `probe_status` | varchar(16) | `untested` / `testing` / `passed` / `partial` / `failed` / `skipped` |
| `probe_error` | text | 最近探测错误摘要 |
| `probed_at` | datetime nullable | 最近探测完成时间 |
| `created_at` / `updated_at` | datetime | 时间戳 |

唯一约束：`(provider_id, model_key)`。

`capabilities` 建议结构：

```json
{
  "text": {
    "value": true,
    "source": "probe",
    "checked_at": "2026-10-10T12:00:00Z",
    "detail": "通过文本与 JSON 输出探测"
  },
  "json": {
    "value": true,
    "source": "probe",
    "mode": "json_mode",
    "detail": "支持 response_format=json_object"
  },
  "image": {
    "value": false,
    "source": "probe",
    "detail": "模型忽略了图片输入"
  },
  "video": {
    "value": false,
    "source": "probe",
    "transport": "openai_video_url",
    "auth_style": "bearer",
    "detail": "视频探测未通过"
  },
  "manual_override": {
    "video": false,
    "reason": ""
  }
}
```

每个能力对象必须记录 `config_revision`：

```json
{
  "text": {
    "value": true,
    "source": "probe",
    "config_revision": 3,
    "probe_version": "v1",
    "checked_at": "2026-10-10T12:00:00Z",
    "detail": "通过文本与 JSON 输出探测"
  }
}
```

探测有效性规则：

- `capabilities_of(model)` 返回某能力为 `value=true` 的必要条件：能力存在、`source` 为 `probe` 或
  `manual_override`、`config_revision == provider.config_revision`。
- `config_revision` 不匹配时按 `source=stale`、`value=false` 返回，详情写“供应商连接配置已变更，需重新检测”。
  API 与推荐都必须使用这一结果，禁止直接读 JSON 里的历史 `value`。

视频协议与鉴权必须分开记录，不能假设等于供应商对话协议：

| `transport` | 请求格式 | 固定 `auth_style` | 端点 |
|---|---|---|---|
| `openai_video_url` | OpenAI 兼容 `video_url` + data URI | `bearer`：`Authorization: Bearer <key>` | `{base_url}/chat/completions` |
| `gemini_inline` | Gemini 原生 `inlineData` | `gemini_api_key`：`x-goog-api-key: <key>` | 站点根 `/v1beta/models/{model}:generateContent` |

- `provider.protocol` 只决定文本、判类、拆解等对话请求的协议与鉴权。
- 视频调用以 `capabilities.video.transport` 决定请求格式和鉴权风格，不读取 `provider.protocol`。
- 因此 `provider.protocol=openai` + `transport=gemini_inline` 是合法组合：对话走 Bearer，
  视频走 Gemini 原生端点与 `x-goog-api-key`。
- `auth_style` 是 `transport` 的派生快照，不是独立配置源：运行期必须先由 `transport` 经固定映射得到
  鉴权风格；`auth_style` 缺失或与映射不一致时都归一为映射值，保证迁移旧数据不会因缺字段失败。

能力来源枚举：

- `metadata`：来自供应商模型清单，只作初始提示。
- `probe`：平台真实调用探测得出。
- `manual_override`：用户明确手工标记；视频能力允许，但列表页必须显示“手工标记，未实测”。

### 6.3 `model_assignment`

三个固定工作槽位。每行一个槽位。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | 主键 |
| `work_type` | varchar(48) unique | `text_analysis` / `cover_classify` / `video_understanding` |
| `primary_model_id` | FK `ai_model.id` ON DELETE SET NULL, nullable | 主模型 |
| `fallback_model_id` | FK `ai_model.id` ON DELETE SET NULL, nullable | 备用模型 |
| `auto_configured` | bool | 是否由系统推荐填充；用户修改后置 false |
| `legacy_pending_validation` | bool | 仅迁移生成的 assignment：能力未实测前的受控过渡标记；用户修改或探测完成后置 false |
| `updated_at` | datetime | 最近修改时间 |

约束：

- `primary_model_id` 与 `fallback_model_id` 不能相同。
- 指定模型必须启用且满足该槽位的最低能力。
- 删除被分配模型前必须先解除分配，避免静默失效。
- 若主模型被停用，工作分配页显示明确警告；运行期将主模型视为“不可用”而不是“执行失败”，
  存在合格备用模型时直接使用备用模型并记录 `primary_error_class=disabled`。
- 若主模型和备用模型都不可用（停用、被删、能力不再满足），任务在发起任何外部请求前失败，
  错误写明“该工作没有可用模型”，不计费、不写模型调用 attempt。
- 若仅备用模型被停用，工作分配页显示警告，主模型正常执行，不发生切换。

### 6.4 `model_call_attempt`

每次真实模型调用一行。探测调用也写这里，`work_type` 使用 `probe_text` / `probe_image` / `probe_video`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | 主键 |
| `attempt_group_id` | varchar(64) | 一次业务调用及其备用尝试共享 |
| `work_type` | varchar(48) | 工作槽位或探测类型 |
| `operation` | varchar(48) | `analyze` / `classify` / `understand_video` / `breakdown` / `probe` |
| `asset_id` | int nullable | 关联素材，便于素材详情展示 |
| `job_id` | int nullable | 关联队列任务 |
| `role` | varchar(16) | `primary` / `fallback` / `probe` |
| `provider_id` | int nullable | 供应商 ID；删除后置空 |
| `provider_name` | varchar(120) | 供应商名称快照 |
| `model_id` | int nullable | 模型 ID；删除后置空 |
| `model_key` | varchar(200) | 模型 ID 快照 |
| `status` | varchar(16) | `running` / `ok` / `error` |
| `error_class` | varchar(32) | 失败分类，见 §9.3 |
| `error_message` | text | 原始错误文本，最多保留 8000 字符 |
| `http_status` | int nullable | 上游 HTTP 状态码 |
| `duration_ms` | int | 调用耗时 |
| `usage` | JSON | 上游返回的 token / video token 用量 |
| `request_meta` | JSON | 仅保存体积、是否带图、是否带视频、协议等，不保存访问密钥和完整请求体 |
| `created_at` | datetime | 时间戳 |
| `finished_at` | datetime nullable | attempt 结束时间；running 回收以此为准，未结束为空 |

索引：

- `(attempt_group_id)`
- `(work_type, created_at)`
- `(asset_id, created_at)`
- `(model_id, created_at)`
- `(status, finished_at, created_at)`

审计外键删除语义必须显式写进迁移：

- `model_call_attempt.provider_id`：`FK ai_provider.id ON DELETE SET NULL`。
- `model_call_attempt.model_id`：`FK ai_model.id ON DELETE SET NULL`。
- `model_assignment.primary_model_id` / `fallback_model_id`：`FK ai_model.id ON DELETE SET NULL`。
- `ai_model.provider_id`：`FK ai_provider.id ON DELETE CASCADE`；供应商删除时模型一并删除，但历史 attempt 仍保留快照。
- 删除供应商或模型后，`model_call_attempt` 行必须仍可查询，`provider_name` / `model_key` 快照保持可读。

## 7. 旧配置迁移

新增 Alembic `0018_model_access.py`，承接当前最新版本 `0017`。

迁移顺序：

0. 入口前置检查，先于任何建表/写数据动作：
   1）加密子系统必须就绪：显式 `PLATFORM_CREDENTIAL_ENC_KEY`，或已有可读的
   `credential_key_file` / `data/secret.key`。禁止触发 `_load_key()` 的自动生成兜底；未就绪直接失败。
   2）通过 `op.get_bind()` 直接读取 `system_setting`，若
   `model_access_legacy_imported` 已为 `"1"`，记录日志后直接返回，不再创建供应商、模型或 assignment。
   该守卫只用于迁移测试与显式手工重放，不声称具备生产级“半完成自动修复”能力；标准 Alembic 不会重跑。
   若标记为 1，还必须在返回前逐项检查以下结构存在；任一缺失必须显式报错，不得静默返回：
   - 四张新表：`ai_provider`、`ai_model`、`model_assignment`、`model_call_attempt`。
   - 三个 `routing` 列：`analysis_result.routing`、`video_understanding.routing`、`viral_breakdown.routing`。
   - 三个 `model` 列长度：`analysis_result.model`、`video_understanding.model`、`viral_breakdown.model`
     均不小于 200。
   该守卫只承诺“关键结构存在”，不承诺约束、索引和外键语义完整；完整语义由迁移测试和 fresh
   迁移链路验证，避免给运维造成“守卫通过即数据库完好”的错误安全感。
1. 创建四张新表和索引。
2. 将现有结果表的模型名字段从 `String(64)` 扩到 `String(200)`，并新增 `routing` JSON 列：
   - `analysis_result.model`
   - `video_understanding.model`
   - `viral_breakdown.model`
   - `analysis_result.routing`
   - `video_understanding.routing`
   - `viral_breakdown.routing`
   方言处理与 0005 迁移一致：PostgreSQL 直接 `alter_column` / `add_column`；
   SQLite 不支持直接改列类型，使用 `op.batch_alter_table` 重建表。新增 JSON 列两边都可直接添加。
3. 读取旧有效配置。有效配置必须复用运行时同一套解析优先级，不能只读 `system_setting`：
   - Base URL：DB `llm_base_url` > `settings.llm_base_url`（包含环境变量）。
   - Key：DB `llm_api_key` > `settings.llm_api_key`（包含环境变量）。
   - 模型：DB `llm_model` > `settings.llm_model`。
   - `process_provider`：同样按 `resolve_process_settings_from_rows()["provider"]` 解析，包含默认值 `qwen`。
   - 判类模型：按 `get_process_settings` 的同一套默认值与类型归一规则解析 `classify_model`。
   - 视频模型：按同一规则解析 `vision_model`。
   - 迁移层不得构造 ORM `Session` 或调用 `app.db.SessionLocal`，否则会开第二个连接/事务，破坏
     “步骤 1–8 同一 Alembic 事务”的约束。实现上把运行时解析抽成纯函数：

     ```python
     def resolve_process_settings_from_rows(rows: Mapping[str, str]) -> dict:
         """从 system_setting 行映射解析加工配置；不依赖 DB/Session。"""

     def get_process_settings(db: Session) -> dict:
         return resolve_process_settings_from_rows(_rows(db))
     ```

     迁移层用 `op.get_bind()` 一次查出 `system_setting` 行映射，再调用该纯函数取
     `classify_model` / `vision_model`。这样运行时与迁移共用默认值，不会因复制常量而漂移。
   不得只读原始 KV；否则 `process_classify_model` / `process_vision_model` 未显式落库时会被漏迁，
   删除旧 Key 后这两个槽位无法回退。
4. 如果解析出的 Base URL 与模型名存在：
   - 以名称 `系统迁移（旧配置）` + `preset_key=custom` + 解析出的 Base URL 做 get-or-create；
     已存在则复用，避免重复迁移产生多个供应商。
   - 对话协议只按 Base URL 推断：星算/OpenAI 兼容地址为
     `openai`，明确的 Gemini 站点根地址为 `gemini`。**不得用 `process_provider` 推断供应商的对话协议**，
    它只描述旧视频端点，不描述旧文本/判类端点。
   - 用 get-or-create 语义创建一个 `ai_model`，模型名为解析出的模型名；后续步骤复用同一模型，不重复插入。
   - 分支 A（解析出 Key）：把 Key 通过 AES-GCM 加密写入 `ai_provider.api_key_encrypted`；
     将 `text_analysis.primary_model_id` 指向该模型，并设置 `auto_configured=true`、
     `legacy_pending_validation=true`。
   - 分支 B（未解析出 Key）：`api_key_encrypted` 留空，**不建立任何 assignment**；保留旧配置回退，
     并在供应商列表显示“缺少访问密钥，尚未接管任务”，避免升级后认证全部失败。
5. 仅分支 A 继续：使用上一步解析出的判类有效模型（含内置默认值），get-or-create 为同一供应商下的模型，
   并作为 `cover_classify.primary_model_id`，设置 `auto_configured=true`、`legacy_pending_validation=true`。
6. 仅分支 A 继续：使用上一步解析出的视频有效模型（含内置默认值）：
   - get-or-create 为同一供应商下的模型；即使它与 `llm_model` 同名也复用同一行，不得重复插入。
   - 根据 `process_provider` 写 `capabilities.video.transport`：
     - `gemini` -> `gemini_inline`
     - 其他 -> `openai_video_url`
   - 作为 `video_understanding.primary_model_id`，设置 `auto_configured=true`、`legacy_pending_validation=true`。
7. 残缺旧配置的升级语义必须先判定：
   - 有 Base URL 与模型名、无有效 Key：允许升级，建供应商与模型但不建 assignment、不删任何 Key、
     不写 `model_access_legacy_imported`，保留后续补 Key 再迁移的能力。
   - 无 Base URL 或模型名、有有效 Key：允许升级新表结构，但不迁移该 Key、不写完成标记、不删 Key；
     用户之后可在模型接入页手工导入。这样不会让原本发不出请求的旧安装卡死在 Alembic。
   - 无 Base URL、无模型名、无有效 Key：允许升级空结构，不写完成标记。
   - 只有 Base URL 与模型名都存在且解析出有效 Key 时，才进入删除旧 Key 的完整迁移路径。
8. 只有在完整迁移路径（Base URL、模型名、有效 Key 齐全）下，才允许写
   `system_setting["model_access_legacy_imported"] = "1"`；残缺配置一律不写，保留后续补迁。
   写入方式为 `op.get_bind()` 先 UPDATE、`rowcount=0` 时 INSERT；不使用 ORM Session，
   也不依赖特定数据库的 upsert 语法。写入必须与步骤 1–7 在同一 Alembic 事务内。
9. 删除旧明文 Key 的前置条件必须全部满足，并且与步骤 1–8 **在 Alembic 的同一个 upgrade 事务内完成**：
   - 至少一个 assignment 指向加密供应商；若不存在任何有效 Key，则不做任何 Key 删除，
     不写 `model_access_legacy_imported=1`，保留旧配置与后续补迁能力；
   - 新供应商密文可通过 `decrypt_credential` 往返校验；
   - 新表、assignment 与加密密文均已成功写入当前事务。
   有效 Key 的定义必须与运行时一致：DB `llm_api_key` 或 `settings.llm_api_key` 任一存在；
   仅“DB 没有 Key”不得当作“没有可用 Key”。满足上述条件后删除 `system_setting.llm_api_key` 明文行；
   任一条件不满足则抛异常，整个 upgrade 回滚，
   包括删除操作。不得在 `upgrade()` 内主动 commit 拆事务。
10. 降级口径：`downgrade()` 只删除新表和新列，不恢复旧 Key 明文行。回滚到旧版本前，
   运维必须先从新供应商导出并重新配置旧版凭据；文档和发布说明必须写明这一点，避免“降级即失联”。
11. 不删除旧 `llm_base_url` / `llm_model` 两个非敏感键，供只读兼容与回滚诊断使用。

迁移兼容原则：

- 旧 Key 明文一旦成功迁移即删除；不得为了回滚保留明文。
- 环境变量形式的 `settings.llm_api_key` 只作为迁移输入，不作为新运行路径的长期来源。
  迁移后供应商凭据以加密记录为准；兼容层 GET/PUT 不得再把 env Key 当运行时配置返回或使用。
  部署文档要求迁移完成后移除该环境变量，避免同一凭证存在两个事实来源。
- 运行时在存在新工作分配时优先使用新模型网关。
- 回退旧 `llm_*` 解析的判定只有一条：该 work_type 的 `primary_model_id` 为空。
  不存在任何 assignment、或某槽位未建立 assignment 时，该槽位按旧配置解析；
  一旦槽位有主模型，就只走新模型网关，不再回退。
- 旧 `/api/system/settings` 与 `/api/system/process-settings` 保留兼容层：
  - GET 尽量从新模型配置派生，避免两处显示不一致。
  - PUT 将旧字段翻译为供应商、模型或工作分配更新；Key 只写 AES-GCM 加密后的供应商记录，
    不再写回 `system_setting.llm_api_key`。兼容接口收到 Key 时按新供应商更新处理。
  - 响应或开发日志中标记 deprecated；前端新页面不再调用旧字段。

Legacy 过渡规则：

- `legacy_pending_validation` 挂在 migration 生成的 `model_assignment` 行上，不挂模型；只有该行的主模型可享受过渡放行。
- 迁移生成的 assignment 允许其主模型暂时没有可用 capabilities；运行期能力校验**只对此类 assignment**
  放行，允许按原槽位直接调用，避免升级即中断。`probe_status` 仍记录在模型上，不因过渡放行而伪造为 passed。
- 首次调用无论成功失败都写 `model_call_attempt`。成功时按真实工作补写对应 capability，并清除该 assignment 的
  `legacy_pending_validation`；失败时**保留**过渡标记，保证队列重试仍可用，不会因为一次网络超时永久锁死任务。
- 任意探测完成时：若能力满足该槽位则清除标记；若不满足则清除标记并清空该槽位主模型，回到自动推荐或旧配置回退，
  不允许一个已证实不合格的模型长期占用槽位。
- 用户手工修改该槽位（改主模型、改备用模型、恢复自动推荐）时清除 `legacy_pending_validation`，视为用户已确认。
- 过渡放行只对迁移生成的 assignment 生效，不适用于用户新导入但未探测的模型，也不适用于模型被重新分配到其他工作。

## 8. 供应商、模型与能力探测

### 8.1 供应商连接

保存供应商时不做长时间调用，只做输入校验。用户点击“测试连接”或导入模型时再拉取模型清单。

连接测试：

- OpenAI 兼容：`GET {base_url}/models`，`Authorization: Bearer <key>`。
- Anthropic：`GET {base_url}/v1/models`，`x-api-key` + `anthropic-version`。
- Gemini：`GET {base_url}/v1beta/models`，`x-goog-api-key`。
- 自定义协议允许跳过清单拉取，直接手工输入模型 ID。

Base URL 归一规则：

- OpenAI 兼容和 Anthropic 适配器接受带或不带 `/v1` 的地址，内部只归一一次，避免出现 `/v1/v1/...`。
- Gemini 预设保存站点根地址；原生端点始终从站点根构建 `/v1beta/models/...`。
- 如果用户为 Gemini 填了以 `/v1` 结尾的地址，适配器先去一次 `/v1` 后缀再拼接。

错误处理：

- 网络错误、401、403、404、429、5xx 都返回原始状态码与可读摘要。
- 错误文本必须经过 Key 脱敏。
- 连接测试结果写 `last_test_at/status/error`，但不因此自动删除供应商。

### 8.2 模型清单拉取

添加模型弹窗中：

1. 用户选择供应商预设。
2. 填访问密钥。
3. 系统调用供应商模型清单接口。
4. 前端展示可搜索、多选列表。
5. 清单不可用时展示“手动输入模型名”入口。
6. 导入完成后异步执行能力探测；探测不阻塞弹窗关闭。

清单拉取失败不应阻止手工导入。模型列表可能很大，后端只透传规范化字段：

```json
{
  "model_key": "gpt-5.4-mini",
  "display_name": "GPT 5.4 Mini",
  "declared_capabilities": {
    "text": true,
    "image": true,
    "video": false
  }
}
```

`declared_capabilities` 只用于 UI 预筛选和探测提示，不直接作为工作分配依据。

### 8.3 探测设计

探测由 worker 的 `probe_model` 任务执行，避免长网络请求阻塞 API。每次探测都会写 `model_call_attempt`。

探测项：

1. **文本与结构化输出**
   - 请求模型只输出 JSON：`{"ok": true, "echo": "workbench"}`。
   - 先尝试 `response_format={"type":"json_object"}`。
   - 如果上游明确报“不支持 response_format”，同模型去掉该参数重试一次；只要结果能解析为 JSON，则标记 `json.mode=prompt_only`。
   - 两次都不通过则 `text` 或 `json` 能力失败。
2. **图片输入**
   - 用 Pillow 生成一张确定性的纯红色小图，不把二进制探针放进仓库。
   - `pyproject.toml` 显式增加 `pillow` 依赖，不依赖 `qrcode[pil]` 的间接安装。
   - 探针必须能区分“真看图”与“纯文本猜测”：请求要求模型返回
     `{"has_image": true|false, "color": "..."}`，颜色值由探针运行时从一组颜色中随机选择。
   - 只有 `has_image=true` 且颜色与本次随机颜色一致，才标记 `image=true`；
     回答“没有图片”或颜色猜错都标记 `image=false`。
3. **视频输入**
   - 用 ffmpeg 生成约 2 秒的确定性测试视频：前 1 秒纯红，后 1 秒纯蓝。
   - 分别按模型的视频 transport 发送：
     - `openai_video_url`：OpenAI 兼容 `video_url` data URI。
     - `gemini_inline`：Gemini 原生 `inlineData`。
   - 请求模型返回 JSON：`{"first": "...", "second": "..."}`。
   - 只有两段颜色都正确，才标记 `video=true`。这能区分“真正看了视频”和“只收到文件后幻觉”。
   - ffmpeg 不可用时标记 `skipped`，错误为“未安装 ffmpeg，无法生成视频探针”，并允许手工标记。

视频探测费用提示：

- 添加模型弹窗提供单个复选框：“检测视频能力（会产生极少量调用费用）”。
- 默认勾选；批量导入时展示将检测的模型数量，用户可取消。
- 未勾选时视频能力为 `untested`，不会被自动推荐到视频理解槽位。
- 列表行提供“检测视频”按钮，用户可随时补测。

人工覆盖：

- 视频探测失败或跳过时，允许用户手工标记“我确认该模型支持视频”。
- 手工标记必须写 `source=manual_override`，列表和详情持续显示“手工标记，未实测”。
- 运行期若视频调用失败，失败原因照常记录，不隐藏“未实测”的风险提示。

### 8.4 能力与工作槽位的匹配

| 槽位 | 最低能力 |
|---|---|
| `text_analysis` | `capabilities["text"].value=true` 且 `capabilities["json"].value=true` |
| `cover_classify` | `capabilities["text"].value=true` 且 `capabilities["json"].value=true` |
| `video_understanding` | `capabilities["json"].value=true` 且 `capabilities["video"].value=true` |

约束：

- 能力判定只有唯一入口 `capabilities_of(model) -> dict[str, Capability]`；所有 API、推荐、分配校验必须经它读取，禁止在业务代码里直接拼 `capabilities.get("video")` 当布尔值用。
- 能力对象是 `{value, source, checked_at, detail, ...}`；简写 `text=true` 只用于文档表达，不落库、不返回。
- 自动推荐只考虑满足最低能力且已启用的模型。
- 工作分配下拉框只展示满足最低能力的模型。
- 封面判类的图片能力是**运行期按需要求**：全局或本次任务 `use_cover=true` 时，执行前校验
  `capabilities["image"].value=true`；不满足则以 `unsupported_capability` 切备用或失败。
  `use_cover=false` 时纯文本模型可正常承担该工作，不因缺图片能力被禁止分配。
- 用户手工覆盖视频能力后，该模型可用于视频理解槽位。
- 若工作槽位没有可用主模型，卡片显示“未配置”，任务运行前给出可读阻断错误，不允许静默回退到其他工作槽位。

## 9. 运行期调用与主备切换

### 9.1 统一调用入口

新增服务：

```python
async def call_with_fallback(
    db: Session,
    *,
    work_type: str,
    operation: str,
    request: dict,
    parse_result: Callable[[dict | str], dict | str],
    validate_result: Callable[[dict | str], None] | None = None,
    asset_id: int | None = None,
    job_id: int | None = None,
) -> ModelCallResult
```

`call_with_fallback` 由 `model_access_service` 对象暴露，不直接依赖模块级 `SessionLocal`：

```python
class ModelAccessService:
    def __init__(self, *, attempt_session_factory: Callable[[], Session] | None = None):
        self._attempt_session_factory = attempt_session_factory or SessionLocal
```

- 生产默认使用 `app.db.SessionLocal`。
- 测试通过依赖覆盖或服务构造参数注入绑定临时 engine 的 sessionmaker，禁止审计写入真实 PG。
- 业务代码只调用服务方法，不得自行 `from app.db import SessionLocal` 写 attempt。

职责：

1. 解析工作分配，拿到主模型与备用模型。
2. 校验供应商启用状态、模型启用状态与最低能力。供应商或模型停用时都视为该模型不可用；
   配置不合法时直接抛出可读错误，不发起外部请求。
3. 用 `attempt_group_id` 标记本次调用。
4. 先调用主模型；无论成功失败都写 `model_call_attempt`。
5. 收到响应后先执行 `parse_result`，再执行可选的 `validate_result`。解析或校验失败时，
   本次 attempt 归类为 `invalid_json` / `schema_mismatch`，不得视为调用成功。
6. 主模型失败且存在备用模型时，调用备用模型一次；备用响应同样经过解析与校验。
7. 主备都失败时抛出结构化 `ModelCallError`，内容包含两段尝试摘要。
8. 返回 `ModelCallResult`：

```python
@dataclass
class ModelCallResult:
    data: dict | str             # 已经过 parse_result / validate_result 的合格结果
    used_model_id: int
    used_model_key: str
    used_role: str              # primary / fallback
    fallback_used: bool
    primary_model_id: int | None
    primary_model_key: str
    fallback_model_id: int | None
    fallback_model_key: str
    primary_error_class: str
    primary_error_message: str
    fallback_error_class: str   # 未使用备用或备用成功时为空串
    fallback_error_message: str # 未使用备用或备用成功时为空串
    attempt_group_id: str
    usage: dict
```

`ModelCallResult` 只在至少一个模型成功时返回。主备都失败时抛出的 `ModelCallError` 必须携带：

```python
class ModelCallError(Exception):
    attempt_group_id: str
    primary_model_id: int | None
    primary_model_key: str
    fallback_model_id: int | None
    fallback_model_key: str
    primary_error_class: str
    primary_error_message: str
    fallback_error_class: str
    fallback_error_message: str
```

这样判类 best-effort 仍能拿到两次失败的稳定分类与原始摘要。

为避免业务模块各自拼 JSON，落库统一使用 `RoutingStage`：

```python
class RoutingStage(TypedDict):
    used_model_id: int | None
    used_model_key: str
    used_role: str
    fallback_used: bool
    primary_model_id: int | None
    primary_model_key: str
    fallback_model_id: int | None
    fallback_model_key: str
    primary_error_class: str
    primary_error_summary: str
    fallback_error_class: str
    fallback_error_summary: str
    attempt_group_id: str
```

`routing` 顶层类型为 `Partial[Record[WorkType, RoutingStage]]`，即“只包含实际执行过的阶段”。

Preflight 失败（主备都不可用、未发起任何请求）的契约：

- 不写 `model_call_attempt`。
- 若该阶段有配置过主模型，即使模型已停用/被删，也写
  `RoutingStage(primary_model_id=<配置值或 None>, primary_model_key=<快照或 "">,
  primary_error_class="disabled" 或 "unsupported_capability", used_model_id=None,
  used_model_key="", fallback_used=False)`。
- 若该阶段连模型 ID 都没有（完全未配置），不写 `routing[work_type]`；任务直接以“该工作没有可用模型”失败。
- 前端必须先读任务错误；`routing` 只用于解释“为什么某个已配置模型没有执行”。

- `ModelCallResult.to_routing() -> RoutingStage` 基于成功结果生成阶段。
- `ModelCallError.to_routing() -> RoutingStage` 基于两次失败生成阶段，`used_model_id=None`、
  `used_model_key=""`。
- 业务模块只调用这两个方法并把结果放进 `routing[work_type]`，不得手写字典。
- `*_error_summary` 是对 `*_error_message` 做脱敏后截断到 512 字符的短文本；
  完整原始报错只从 `model_call_attempt.error_message` 读取。

### 9.2 备用切换规则

按用户确认的策略执行：**任何拿不到合格结果的失败都切一次**。

包括但不限于：

- 网络超时、连接失败。
- HTTP 429。
- HTTP 5xx。
- 认证或权限错误。
- 请求参数不被上游支持。
- 上游返回空内容。
- 返回内容无法解析为要求的结构。
- 结构化结果校验失败。
- 能力不匹配，例如视频被模型忽略或图片输入被拒绝。

切换边界：

- 只切一次，不递归。
- 备用模型失败后直接抛任务失败，由现有队列重试机制处理，不再横向尝试其他模型。
- 主模型失败但备用成功时，任务视为成功，但结果标记 `fallback_used=true`。
- 不得因为重试、队列重跑或备用切换重复写入同一业务结果；结果表仍按现状 upsert。

### 9.3 失败分类

`error_class` 枚举：

| 分类 | 含义 |
|---|---|
| `timeout` | 连接或读取超时 |
| `network` | DNS、连接、TLS 等网络失败 |
| `auth` | 401 / 403 或供应商明确拒绝凭据 |
| `rate_limit` | 429 |
| `server` | 5xx |
| `bad_request` | 请求参数或协议不被支持 |
| `unsupported_capability` | 图片/视频/JSON 能力不满足 |
| `not_configured` | 工作槽位完全未配置模型，未发起请求 |
| `disabled` | 供应商或模型已停用，未发起外部请求 |
| `empty_response` | 上游成功但无有效内容 |
| `invalid_json` | 返回内容无法解析为 JSON |
| `schema_mismatch` | JSON 可解析但关键字段不符合要求 |
| `cancelled` | 上游取消或任务被中止 |
| `unknown` | 未归类错误 |

`disabled` 的审计语义：

- 被停用的模型不会发起外部请求，因此**不写 `model_call_attempt`**。
- 若主模型停用、备用模型成功，结果对应阶段写
  `routing[work_type].primary_error_class=disabled`、`routing[work_type].fallback_error_class=""`；
  `model_call_attempt` 只有备用模型那一行。
- 若主备都停用，任务在发起任何请求前失败，结果/任务错误写对应阶段的
  `routing[work_type].primary_error_class=disabled`，
  同样不产生 attempt。

### 9.4 结果落库

现有结果表新增一个 JSON 字段 `routing`：

- `analysis_result.routing`
- `video_understanding.routing`
- `viral_breakdown.routing`

`routing` 结构按工作分阶段，避免视频理解里“判类调用”和“视频理解调用”互相覆盖。
顶层 key 限定为三个工作类型，未执行的阶段直接省略；每个阶段结构一致：

```json
{
  "cover_classify": {
    "used_model_id": 10,
    "used_model_key": "qwen3.8-flash",
    "used_role": "primary",
    "fallback_used": false,
    "primary_model_id": 10,
    "primary_model_key": "qwen3.8-flash",
    "fallback_model_id": null,
    "fallback_model_key": "",
    "primary_error_class": "",
    "primary_error_summary": "",
    "fallback_error_class": "",
    "fallback_error_summary": "",
    "attempt_group_id": "mg_classify_xxx"
  },
  "video_understanding": {
    "used_model_id": 12,
    "used_model_key": "qwen3.8-flash",
    "used_role": "fallback",
    "fallback_used": true,
    "primary_model_id": 11,
    "primary_model_key": "deepseek-v4-flash",
    "fallback_model_id": 12,
    "fallback_model_key": "qwen3.8-flash",
    "primary_error_class": "timeout",
    "primary_error_summary": "读取超过 90 秒",
    "fallback_error_class": "",
    "fallback_error_summary": "",
    "attempt_group_id": "mg_understand_xxx"
  }
}
```

分阶段落库规则：

- 顶层类型等价于 `Partial[Record[WorkType, RoutingStage]]`，不是固定包含三段。
- `analysis_result.routing`：只写 `text_analysis` 阶段。
- `viral_breakdown.routing`：只写 `text_analysis` 阶段。
- `video_understanding.routing`：判类执行过就写 `cover_classify` 阶段；视频理解执行过就写
  `video_understanding` 阶段；两个阶段各自有独立 `attempt_group_id`，不得合并成一条。

失败兜底时该阶段没有“实际使用模型”，`used_model_id` 必须写 `null`，`used_model_key` 必须写空字符串；
前端与类型定义都按可空处理，禁止用 0 或 `"unknown"` 之类哨兵值冒充真实模型。

`routing` 的 `fallback_error_class` / `fallback_error_summary` 在备用未使用或备用成功时为空；
主备都失败时两者都必须写入，供判类 best-effort 结果展示两次失败原因。
`model` 字段继续保存实际使用模型名，保持现有 API 与前端局部兼容；迁移会把三个结果表的该字段扩到 200 字符。
完整原始报错从 `model_call_attempt` 读取，不让结果表无限膨胀。

### 9.5 业务模块改造

四个调用点改为声明工作类型：

| 模块 | work_type | operation | 输入 | 输出要求 |
|---|---|---|---|---|
| `analysis.py` | `text_analysis` | `analyze` | 标题 + 转录全文 | 摘要、标签、结构化结果 JSON |
| `breakdown.py` | `text_analysis` | `breakdown` | 元数据 + 转录 + 理解 + 评论 + 确定性数字 | 拆解报告 JSON |
| `classify.py` | `cover_classify` | `classify` | 标题、作者、时长、转录；可选封面图 | 类型、置信度、理由 JSON |
| `vision.py` | `video_understanding` | `understand_video` | 视频 + 提示词 | 多维理解 JSON |

这些模块不再读取 `settings.llm_*` 或 `process_*` 模型字段，只接收网关返回的 `ModelCallResult`。

判类失败隔离保持不变：

- `cover_classify` 是视频理解链路的 best-effort 前置步骤；主备模型都失败时，`understand_video` 不因此整单失败。
- 失败时 `run_understand` 记录 `category=other`、`category_confidence=0`，理由包含主模型失败分类；
  `model_call_attempt` 照常保留两次尝试，供用户查看原始报错。
- 分类调用成功时把实际模型写入 `video_understanding.routing.cover_classify`；主备都失败时该阶段
  `used_model_id=null`、`used_model_key=""`、`fallback_used=false`，并保留 `primary_error_class` /
  `fallback_error_class` 供前端展示，不能覆盖 `video_understanding` 阶段。
- `analysis` 与 `breakdown` 不做 best-effort：主备都失败时任务失败，按现有队列重试。

## 10. 自动推荐策略

### 10.1 触发时机

自动推荐在以下时机运行：

- 新模型完成能力探测后。
- 已有模型被删除或停用导致槽位为空时。
- 用户点击“恢复自动推荐”时。

### 10.2 推荐规则

按工作槽位过滤满足最低能力的启用模型，再按以下优先级排序：

1. 能力来源为 `probe` 且全部通过。
2. 能力来源为 `manual_override`。
3. 最近探测通过且没有最近失败记录的模型。
4. 导入时间较早的模型。

推荐结果：

- 只填充空槽位的 `primary_model_id`。
- 不自动填写备用模型。
- 不覆盖 `auto_configured=false` 的人工配置。
- 填充后设置 `auto_configured=true`；用户任一手工修改后置 false。

状态判定必须无歧义：

- 可自动写入的条件只有一个：`primary_model_id IS NULL`。
- `auto_configured` 只用于界面展示“已自动配置 / 已手动配置”，不能作为是否允许写入的唯一判据。
- 即使 `auto_configured=true`，只要 `primary_model_id` 非空，普通推荐不得改写。
- “恢复自动推荐”是唯一显式例外：用户点击后弹出确认，确认后清空三个槽位的主备配置并重新推荐；
  这是用户主动放弃人工配置，不属于自动覆盖。
- 删除或停用模型导致主模型为空时，只允许重新填充 `primary_model_id`，不得触碰备用模型。

不参与排序的因素：

- 模型价格：当前导入流程不保证能获得可靠价格。
- 模型名称猜测：不能以名字推断能力。
- 供应商推荐：模型接入页展示星算AI推荐入口和预设，但不强制、不锁定；工作要求仍按真实探测结果匹配。

## 11. API 设计

新增路由前缀：`/api/model-access`。遵循“API 即产品”红线，不使用仅网页可做的管理员 session 单轨，
而是与现有 `skills.py` 保持一致的双轨鉴权：

| 接口类别 | 前端管理员会话 | 外部 API Key scope |
|---|---|---|
| 只读：`GET /providers`、`/models`、`/assignments`、`/calls`、`/calls/{group}` | 允许 | `read` / `operate` / `admin` 任一 |
| 普通写与探测：模型导入、模型启停/改名、探测、手工覆盖、分配更新、测试连接 | 允许 | `operate` / `admin` |
| 安全敏感写：新增供应商、修改 `base_url` / `protocol` / 访问密钥、删除供应商、删除模型 | 允许 | 仅 `admin` |

`operate` Key 不得修改既有供应商的连接目标或凭据。否则持有 `operate` 的攻击者可以把 Base URL
改成自己的地址，再触发一次普通分析任务，平台就会解密并外发已保存的供应商访问密钥。

实现契约：新增组合依赖 `require_admin_or_scope(*needed)`，不能把 `require_admin` 与 `require_scope`
同时挂在一条路由上（FastAPI 的多个 `Depends` 是 AND 关系，会让浏览器和 API Key 都失败）。

- 凭据通道识别做成纯函数 `resolve_auth_channel(request, db) -> AuthContext`，按以下顺序：
  1. 读取 `Authorization: Bearer <token>`，同时读取 `xsai_session` Cookie。
  2. 若 Bearer 以 `sk-` 开头，且能在 `api_key` 表按哈希命中且启用：判为 API Key 通道。
     管理员 session Cookie 对 API Key 通道**永远不构成冲突，直接忽略**，因为单管理员模型下 Cookie
     只代表平台管理员，不携带可与外部 Key 比较的第二身份。API Key 通道按自身 scope、限流执行。
  3. 若 Bearer 不以 `sk-` 开头，或未命中 API Key 表，则把 Bearer 视为管理员 session token；
     若 Bearer 无效但 `xsai_session` Cookie 有效，也按管理员 session 放行。
  4. 前端登录后的正常请求天然同时带 Bearer session token 和同名 session Cookie，两者是同一个 token，
     必须视为同一身份，不返回冲突。
  5. Bearer 命中 API Key 表不作为 session 的兜底：session token 不允许回退按 API Key 解析。
     增加负向测试：伪造一个恰好哈希命中 API Key 的 session token 时，应按通道识别规则处理，不越权。
- 冲突判定只覆盖“多个不同 API Key”或其它无法唯一确定通道的畸形请求；管理员 Cookie 不参与冲突判定。
- 无有效凭据返回 401；凭据有效但 scope 不足返回 403。
- `resolve_auth_channel` 只做鉴权并返回 `AuthContext`（`kind=admin/api_key`、`key_id`、`scopes`），
  不写业务日志。路由层复用统一 helper `log_model_access_call(...)` 写 `api_call_log`，
  状态用 `ok/error/denied`，并记录路由名与耗时；管理员 session 通道不写 API Key 日志。
- API Key 通道必须完整复用现有 `app.core.api_auth._check_rate(key)` 与 `last_used_at` 更新语义：
  超限返回 429，成功调用更新最后使用时间。`resolve_auth_channel` 或统一 helper 承接同等行为，
  不得因为换成组合依赖而绕过实体 Key 的 `rate_limit_per_min`。
- API Key 前缀沿用现有体系 `sk-`，不引入 `xsai_` 新前缀，保证与 `/api/ai/keys` 创建出的 Key 直接兼容。
- 所有响应与参数模型不因鉴权通道不同而分叉；外部客户端与前端能力对等，只受 scope 限制。

### 11.1 供应商

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/providers` | 供应商列表，Key 脱敏 |
| `POST` | `/providers` | 新增供应商 |
| `PATCH` | `/providers/{id}` | 修改名称、地址、Key、启用状态 |
| `DELETE` | `/providers/{id}` | 删除空供应商；有导入模型时拒绝 |
| `POST` | `/providers/{id}/test` | 连接测试 |
| `POST` | `/providers/{id}/discover-models` | 拉取可导入模型清单 |

响应示例：

```json
{
  "id": 3,
  "name": "我的 OpenAI",
  "preset_key": "openai",
  "protocol": "openai",
  "base_url": "https://api.openai.com/v1",
  "has_api_key": true,
  "api_key_masked": "sk-a****z9",
  "enabled": true,
  "last_test_status": "ok",
  "last_test_at": "2026-10-10T12:00:00Z"
}
```

供应商更新规则：

- 修改 `name`、`enabled` 或仅轮换 Key：不改变 `config_revision`，已有探测结果继续有效；
  轮换 Key 不自动清空能力，因为能力由模型与协议决定，不由配额决定。
- 修改 `base_url` 或 `protocol`：必须递增 `provider.config_revision`，并把其下所有模型的
  `probe_status` 重置为 `untested`、`probed_at` 置空、清除 `legacy_pending_validation`。
  旧的能力详情可保留作历史，但 `capabilities_of()` 因版本不匹配必须返回不可用；`manual_override`
  与普通 `probe` 一样失效，用户需在新连接配置上重新确认。
- 停用供应商不删模型、不清能力；重新启用后，若连接配置未变，旧探测结果仍可按版本匹配生效。
- 前端在供应商停用、配置版本变化后必须显示“需重新检测”，不得继续展示为可直接使用。

### 11.2 模型

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/models` | 已导入模型列表，支持供应商、能力、工作筛选 |
| `POST` | `/models/import` | 从清单批量导入，或手工导入 |
| `PATCH` | `/models/{id}` | 修改展示名、启用状态 |
| `DELETE` | `/models/{id}` | 删除未参与工作分配的模型 |
| `POST` | `/models/{id}/probe` | 重新探测；可指定是否包含视频 |
| `POST` | `/models/{id}/manual-capability` | 手工覆盖视频能力 |

导入请求：

```json
{
  "provider_id": 3,
  "models": [
    {"model_key": "gpt-5.4-mini", "display_name": "GPT 5.4 Mini"}
  ],
  "probe": {
    "text": true,
    "image": true,
    "video": true
  }
}
```

手工覆盖请求必须写全能力字段：

```json
{
  "video": true,
  "transport": "openai_video_url",
  "reason": "自有网关已实测支持视频"
}
```

- 服务端写入：`value`、`source=manual_override`、当前 `provider.config_revision`、`probe_version`、
  `checked_at`、`transport`、由 `transport` 派生的 `auth_style`、`detail`、用户 `reason`。
- `transport` 仅支持 `openai_video_url` / `gemini_inline`；未提供时按 `provider.protocol` 归一：
  `gemini` -> `gemini_inline`，其余 -> `openai_video_url`。高级选项允许覆盖这一默认。
- `provider.config_revision` 变化后，手工覆盖与探测结果一起判定为 stale，需要用户重新确认。

模型响应示例：

```json
{
  "id": 12,
  "provider_id": 3,
  "provider_name": "我的 OpenAI",
  "model_key": "gpt-5.4-mini",
  "display_name": "GPT 5.4 Mini",
  "enabled": true,
  "capabilities": {
    "text": {"value": true, "source": "probe"},
    "json": {"value": true, "source": "probe", "mode": "json_mode"},
    "image": {"value": true, "source": "probe"},
    "video": {"value": false, "source": "probe"}
  },
  "assigned_work_types": ["text_analysis"],
  "probe_status": "passed",
  "probed_at": "2026-10-10T12:00:00Z"
}
```

### 11.3 工作分配

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/assignments` | 返回三个槽位及可选模型 |
| `PUT` | `/assignments/{work_type}` | 设置主模型与备用模型 |
| `POST` | `/assignments/recommend` | 只填空槽位，不覆盖人工配置 |

更新请求：

```json
{
  "primary_model_id": 12,
  "fallback_model_id": 15
}
```

服务端校验：

- `work_type` 必须是三个固定值之一。
- 主模型不能等于备用模型。
- 两个模型都必须启用并满足槽位最低能力。
- `fallback_model_id` 可为 `null`。

### 11.4 调用审计

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/calls` | 按工作、模型、素材、状态分页查询调用尝试 |
| `GET` | `/calls/{attempt_group_id}` | 一次业务调用下所有主备尝试 |

返回内容包含完整 `error_message`，前端默认折叠，展开后不截断。

### 11.5 素材详情补充

新增：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/assets/{asset_id}/model-calls` | 该素材涉及的模型调用与主备尝试 |

素材详情页展示：

- 成功：`本次由 <模型> 完成`。
- 发生降级：`主模型 <模型> 失败：<分类摘要>`，可展开原始报错。
- 失败：显示最终错误与两次尝试的完整记录。

## 12. 前端交互

新增一级导航：**模型接入**。页面分三个页签：**已导入模型 / 工作分配 / 调用审计**。

### 12.1 已导入模型

默认页签。顶部只有一个主按钮：**添加模型**。

列表列：

| 列 | 内容 |
|---|---|
| 模型 | 展示名；次级小字显示模型 ID |
| 供应商 | 供应商名称 |
| 能力 | “文字 / 看图 / 看视频”标签；手工标记用不同颜色并带说明 |
| 当前工作 | 用工作名展示，不用内部 work_type |
| 状态 | 已启用 / 已停用 / 检测中 / 检测失败 |
| 操作 | 检测、编辑、删除 |

操作条保留“供应商管理”入口，用于编辑 Base URL/协议/密钥、启用停用和删除供应商。该入口不新增概念：
供应商是模型的来源，只有在模型来源或密钥错误时才需要进入；常规导入仍然是在“添加模型”弹窗里完成。
修改 Base URL 或协议后，该供应商下的模型能力立即变为“需重新检测”。

展开行显示：

- 协议与 Base URL。
- 最近探测时间与详细结果。
- 原始错误摘要。
- 导入来源。
- 手工标记说明。

空状态：

- 文案：“还没有导入模型。先添加一个模型，系统会自动检测它能做什么。”
- 主按钮：“添加模型”。

### 12.2 添加模型

三步弹窗：

1. **选择供应商**
   - 预设以卡片或分组下拉展示。
   - 默认带出 Base URL。
   - “自定义”需要用户填写 Base URL，并默认 OpenAI 兼容协议；协议选择折叠在高级选项。
2. **填写访问密钥**
   - 唯一必填项。
   - Base URL 默认折叠在“高级选项”。
   - 提供“测试连接”。
3. **选择模型**
   - 自动拉取清单。
   - 搜索 + 多选。
   - 拉取失败时展示“手动输入模型名”。
   - 底部有一个复选框：“检测视频能力（会产生极少量调用费用）”，默认勾选，并显示将检测的模型数量。
   - 导入按钮文案：“导入并检测”。

探测异步进行，弹窗关闭后列表立即出现模型行，状态显示“检测中”。
若用户只在弹窗中测试连接或拉取清单、尚未导入模型就取消，前端会删除本次会话刚创建的空供应商，
避免遗留不可见的无效记录；已经导入模型的供应商不会被取消动作删除。

### 12.3 工作分配

三张卡片：

```text
文字分析
读文字，输出摘要、标签或结构化拆解
主模型：[下拉]
备用模型（可选）：[下拉]
状态：已自动配置 / 已手动配置 / 未配置
```

要求：

- 下拉框只出现满足该工作最低能力的模型。
- 默认由系统自动填充主模型。
- 用户改任一项后状态变为“已手动配置”。
- 提供“恢复自动推荐”，只在用户主动点击时重新推荐。
- 备用模型默认留空，用户明确选择后才启用切换。

在卡片底部用一行小字解释备用模型行为：

> 主模型失败时自动尝试备用模型；失败原因和原始报错会保留。

### 12.4 系统设置调整

- 原“系统设置 → 大模型”页签移除模型配置，改为指向“模型接入”的快捷入口。
- “视频理解”页签移除 `provider`、`vision_model`、`classify_model` 三个技术字段，只保留门控、提示词、类型目录和执行开关。
- 旧接口继续兼容，但前端不再依赖旧模型字段。
- 新增一级路由与导航入口要同时改三处：`web/src/router.ts`、`web/src/layout/Layout.vue`、
  `web/src/pages/Settings.vue` 的模型快捷入口；只改设置页会留下“没有入口”的半成品。

### 12.5 调用审计

调用审计按“每次真实外部模型请求一行”展示，不把主备两次尝试合并成一行：

- 默认只展示时间、工作、模型、素材、主备角色、状态和耗时。
- 支持按工作、状态、模型、素材筛选；分页由后端 `X-Total-Count` 提供总数。
- 展开行展示调用分组、操作、HTTP 状态、任务 ID、错误分类、usage 和不截断的原始报错。
- 被停用而未发起外部请求的模型不会出现在列表里，这类原因在结果路由和任务错误中展示。
- 列表接口仍保持数组响应，分页总数通过 `X-Total-Count` 响应头返回，兼容已有 API 调用方。

## 13. 安全与隐私

1. 供应商访问密钥一律使用 `app.core.crypto` AES-GCM 加密入库。
2. 所有 API 响应只返回 `has_api_key` 与 `api_key_masked`，绝不返回明文或密文。
3. 日志与 `model_call_attempt.request_meta` 不得包含：
   - 供应商访问密钥。
   - Authorization / x-api-key 等鉴权头。
   - 完整请求体中的视频 base64、图片 base64。
4. 错误文本写库前执行 Key 脱敏；如果供应商回显了 Key，必须替换为 `[REDACTED]`。
5. 自定义 Base URL 只允许 `http` / `https`。平台是单管理员模式，允许局域网和本机模型服务。
6. 删除供应商或模型不得影响历史 `model_call_attempt`：快照字段保留可读信息，FK 置空。
7. 停用模型等于“不再用于新调用”；已完成的业务结果不重写。

## 14. 队列与并发

新增 `probe_model` job：

- payload：`{"model_id": 12, "probe": {"text": true, "image": true, "video": true}}`
- worker 调用 `probe_model_service`。
- 探测超时按单项控制：文本 30s、图片 45s、视频 300s。
- 某个探测项失败不回滚其他已通过项；整体 `probe_status` 可为 `partial`。
- 同一模型的探测并发去重不能只靠 `queue.enqueue` 的 payload 全等比较。实现必须二选一：
  - 在 `ai_model` 上加探测锁列（如 `probe_lock_token` / `probe_lock_at`），领取前原子获取；
  - 或新增 `probe_model:<model_id>` 的稳定精确去重键，并由仓储层在事务内串行化。
  重复点击同模型只返回已有 queued/running 任务；不同探测范围的请求合并为并集，不允许两个
  probe job 并发覆盖 `capabilities` 与 `probe_status`。
- 模型删除时，未执行的探测任务在 handler 中发现模型不存在即安全退出。

运行期模型调用：

- 不新增独立 job 类型。现有 `analyze` / `understand_video` / `breakdown` 在 worker 内调用模型网关。
- `classify` 是 `understand_video` 的内部步骤，不单独入队。
- 模型网关不持有长期数据库事务；网络调用期间不占用队列锁之外的事务。
- `model_call_attempt` 的生命周期必须独立于业务事务：
  1. attempt 写入/更新必须通过 9.1 注入的 `attempt_session_factory()` 创建独立 session，不得复用 handler
     传入的业务 `db`；否则业务层 `db.rollback()` 会连带回滚审计。每个短事务完成后立刻 close。
     生产默认 factory 是 `app.db.SessionLocal`；测试/本地通过服务构造参数覆盖，禁止在调用点硬编码。
  2. 调用开始前用该独立 session 写一行 `status=running`、`finished_at=NULL`。
  3. 单次外部请求结束后立即用另一个独立 session 更新为 `ok` / `error`，同时写
     `finished_at=now()`、`error_class`、原始报错、耗时、usage。
  4. 主模型失败、备用调用、业务解析失败、业务层后续 `db.rollback()` 都不得删除或回滚 attempt。
  5. 业务层最终 `db.commit()` / `db.rollback()` 只影响业务结果与状态表，不参与 attempt 的提交边界。
  6. 进程崩溃遗留的 `running` attempt 由 worker janitor 超时置为 `error`，`error_class=unknown`，
     `error_message="任务中断，未收到模型响应"`。
     回收判据：`status=running` 且 `created_at` 早于“单项最大超时 + 60 秒安全余量”（视频 360 秒、
     图片 105 秒、文本 90 秒）；实际以 `finished_at` 为空且 `created_at` 超阈值为准，避免误杀仍在
     正常长调用的视频请求。回收后写 `finished_at`。
- `model_call_attempt.job_id` 不能依赖业务 payload 自带：当前 `app/workers/runner.py` 调用
  `handler(db, job.payload)`，handler 拿不到队列行。实现时必须把分发签名扩展为
  `handler(db, payload, job)`（或等价地注入不可变的 `_job_id`），把真实 `JobQueue.id`
  传给模型网关；不得伪造或在 payload 中让调用方传入。

## 15. 错误处理与用户可见反馈

| 场景 | 用户看到 | 系统行为 |
|---|---|---|
| 供应商连接失败 | “连接失败：HTTP 401，请检查访问密钥” | 保存错误摘要，不保存明文 Key |
| 模型清单拉取失败 | “无法读取模型清单，可手动输入模型名” | 允许继续导入 |
| 文本探测失败 | 能力标签灰显，详情显示原始错误 | 不推荐到文字工作 |
| 图片探测失败 | “不支持看图” | 不推荐到封面判类 |
| 视频探测失败 | “未通过视频检测” | 不推荐到视频理解 |
| 用户手工标记视频 | “手工标记，未实测” | 允许分配，但风险提示常驻 |
| 槽位未配置 | “尚未选择模型” | 任务入队前或运行前给出可读阻断错误 |
| 主模型已停用、备用可用 | “主模型已停用，已使用备用模型” | 不调用停用模型，直接使用备用 |
| 主备都不可用 | “该工作没有可用模型” | 不发起外部请求，不产生调用记录 |
| 主模型失败、备用成功 | “已由备用模型完成；主模型失败原因可查看” | 结果成功，标记 fallback |
| 主备均失败 | “主模型和备用模型都失败” | 任务按现有队列重试规则处理 |

## 16. 测试与验收

### 16.1 后端单元测试

新增或扩展：

- `tests/test_model_access_models.py`：四张表、唯一约束、级联/置空行为。
- `tests/test_model_access_provider_lifecycle.py`：停用供应商后模型不可用；改 base_url/protocol 后旧能力失效并需重测。
- `tests/test_model_access_api.py`：供应商 CRUD、Key 不回显、模型导入、分配校验。
- `tests/test_model_access_auth.py`：管理员 session 与 API Key 双轨；只读 scope 可读不可写；
  `operate` 可导入/探测，但不能改供应商 Base URL/协议/凭据、不能删除；安全敏感操作仅 `admin`；
  前端同源 Bearer session + Cookie 不误判冲突；API Key 与第三方身份冲突返回 403；
  管理员 Cookie 不参与 API Key 通道冲突；API Key 超限返回 429 且成功调用更新 `last_used_at`；
  伪造 session token 不会因哈希命中被当成 API Key；确认不存在 web-only 管理接口。
- `tests/test_model_protocols.py`：OpenAI / Anthropic / Gemini 请求体与响应解析。
- `tests/test_model_probe.py`：文本、图片、视频探测的成功、失败、跳过、手工覆盖。
- `tests/test_model_routing.py`：主模型成功、主失败备成功、主备均失败、无分配、能力不匹配。
- `tests/test_model_migration.py`：旧 `llm_*` 与 `process_*` 迁移、幂等、回滚。
- `tests/test_model_migration_defaults.py`：`process_classify_model` / `process_vision_model` 未显式落库时
  仍按内置默认值迁移两个槽位，不漏 assignment。
- `tests/test_process_settings_rows.py`：纯函数行映射解析与 `get_process_settings(db)` 结果完全一致。
- `tests/test_model_migration_env.py`：DB 缺失但环境变量有 Base URL / Key / 模型时的解析与迁移。
- `tests/test_model_migration_atomicity.py`：Alembic 单事务内密文校验失败时不删除旧 Key、整体回滚。
- `tests/test_model_migration_crypto_ready.py`：迁移前加密密钥缺失时不自动生成新 key；
  旧 Key 保持原样、迁移失败。只有显式配置环境变量或已有 keyfile 时才允许写入密文。
- `tests/test_model_migration_protocol.py`：`process_provider=gemini` 不改写文本/判类对话协议，只影响视频 transport。
- `tests/test_model_protocols.py` 必须覆盖 `provider.protocol=openai` + `video.transport=gemini_inline`
  的视频鉴权使用 `x-goog-api-key`，而不是 Bearer。
- `tests/test_model_migration_get_or_create.py`：视觉模型与文本模型同名时复用同一行，不违反唯一约束。
- `tests/test_model_migration_legacy_validation.py`：legacy assignment 未探测时可过渡、调用失败保留标记、成功或探测后按规则清除。
- `tests/test_model_routing_parser.py`：主模型 200 但 JSON 不合法 / schema 不匹配时切备用。
- `tests/test_model_routing_attempt_lifecycle.py`：业务层回滚后 attempt 的 running→error 仍保留；备用失败字段完整。
- `tests/test_model_access_attempt_session.py`：注入临时 sessionmaker 后 attempt 写入内存 SQLite，
  不触碰真实 `SessionLocal`，证明测试与本地开发环境不会连生产库。
- `tests/test_model_routing_classify_best_effort.py`：判类主备都失败仍回退 other，理解任务不失败。
- `tests/test_model_probe_concurrency.py`：不同探测范围并发请求不会互相覆盖。
- `tests/test_analysis_fallback.py`、`tests/test_vision_fallback.py`、`tests/test_breakdown_fallback.py`：
  验证业务模块经统一网关调用并落 `routing`。

### 16.2 前端验证

- `cd web && npm run build`
- 模型接入页手工检查：
  - 空状态。
  - 添加模型三步弹窗。
  - 模型清单搜索和多选。
  - 探测中状态。
  - 能力标签和展开详情。
  - 工作分配三张卡片。
  - 主备模型下拉只出现合格模型。

### 16.3 端到端验收（假供应商）

用本地 `httpx.MockTransport` 或测试 HTTP 服务，不消耗真实模型费用：

1. 创建供应商并拉取模型清单。假供应商也必须走真实 `encrypt_credential` 入库；
   运行时通过真实 `decrypt_credential` 取出，断言请求头使用了解密后的访问密钥。
2. 导入两个模型。
3. 探测通过文字、图片、视频能力。
4. 将模型 A 设为主模型，模型 B 设为备用。
5. 让模型 A 返回 500，模型 B 返回合法 JSON。
6. 触发分析任务，断言：
   - 任务成功。
   - 结果 `model` 是模型 B。
   - 结果 `routing.text_analysis.fallback_used=true`（`routing` 顶层按工作类型分阶段，不读取扁平字段）。
   - `model_call_attempt` 有两行。
   - 主模型行 `status=error`、`error_class=server`、原始报错完整。
   - 两个 attempt 的 `provider_name` / `model_key` 快照完整，且任何响应或日志中都没有明文 Key。
7. 让主备都失败，断言任务进入现有失败重试链路。
8. 单独验证停用场景：主模型停用、备用成功时只有一行 attempt，结果对应阶段
   `routing[work_type].primary_error_class=disabled`、`fallback_error_class=""`；
   实现不得为停用模型伪造调用行。

### 16.4 安全验收

- 任意模型/供应商 API 响应中搜索不到明文 Key。
- `model_call_attempt` 与日志中搜索不到 Key。
- 数据库 `ai_provider.api_key_encrypted` 不是明文。
- 旧配置迁移后，`system_setting.llm_api_key` 明文行已被删除；兼容 PUT 不会重新写入明文。

## 17. 实施分期

虽然本设计覆盖完整闭环，实施时按以下顺序提交，每步都可独立验收：

1. **数据与迁移**：四张表、迁移、旧配置导入、模型与仓储层。
2. **供应商与模型 API**：CRUD、模型清单拉取、Key 加密与脱敏、API 测试。
3. **能力探测**：`probe_model` worker、文本/图片/视频探针、手工覆盖。
4. **工作分配与模型网关**：三槽位、自动推荐、主备调用、调用审计。
5. **业务模块接入**：分析、判类、视频理解、拆解改为走统一网关。
6. **前端模型接入页**：已导入模型列表、添加模型弹窗、工作分配页。
7. **兼容收尾**：旧系统设置页签移除模型字段，旧 API 兼容层验证。
8. **端到端验收与文档索引更新**。

## 18. 星算接入方式

星算使用普通供应商导入，不实现 OAuth / 设备码一键授权，也不新增令牌来源：

- 星算预设使用 `preset_key=xingsuan`，协议为 OpenAI 兼容，Base URL 为 `https://api.xsai5.xyz/v1`。
- 用户在模型接入页通过星算官网 `https://xsai5.xyz` 获取访问密钥，再按普通供应商流程导入。
- 星算供应商的模型清单与能力仍然走同一探测流程，不因为“官方”而跳过实测。

## 19. 验收标准

满足以下条件才视为完成：

1. 用户可以在不阅读协议文档的情况下，通过预设 + 供应商访问密钥 + 模型多选完成导入。
2. “已导入模型”列表能清楚展示能力、工作归属、检测状态与最近错误。
3. 三个工作槽位均有主模型与可选备用模型，默认自动推荐，人工修改不被覆盖。
4. 能力标签以真实探测为准，视频能力只有视频探针通过或用户明确手工标记才可用。
5. 主模型失败切备用模型后，任务可成功，但原始失败与报错完整可查。
6. 任何 API、日志、列表响应都不泄露供应商访问密钥。
7. 旧 `llm_*` / `process_*` 配置可无损迁移，升级后任务不中断。
8. 后端测试、前端构建、假供应商端到端验收全部通过。
