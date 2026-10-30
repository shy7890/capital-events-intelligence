"""FastAPI 入口：REST API + 前端静态托管（PRD §4）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import router
from .config import settings

@asynccontextmanager
async def _lifespan(_: FastAPI):
    with db.connect() as conn:
        db.init_schema(conn)
    yield


app = FastAPI(title=settings.app_name, docs_url="/api/docs", lifespan=_lifespan)
app.include_router(router)

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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return index_file.read_text("utf-8")
    return _PLACEHOLDER


if (FRONTEND_DIST / "assets").exists():  # 前端构建产物静态挂载（缺失时走上方占位页）
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
