"""升级边车（updater sidecar）——见 service.py。

为什么单独一个服务：一键升级需要控制 Docker（pull + recreate api/worker）。
把 docker socket 交给主 API 进程 = 把最高权限暴露给对外服务的攻击面。
这里隔离成独立进程，且：
- 不映射任何宿主端口，只在 compose 内部网络可达；
- 所有升级请求必须带共享密钥 X-Updater-Token；
- 版本号过白名单正则，拒绝任意 tag / shell 片段；
- 只操作 platform-api / platform-worker，绝不动 db 和自己。
"""

from app.updater.service import app

__all__ = ["app"]
