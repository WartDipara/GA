"""Kernel execution ownership uses the shared OS file lock."""

from game_automation.common import FileLock as ExecutionLock

__all__ = ["ExecutionLock"]
