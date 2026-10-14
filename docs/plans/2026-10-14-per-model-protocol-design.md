# 按模型自动判定协议 · 设计

> 日期：2026-10-14
> 范围：`platform-api`（模型接入）
> 状态：设计已确认，实施中
> 前置：`2026-10-10-model-access-and-task-routing-design.md`（模型接入与任务路由）

## 1. 背景与目标

现状：`ai_provider.protocol` 是**供应商级**的单选（openai / anthropic / gemini），
一次决定该供应商下所有模型的对话协议。问题是：

1. 一个网关常常同时暴露多种协议（星算 `api.xsai5.xyz/v1` 走 OpenAI 兼容，同站点根还有
   Gemini 原生 `:generateContent`）；供应商级一刀切无法表达。
2. 让普通用户理解并选择协议本身就是负担；协议应当由后端实测判定，而不是让人选。
3. 用户侧最痛的表述是「不论接哪家供应商都 404」——协议/端点错配却没有任何校验。
   协议若靠猜，会把「选错协议」变成「静默选错协议」，更难排查。

目标：

- 协议**默认自动判定**：导入/检测时后端用真实请求逐个试，谁能拿到合格结果就固化谁。
- 允许在模型级**手动覆盖**（用户明确知道时）。
- 未判定前/未覆盖时**继承供应商协议**，存量行为不变。
- 协议变化必须让旧能力失效并可重新检测（复用现有 stale 语义）。

## 2. 非目标

- 不改供应商级协议的语义：它仍是「默认协议 + 拉取模型清单用」。
- 不引入按「工作类型」定协议（视频 transport 已有该能力，与本文正交）。
- 不做跨协议的结果质量比较；只判定「能否拿到合格响应」。

## 3. 数据模型

`ai_model` 两列新增（迁移 `0021`，只加列、不回填，存量行为不变）：

| 列 | 类型 | 说明 |
|---|---|---|
| `protocol` | varchar(32) NULL | 模型级协议覆盖；`NULL` = 继承供应商 |
| `protocol_source` | varchar(16) | `inherit` / `auto` / `manual`，默认 `inherit` |

- **生效协议** = `model.protocol or provider.protocol`。
- `manual`：用户显式指定，**自动检测不得覆盖**。
- `auto`：后端实测判定并固化。
- `inherit`：未判定，跟随供应商。

## 4. 判定流程（probe 内）

`probe_model` 在文本探测阶段先定协议：

1. `protocol_source == "manual"` → 直接用 `model.protocol`，不试其他。
2. 否则按候选顺序试：`[继承协议, 其余两个协议]`，第一个返回合格 JSON 的胜出。
3. 胜出者 == 继承协议 → 保持 `inherit`（不 pin，跟随供应商变化）。
4. 胜出者 != 继承协议 → 写 `model.protocol` + `protocol_source="auto"`。
5. 全部失败 → 保持 `inherit`，能力记为失败（错误详情照旧可见）。

图片/视频子探测一律使用**同一个已判定协议**。

## 5. 失效判定

能力记录（`capabilities.*`）新增写入生效 `protocol`。`capabilities_of()` 在现有
`config_revision` 比对之外，再比对协议：不一致即判 stale（`value=False`，`source="stale"`，
detail 提示需重新检测）。这样「改了模型协议」与「改了供应商连接」走同一套失效语义。

## 6. 接口

- `GET /api/model-access/models`：`_model_payload` 增加 `protocol`（生效协议）、
  `protocol_source`、`protocol_override`（模型级值，可能为 null）。
- `PATCH /api/model-access/models/{id}`：接受 `protocol`（`null` = 恢复继承）与
  `protocol_source`（`manual` / `inherit`），并清空该模型的探测结果使其重测。
- `POST /{id}/probe`：按第 4 节自动判定。

## 7. 前端

- 模型列表：能力列旁显示生效协议；详情区显示「协议」与来源（继承/自动/手动）。
- 模型行提供「协议」选择：跟随供应商 / OpenAI 兼容 / Anthropic 原生 / Gemini 原生。
  默认「跟随供应商」，即用户全程不必碰。
- 供应商表单里的协议保留，文案明确为「默认协议（拉取模型清单用）」。

## 8. 验收

- 供应商协议 openai，但模型实际只支持 gemini 原生：自动检测后模型协议为 gemini，
  能力通过；`GET /models` 显示 `protocol=gemini`、`protocol_source=auto`。
- 手动设为 anthropic 后自动检测不覆盖；改回「跟随供应商」后继承生效。
- 模型协议变化后旧能力立即 stale，需重新检测。
- 存量数据（两列未设）行为与改造前完全一致：全部测试通过。
