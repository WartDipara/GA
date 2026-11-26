"""Named SQL resources for read-only storage and report queries."""

from functools import cache
from importlib.resources import files


@cache
def read_query(name: str) -> str:
    return files("game_automation").joinpath("storage", "sql", f"{name}.sql").read_text("utf-8")
