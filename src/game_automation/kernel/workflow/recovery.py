"""Recover execution boundaries through the authoritative TypeScript executor."""

from .execution_client import invoke


def reconcile_interrupted_runs(store):
    return invoke(store.root, "recover")
