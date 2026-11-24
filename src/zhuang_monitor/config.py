"""全局配置。所有阈值类运行参数存 SQLite config 表（PRD F10），此处只放环境级配置。"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZM_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "资本事件研究台"
    db_path: Path = PROJECT_ROOT / "data" / "zhuang.db"
    host: str = "127.0.0.1"
    port: int = 8317
    admin_username: str = "admin"
    admin_password: SecretStr | None = None

    # Optional authorized financial-data bridge. The public package has no provider default.
    ifind_mcp_url: str = os.getenv("ZM_IFIND_MCP_URL", "")
    ifind_key_path: Path = Path.home() / ".config/capital-events-intelligence/secrets/provider.key"

    # 每日批任务触发点（本地时间），launchd plist 与此处保持一致
    batch_times: tuple[tuple[int, int], ...] = ((17, 30), (21, 30), (8, 30))


settings = Settings()
