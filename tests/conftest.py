"""pytest 全局夹具：为通知模块保持 hermetic——单测绝不允许真的弹 macOS 系统通知。"""

import os

os.environ["ZM_NOTIFY_OFF"] = "1"
