"""基于已选 Flask 项目的正式适配入口，替换全局状态和占位保存。"""
from .paths import configure_environment

configure_environment()

from .app import create_app

__all__ = ["create_app"]
