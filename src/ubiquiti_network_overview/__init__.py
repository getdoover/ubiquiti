"""Lambda entry point for the Ubiquiti Network Overview."""

from pydoover.processor import run_app

from .app_config import NetworkOverviewConfig
from .application import NetworkOverviewApp

__all__ = ["NetworkOverviewApp", "handler"]


def handler(event, context):
    # Config elements are declared at class level and accumulate across warm
    # invocations of the same lambda container, so the schema is cleared before
    # each run rather than growing a duplicate of every field.
    NetworkOverviewConfig.clear_elements()
    return run_app(NetworkOverviewApp(), event, context)
