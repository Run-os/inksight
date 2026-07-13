"""设备待办接口：对接滴答清单 (Dida365)。"""
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from app.config import DEVICE_SHARED_SECRET
from app.dida import client

logger = logging.getLogger("inksight.todos")
router = APIRouter(tags=["todos"])


def _check_secret(request: Request) -> None:
    if DEVICE_SHARED_SECRET:
        provided = request.headers.get("X-Device-Secret", "")
        if provided != DEVICE_SHARED_SECRET:
            raise HTTPException(status_code=401, detail="invalid device secret")


@router.get("/api/todos")
def api_todos(request: Request, mac: str = ""):
    """设备拉取待办列表。默认显示（固件启动即进入待办页）。

    返回: {"updated_at": <ts>, "source": "dida365", "items": [{id,text,done,remind_at,due,project}]}
    """
    _check_secret(request)
    try:
        items = client.get_tasks()
    except RuntimeError as e:
        if "NO_TOKEN" in str(e):
            raise HTTPException(
                status_code=503,
                detail="Dida365 未授权：请访问 /admin/dida365/auth 完成授权",
            )
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("fetch dida tasks failed")
        raise HTTPException(status_code=502, detail=f"dida365 error: {e}")

    return {
        "updated_at": int(time.time()),
        "source": "dida365",
        "items": items,
    }
