"""Compatibility entry points for TypeScript-owned report publication."""


def finalize_reports(store, run_id):
    from game_automation.kernel.workflow.execution_client import invoke

    receipts = invoke(store.root, "report", run_id=run_id, pending=False)
    return receipts[0]


def repair_pending_reports(store):
    from game_automation.kernel.workflow.execution_client import invoke

    return invoke(store.root, "report", pending=True)
