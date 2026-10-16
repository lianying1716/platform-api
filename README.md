# assets（媒体托管分支，孤儿分支）

这个分支**只放 README / 文档里引用的媒体文件**，不进 `main` 的历史，
避免把几十 MB 的视频塞进代码历史。

- `promo/stargather-promo-v9-1080p.mp4` — 星采宣传片 v9 全片
- `promo/stargather-promo-v9-720p.mp4` — 轻量版
- `promo/stargather-promo-v9-poster.jpg` — 宣传片封面帧
- `promo/stargather-promo-v9-contact-sheet.jpg` — 全片接触表（8 镜静帧）
- `shots/*.jpg` — 首页「界面」一节的 6 张真实界面截图（来自公开演示站，演示数据已脱敏）

图片类资源用 `https://raw.githubusercontent.com/lianying1716/platform-api/assets/...`
引用即可（返回正确的 `image/*`）。

## 视频：必须走 user-attachments，不要指向本分支

GitHub 的 README 渲染器**只对 `github.com/user-attachments/assets/<uuid>` 这类资源
输出内嵌播放器**。指向 `raw.githubusercontent.com/.../*.mp4` 的 `<video>` 会被整个过滤掉，
读者只会看到一个下载链接 —— 看起来像播放器，实际不能播。

所以本分支的 mp4 只作留档，首页播放用的是上传到 user-attachments 的副本：

| 用途 | 资源 |
|---|---|
| 首页内嵌播放（720p） | `https://github.com/user-attachments/assets/e9fcdca2-2fb2-452e-9086-3b4efdf85794` |
| 1080p 全片（链出） | `https://github.com/user-attachments/assets/be90dda1-128e-4f44-8845-e54debe6172e` |

重新生成成片见工作台仓库 `promo-video/`；替换播放器资源时要同步改 `main` 的 README。
