"""InkSight 新后端服务配置（从站点根目录 .env 读取）。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# ── 服务 ──────────────────────────────────────────────
PORT = int(_env("PORT", "8000"))

# 可选设备鉴权共享密钥；设置后设备需在 Header 带 X-Device-Secret
DEVICE_SHARED_SECRET = _env("DEVICE_SHARED_SECRET", "")

# ── 滴答清单 (Dida365) OAuth 应用凭据 ──────────────────
DIDA_CLIENT_ID = _env("DIDA_CLIENT_ID", "")
DIDA_CLIENT_SECRET = _env("DIDA_CLIENT_SECRET", "")
DIDA_REDIRECT_URI = _env("DIDA_REDIRECT_URI", "")
DIDA_SCOPE = _env("DIDA_SCOPE", "tasks:read")
# 可选：仅展示该清单(项目)的任务；留空则聚合所有清单
DIDA_PROJECT_ID = _env("DIDA_PROJECT_ID", "")

# 可选：预填令牌（省去网页授权步骤）
DIDA_ACCESS_TOKEN = _env("DIDA_ACCESS_TOKEN", "")
DIDA_REFRESH_TOKEN = _env("DIDA_REFRESH_TOKEN", "")

# 图片资源目录（相对站点根目录）
IMAGES_DIR = (BASE_DIR / _env("INKSIGHT_IMAGES_DIR", "images")).resolve()

# ── Dida365 端点 ──────────────────────────────────────
DIDA_AUTH_BASE = "https://dida365.com/oauth"
DIDA_API_BASE = "https://api.dida365.com/open/v1"

# 运行时令牌存储（不入库，单文件）
TOKENS_FILE = BASE_DIR / ".dida_tokens.json"
