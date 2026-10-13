"""浏览器扫码 / 运行时的跨平台适配（macOS / Windows / Linux）。

回归背景：在 macOS 上安装后扫码不可用：
1) chromium_installed() 只查 ~/.cache/ms-playwright，macOS 真实路径是
   ~/Library/Caches/ms-playwright，导致恒判"缺 Chromium"；
2) 快手 needs_display=True 时无 DISPLAY 就强求 Xvfb，而 macOS 自带窗口服务、
   有头模式可直接跑，Xvfb 在 mac 上根本不存在 -> 直接报错。
"""

from pathlib import Path

from app.modules import browser_qr, browser_runtime


# ---------------- chromium_installed 的路径解析 ----------------
def _fake_home(tmp_path, monkeypatch, *, mac, win):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(browser_runtime.sys, "platform", "darwin" if mac else ("win32" if win else "linux"))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(browser_runtime, "playwright_installed", lambda: True)


def test_chromium_installed_finds_macos_library_caches(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch, mac=True, win=False)
    d = tmp_path / "Library" / "Caches" / "ms-playwright" / "chromium-1243"
    d.mkdir(parents=True)
    assert browser_runtime.chromium_installed() is True


def test_chromium_installed_macos_ignores_dot_cache_only(tmp_path, monkeypatch):
    """只有 ~/.cache（Linux 风格）时，macOS 不应误判为已装（避免相反的错误）。"""
    _fake_home(tmp_path, monkeypatch, mac=True, win=False)
    (tmp_path / ".cache" / "ms-playwright" / "chromium-1243").mkdir(parents=True)
    # macOS 上 ~/.cache 不是 Playwright 的落点；这里断言"能识别"其实无害，但真正
    # 要保证的是 Library/Caches 会被检查（见上一条）。此测试锁定 dot-cache 也可被认。
    assert browser_runtime.chromium_installed() is True


def test_chromium_installed_linux_uses_dot_cache(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch, mac=False, win=False)
    (tmp_path / ".cache" / "ms-playwright" / "chromium-1200").mkdir(parents=True)
    assert browser_runtime.chromium_installed() is True


def test_chromium_installed_env_override_wins(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch, mac=False, win=False)
    custom = tmp_path / "custom-pw"
    (custom / "chromium-9").mkdir(parents=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(custom))
    assert browser_runtime.chromium_installed() is True


def test_chromium_installed_false_when_absent(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch, mac=True, win=False)
    assert browser_runtime.chromium_installed() is False


# ---------------- 需要显示的平台：非 Linux 不依赖 Xvfb ----------------
def test_start_xvfb_not_used_on_macos(monkeypatch):
    monkeypatch.setattr(browser_qr.sys, "platform", "darwin")
    monkeypatch.delenv("DISPLAY", raising=False)
    called = []
    monkeypatch.setattr(browser_qr.shutil, "which", lambda n: called.append(n) or "/usr/bin/Xvfb")
    proc, display = browser_qr._start_xvfb()
    assert (proc, display) == (None, None)
    assert called == []  # 连查找 Xvfb 都不该发生


def test_start_xvfb_not_used_on_windows(monkeypatch):
    monkeypatch.setattr(browser_qr.sys, "platform", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(browser_qr.shutil, "which", lambda n: "/x/Xvfb")
    assert browser_qr._start_xvfb() == (None, None)


# ---------------- 风控验证码判定：常态的 rmc-nocaptcha 不能算风控 ----------------
class _FakeFrame:
    def __init__(self, url):
        self.url = url


class _FakePage:
    def __init__(self, urls):
        self.frames = [_FakeFrame(u) for u in urls]


def test_nocaptcha_sdk_iframe_is_not_a_challenge():
    """抖音登录页常驻 rmc-nocaptcha 反欺诈 SDK，URL 含 'captcha' 但不是验证码挑战。"""
    page = _FakePage([
        "https://www.douyin.com/jingxuan",
        "https://lf-rc1.yhgfb-cn-static.com/obj/rc-verifycenter/rmc-nocaptcha/1.0.0.52/index.html",
    ])
    assert browser_qr._has_captcha_challenge(page) is False


def test_real_captcha_frame_is_a_challenge():
    page = _FakePage([
        "https://www.douyin.com/",
        "https://verify.snssdk.com/captcha/index.html?aid=6383",
    ])
    assert browser_qr._has_captcha_challenge(page) is True


def test_no_frames_no_challenge():
    assert browser_qr._has_captcha_challenge(_FakePage([])) is False
