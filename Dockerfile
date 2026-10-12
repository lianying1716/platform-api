# 多阶段构建：前端 -> 后端运行时
FROM node:22-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npx vite build --outDir /build/dist --emptyOutDir

FROM python:3.12-slim
WORKDIR /srv

# 构建期注入版本三件套（CI 传 tag / commit / 时间；本地不传则回退 dev）。
# 这样 /api/system/version 报出的就是镜像真实版本，前后端一致。
ARG PLATFORM_BUILD_VERSION=dev
ARG PLATFORM_BUILD_COMMIT=unknown
ARG PLATFORM_BUILD_TIME=unknown
ENV PLATFORM_BUILD_VERSION=${PLATFORM_BUILD_VERSION} \
    PLATFORM_BUILD_COMMIT=${PLATFORM_BUILD_COMMIT} \
    PLATFORM_BUILD_TIME=${PLATFORM_BUILD_TIME}

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY app/ app/
RUN uv pip install --system ".[postgres]" && uv pip install --system playwright && playwright install --with-deps chromium
COPY alembic.ini ./
COPY alembic/ alembic/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh
COPY --from=web /build/dist app/static
# 生产镜像默认值必须安全：建表完全交给 Alembic（entrypoint 迁移），
# 不用 create_all。这同时让启动版本门禁进入严格模式（enforce = not auto_create_tables）。
# 这样即使用户直接 `docker run` 而不走 compose，也拿到严格门禁，不会 fail-open。
ENV PLATFORM_HOST=0.0.0.0 PLATFORM_PORT=9100 PLATFORM_DATA_DIR=/data \
    PLATFORM_SERVE_FRONTEND=true PLATFORM_AUTO_CREATE_TABLES=false
VOLUME /data
EXPOSE 9100
# 同一镜像两种角色：默认起 API；worker 由 compose 用 command 覆盖为
# ["python", "-m", "app.workers.runner"]（一个 API 进程 + 一个 worker 进程）。
# ENTRYPOINT 先跑 `alembic upgrade head`（幂等），再 exec CMD。
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
