"""pytest 全局夹具：为通知模块保持 hermetic——单测绝不允许真的弹 macOS 系统通知。"""

import os

import pytest

os.environ["ZM_NOTIFY_OFF"] = "1"


@pytest.fixture(autouse=True)
def skip_admin_for_unrelated_tests(monkeypatch):
    from zhuang_monitor.main import app, require_admin

    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: None)
