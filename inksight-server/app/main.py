"""InkSight 新后端服务入口（FastAPI）。

两类设备接口：
  - /api/todos          待办列表（对接滴答清单）
  - /api/images/*       图片资源（读取站点 images/ 目录）
管理/授权：
  - /admin/dida365/auth | /callback   滴答清单 OAuth 授权
  - /                      inksight 风格状态页
  - /health                健康检查
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.routers import todos, images
from app.dida import auth as dida_auth

app = FastAPI(title="InkSight Server", version="1.0.0")

app.include_router(todos.router)
app.include_router(images.router)
app.include_router(dida_auth.router)

_INDEX_HTML = (Path(__file__).resolve().parent / "web" / "templates" / "index.html").read_text(
    encoding="utf-8"
)


@app.get("/", response_class=HTMLResponse)
def index():
    return _INDEX_HTML


@app.get("/health")
def health():
    return {"status": "ok"}
