"""设备图片资源接口：读取站点 images/ 目录（图片由配套脚本生成）。

本期仅完成目录规划与拉取接口；图片的具体样式规则由后续脚本实现。
设备默认显示待办，按键切换到图片页后，按 manifest 顺序拉取并展示。
"""
import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import IMAGES_DIR

logger = logging.getLogger("inksight.images")
router = APIRouter(tags=["images"])

ALLOWED_EXT = {".bmp", ".png", ".jpg", ".jpeg", ".gif"}


def _list_images() -> list[Path]:
    if not IMAGES_DIR.exists():
        return []
    return sorted(
        (f for f in IMAGES_DIR.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED_EXT),
        key=lambda f: f.name,
    )


@router.get("/api/images/manifest")
def image_manifest():
    """返回图片清单；version 随文件名+修改时间变化，设备据此判断是否重新下载。"""
    files = _list_images()
    h = hashlib.sha1()
    for f in files:
        h.update(f.name.encode("utf-8"))
        try:
            h.update(str(int(f.stat().st_mtime)).encode("utf-8"))
        except OSError:
            pass
    images = [
        {"slot": i + 1, "name": f.name, "url": f"/api/images/{f.name}"}
        for i, f in enumerate(files)
    ]
    return {"version": h.hexdigest()[:16], "count": len(images), "images": images}


@router.get("/api/images/{name}")
def image_file(name: str):
    """按文件名返回图片二进制（防目录遍历）。"""
    target = (IMAGES_DIR / name).resolve()
    if target != IMAGES_DIR and IMAGES_DIR not in target.parents:
        raise HTTPException(status_code=400, detail="invalid name")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target)
