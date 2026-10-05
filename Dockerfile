# 多阶段构建：前端 -> 后端运行时
FROM node:22-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npx vite build --outDir /build/dist --emptyOutDir

FROM python:3.12-slim
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml ./
COPY app/ app/
RUN uv pip install --system ".[postgres]" && uv pip install --system playwright && playwright install --with-deps chromium
COPY --from=web /build/dist app/static
ENV PLATFORM_HOST=0.0.0.0 PLATFORM_PORT=9100 PLATFORM_DATA_DIR=/data
VOLUME /data
EXPOSE 9100
# 同一镜像两种角色：默认起 API；worker 由 compose 用 command 覆盖为
# ["python", "-m", "app.workers.runner"]（一个 API 进程 + 一个 worker 进程）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
