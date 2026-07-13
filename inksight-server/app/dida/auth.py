"""滴答清单 OAuth 网页授权路由。

流程：管理员访问 /admin/dida365/auth -> 跳转 Dida365 授权页 ->
用户同意后跳回 DIDA_REDIRECT_URI(/admin/dida365/callback?code=...) ->
本路由用 code 换令牌并保存到 .dida_tokens.json，后续设备即可拉取待办。
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dida import client

router = APIRouter(prefix="/admin/dida365", tags=["dida365-auth"])


@router.get("/auth")
def dida_auth():
    if not client.DIDA_CLIENT_ID or not client.DIDA_REDIRECT_URI:
        return HTMLResponse(
            "<p>未配置 DIDA_CLIENT_ID / DIDA_REDIRECT_URI，请先在 .env 填写。</p>"
        )
    return RedirectResponse(client.auth_url())


@router.get("/callback")
def dida_callback(code: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<p>授权被拒绝: {error}</p>")
    if not code:
        return HTMLResponse("<p>缺少授权码 code。</p>")
    try:
        client.exchange_code(code)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<p>令牌交换失败: {e}</p>")
    return HTMLResponse(
        "<h2>✅ 滴答清单授权成功</h2>"
        "<p>后端已保存访问令牌，设备现在可以拉取待办。返回设备端刷新即可。</p>"
    )
