"""FastAPI 入口：REST API + 前端静态托管（PRD §4）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import db
from .api import router
from .config import settings

@asynccontextmanager
async def _lifespan(_: FastAPI):
    with db.connect() as conn:
        db.init_schema(conn)
    yield


security = HTTPBasic(auto_error=False)


def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> None:
    password = settings.admin_password.get_secret_value() if settings.admin_password else ""
    if len(password) < 16:
        raise HTTPException(503, "Set ZM_ADMIN_PASSWORD to at least 16 characters")
    if credentials is None:
        raise HTTPException(401, "Authentication required", {"WWW-Authenticate": "Basic"})
    valid_username = compare_digest(credentials.username.encode(), settings.admin_username.encode())
    valid_password = compare_digest(credentials.password.encode(), password.encode())
    if not (valid_username and valid_password):
        raise HTTPException(401, "Authentication required", {"WWW-Authenticate": "Basic"})

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if (
            request.headers.get("sec-fetch-site") == "cross-site"
            or (origin is not None and origin != expected)
        ):
            raise HTTPException(403, "Cross-origin write is not allowed")


app = FastAPI(title=settings.app_name, docs_url="/api/docs", lifespan=_lifespan)
app.include_router(router, dependencies=[Depends(require_admin)])

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_PLACEHOLDER = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>资本事件研究台 · Capital Events Intelligence</title>
<style>body{font-family:-apple-system,sans-serif;display:flex;justify-content:center;
padding-top:20vh;background:#111;color:#eee}code{color:#7ec}</style></head>
<body><div style="text-align:center"><h1>资本事件研究台</h1>
<p>M0 脚手架已就绪 —— 后端运行中，前端页面将在 M3 上线</p>
<p>API 文档：<code>/api/docs</code> · 健康检查：<code>/api/health</code></p>
</div></body></html>"""


@app.get("/api/health")
def health() -> dict:
    try:
        with db.connect() as conn:
            tables = len(db.table_names(conn))
        return {"status": "ok", "db": "ok", "tables": tables}
    except Exception as exc:  # noqa: BLE001
        return {"status": "ok", "db": f"error: {exc}"}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def index() -> str:
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return index_file.read_text("utf-8")
    return _PLACEHOLDER


if (FRONTEND_DIST / "assets").exists():  # 前端构建产物静态挂载（缺失时走上方占位页）
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
