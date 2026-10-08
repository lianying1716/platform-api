# 扫码登录：实现说明与排障

支持抖音 / 小红书 / 快手 / 知乎。这些平台没有公开扫码 API，做法是后端起 Chromium
打开官方登录页、截二维码返回前端，用户手机扫码后从浏览器上下文提取 Cookie。

代码：`app/modules/browser_qr.py`（通用流程）+ `app/modules/*.py` 的平台适配
（`qr_start` / `qr_poll`）。前端弹窗在 `web/src/pages/Accounts.vue`。

## 登录成功的判定（多信号，不能只看一个 cookie）

| 平台 | 判定信号 |
|---|---|
| 抖音 | `localStorage.HasUserLogin == "1"` **或** cookie `LOGIN_STATUS=1` / `sessionid` / `sid_tt` **或** 已跳到 `/user/...`、`passport` |
| 小红书 | cookie `web_session` |
| 快手 | cookie `passToken` |
| 知乎 | cookie `z_c0` |

抖音必须同时看 localStorage：PC 端登录后主要写 `HasUserLogin`，
只看 `LOGIN_STATUS` cookie 会导致**扫完码仍判为未登录、一直停在"等待扫码"**。
（来源：MediaCrawler `media_platform/douyin/client.py:pong`，同一份被授权参考）

`LOGIN_STATUS=0` 是明确登出，优先级最高——不能被 `sessionid` 之类的兜底信号翻盘。

## 扫码进度（scanned 状态）

抖音的扫码进度以平台自己的 `check_qrconnect` 为准，实测返回：

| 平台返回 | 我们映射 |
|---|---|
| `new` | `pending`（还没扫） |
| `scanned` | `scanned`（已扫，等手机确认） |
| `confirmed` | `success` |

在 `browser_qr_start` 里挂一个 response 监听把最新 status 存进会话状态，
`browser_qr_poll` 读它。这样用户扫完码能立刻看到「已扫码，请在手机上点击确认」。

> **坑（已踩过）**：不要用 `passport_csrf_token` 去猜"已扫码"——
> 它一进登录页就有，会导致还没扫码就提示"已扫码"（虚假状态）。
> `tests/test_browser_qr_poll.py` 有回归测试锁定这一点。

## 会话有效期

`qr_session_ttl` 默认 **300 秒**。抖音扫码后常有"手机上二次确认"步骤，
原先 180 秒偏紧——用户还没点确认就超时了，表现就是"扫了没反应"。

## 排障清单

用户说"扫码了但没反应"时，按顺序查：

1. **二维码有没有出来**：`/api/accounts/qr-login/{sid}` 轮询若一直 200，
   看返回的 `status`/`message`；`qr_image_base64` 太短说明没截到。
2. **平台状态在变吗**：会话 `state.qr_status` 是否从 `new` → `scanned` → `confirmed`。
   一直 `new` = 手机没扫到（二维码过期/截图糊/扫了别的码）。
   到 `scanned` 但没 `confirmed` = 用户没在手机上点确认。
3. **轮询是不是静默死了**：前端连续 3 次失败才停，超时会提示并给「重新获取二维码」。
   旧版 `catch {}` 会静默停掉，看起来就是"没反应"。
4. **登录后判定**：`localStorage.HasUserLogin`、cookie、URL 三者任一命中即成功。
5. **风控**：无头模式触发验证码时会抛可读异常，引导走「手动粘 Cookie」。

## 真机验证方式

```bash
# 起一个会话，打印平台状态与轮询结果（不扫码时应始终 pending）
.venv/bin/python - <<'PY'
import asyncio, os, sys
sys.path.insert(0, '.')
os.environ.setdefault("PLATFORM_DATABASE_URL", "sqlite:////tmp/qr.db")
async def main():
    from app.modules.browser_qr import browser_qr_start, browser_qr_poll, _live, _close
    s = await browser_qr_start("douyin")
    sid = s.session_data["browser_session"]
    for i in range(6):
        await asyncio.sleep(3)
        print(i, _live[sid]["state"].get("qr_status"), (await browser_qr_poll("douyin", s.session_data)).status)
    await _close(sid)
asyncio.run(main())
PY
```

## 登录后必须拿到"我是谁"（external_id）

`external_id` 决定**同一人二次扫码是更新原账号还是新建一个**。
抖音原先不返回它，导致每扫一次就多一个账号、卡片还显示"未命名账号"（已修）。

取身份的顺序（`_douyin_profile`）：

1. **self profile**：借页面自身带签名的请求
   `/aweme/v1/web/user/profile/self/?device_platform=webapp&aid=6383`
   取 `user.sec_uid` / `user.uid` / `user.nickname`
   （实测未登录返回 `status_code=8 用户未登录`，登录后返回 `user` 对象）
2. 退化到 localStorage（`user_sec_id` / `user_id` 等）与 cookie（`sid_tt` / `uid_tt`）

落库时（`app/api/accounts.py`）的去重顺序：

1. 有 `external_id` → 按 `(platform, external_id)` 精确匹配更新
2. 没匹配到、但该平台**只有一个**账号 → 复用它并回填这次拿到的身份
3. 该平台有**多个**账号又拿不到身份 → 返回 409 明确报错，**绝不猜着建重复号**

> 排查：如果用户反馈"扫一次多一个账号"，先查 `qr_poll` 是否回了 `external_id`。
> `tests/test_qr_login_accounts.py` 已锁定这几种情况。
