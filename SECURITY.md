# 安全策略

## 报告漏洞

请**不要**通过公开 Issue 报告安全问题。
优先用 GitHub 的私有漏洞报告：仓库 **Security → Report a vulnerability**。
报告里请包含：影响范围、复现步骤、PoC（如适用）、你的环境。

## 设计上的安全边界（部署前请知悉）

- **单管理员模式**：没有多用户/角色。暴露到公网前请务必加 HTTPS 反代与访问控制。
- **凭证 AES-256-GCM 加密入库**，明文只在写入时出现；API 与前端响应**永远脱敏**。
  加密密钥来自 `PLATFORM_CREDENTIAL_ENC_KEY` 环境变量 / 密钥文件 / `data/secret.key`
  （开发兜底）。**密钥不进 git；丢了它，所有已存凭证作废。**
- **一键升级边车持有 docker socket（等价 root）**，因此默认关闭，且**不映射任何宿主端口**。
  只在受信环境启用，并用强 `PLATFORM_UPDATER_TOKEN`。
- **数据库默认口令**（`platform/platform`）仅供内网 compose 栈使用；对外部署请改。
- 请勿把 `.env`、`data/`、备份包提交进版本库；它们已在 `.gitignore` 中，
  但**升级/迁移机器的备份必须包含 `secret.key`**。
- 平台会登录各内容平台的账号。**请使用你自己的账号，并遵守各平台的服务条款。**
