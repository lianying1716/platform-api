# systemd 用户服务（开发模板）

可选：把「后端 API + Vite 前端」作为常驻开发服务跑在本机/局域网。

```bash
mkdir -p ~/.config/systemd/user
cp platform-api-dev.service platform-web-dev.service ~/.config/systemd/user/
# 打开两个 unit，把 %h/platform-api 换成你 clone 后的真实路径
systemctl --user daemon-reload
systemctl --user enable --now platform-api-dev platform-web-dev
```

前提：`.venv` 已按 README 建好（`uv sync --extra browser`），Node/npm 在 `/usr/bin`。
生产部署请用 Docker Compose，不需要这些 unit。
