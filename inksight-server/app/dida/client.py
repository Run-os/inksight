"""滴答清单 (Dida365) OAuth 与 Open API 封装。

设计要点：
- 令牌持久化到站点根目录 .dida_tokens.json（也可在 .env 预填 DIDA_ACCESS_TOKEN/REFRESH_TOKEN）。
- 每次调用 ensure_token()：有效则直接用；否则用 refresh_token 刷新；都没有则返回 None（需网页授权）。
- 任务获取：优先 DIDA_PROJECT_ID 单项目；否则聚合所有清单（通过 GET /project/{id}/data，
  这是 Dida365 官方推荐的可靠方式，比 /task 端点稳定）。
"""
import json
import logging
import time
from typing import Optional

import httpx
from urllib.parse import quote

from app.config import (
    DIDA_CLIENT_ID, DIDA_CLIENT_SECRET, DIDA_REDIRECT_URI, DIDA_SCOPE,
    DIDA_PROJECT_ID, DIDA_ACCESS_TOKEN, DIDA_REFRESH_TOKEN,
    DIDA_AUTH_BASE, DIDA_API_BASE, TOKENS_FILE,
)

logger = logging.getLogger("inksight.dida")

TOKEN_EXPIRE_SKEW = 300  # 提前 5 分钟视为过期，避免临界失效


class TokenStore:
    def __init__(self) -> None:
        self.access_token: str = DIDA_ACCESS_TOKEN or ""
        self.refresh_token: str = DIDA_REFRESH_TOKEN or ""
        self.expires_at: float = 0.0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
            self.access_token = data.get("access_token", self.access_token)
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            self.expires_at = float(data.get("expires_at", 0))
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass

    def save(self) -> None:
        TOKENS_FILE.write_text(
            json.dumps(
                {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": self.expires_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def valid(self) -> bool:
        return bool(self.access_token) and time.time() < (self.expires_at - TOKEN_EXPIRE_SKEW)


_store = TokenStore()


def auth_url(state: str = "inksight") -> str:
    """构造滴答清单授权跳转 URL。"""
    return (
        f"{DIDA_AUTH_BASE}/authorize"
        f"?scope={quote(DIDA_SCOPE)}"
        f"&client_id={DIDA_CLIENT_ID}"
        f"&state={quote(state)}"
        f"&redirect_uri={quote(DIDA_REDIRECT_URI)}"
        f"&response_type=code"
    )


def exchange_code(code: str) -> bool:
    """用授权码换取 access/refresh token 并保存。"""
    resp = httpx.post(
        f"{DIDA_AUTH_BASE}/token",
        auth=(DIDA_CLIENT_ID, DIDA_CLIENT_SECRET),
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": DIDA_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _store.access_token = data["access_token"]
    _store.refresh_token = data.get("refresh_token", _store.refresh_token)
    _store.expires_at = time.time() + int(data.get("expires_in", 0))
    _store.save()
    return True


def _refresh() -> bool:
    if not _store.refresh_token:
        return False
    try:
        resp = httpx.post(
            f"{DIDA_AUTH_BASE}/token",
            auth=(DIDA_CLIENT_ID, DIDA_CLIENT_SECRET),
            data={
                "refresh_token": _store.refresh_token,
                "grant_type": "refresh_token",
                "redirect_uri": DIDA_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _store.access_token = data["access_token"]
        if data.get("refresh_token"):
            _store.refresh_token = data["refresh_token"]
        _store.expires_at = time.time() + int(data.get("expires_in", 0))
        _store.save()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Dida365 token refresh failed: %s", e)
        return False


def ensure_token() -> Optional[str]:
    """返回可用的 access_token，必要时刷新；都没有则返回 None。"""
    if _store.valid():
        return _store.access_token
    if _refresh():
        return _store.access_token
    return None


def _headers() -> dict:
    return {"Authorization": f"Bearer {_store.access_token}"}


def _split_due(due: str):
    """ISO 截止时间 -> (due_date, remind_at)。例: 2026-07-13T14:30:00+0000"""
    date_part, _, time_part = due.partition("T")
    remind_at = time_part[:5] if time_part else ""
    return date_part, remind_at


def _normalize_task(t: dict, project_name: str) -> dict:
    status = t.get("status", 0)
    done = status == 2
    due = t.get("dueDate") or t.get("startDate") or ""
    due_date, remind_at = _split_due(due) if due else ("", "")
    return {
        "id": t.get("id", ""),
        "text": (t.get("title") or t.get("content") or "").strip(),
        "done": done,
        "remind_at": remind_at,
        "due": due_date,
        "project": project_name,
    }


def get_tasks() -> list[dict]:
    """拉取待办列表。未授权抛 RuntimeError('NO_TOKEN')。"""
    token = ensure_token()
    if not token:
        raise RuntimeError("NO_TOKEN")

    with httpx.Client(timeout=20) as client:
        if DIDA_PROJECT_ID:
            projects = [{"id": DIDA_PROJECT_ID, "name": ""}]
        else:
            r = client.get(f"{DIDA_API_BASE}/project", headers=_headers())
            r.raise_for_status()
            projects = r.json()

        items: list[dict] = []
        for p in projects:
            pid = p.get("id")
            pname = p.get("name", "")
            try:
                r = client.get(f"{DIDA_API_BASE}/project/{pid}/data", headers=_headers())
                r.raise_for_status()
                data = r.json()
                for t in data.get("tasks", []):
                    items.append(_normalize_task(t, pname))
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to fetch project %s: %s", pid, e)

    # 排序：有截止日期的在前（按日期+时间），无日期的排后
    items.sort(key=lambda x: (x["due"] or "9999", x["remind_at"] or "99"))
    return items
