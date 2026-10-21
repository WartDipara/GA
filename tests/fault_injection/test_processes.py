import asyncio

import pytest

from game_automation.common import FrameworkError
from game_automation.kernel.workflow.runner import fixture_observation


@pytest.mark.parametrize(
    "capability,code",
    [
        ("fixture.crash", "PROCESS_EXITED"),
        ("fixture.corrupt", "INVALID_PROTOCOL"),
        ("fixture.slow", "TIMEOUT"),
    ],
)
async def test_faults_are_traced(host, store, run_id, capability, code):
    with pytest.raises(FrameworkError) as error:
        await host.call(capability, {}, timeout=1)
    assert error.value.code == code
    assert any(e["kind"] in ("call.failed", "call.incomplete") for e in store.events(run_id))


async def test_retries_preserve_failure(host, store, run_id):
    with pytest.raises(FrameworkError):
        await host.call("fixture.fail", {})
    events = store.events(run_id)
    assert len([e for e in events if e["kind"] == "call.failed"]) == 2
    starts = [e for e in events if e["kind"] == "call.started"]
    assert [e["payload"]["attempt"] for e in starts] == [1, 2]
    assert starts[0]["span_id"] != starts[1]["span_id"]


async def test_unknown_action_is_never_replayed(host, store, run_id):
    observation = fixture_observation(store, run_id)
    params = {
        "action": {
            "id": "uncertain",
            "type": "tap",
            "x": 1,
            "y": 1,
            "observation_id": observation["id"],
            "session_id": run_id,
        },
        "observation": observation,
        "fault": "crash",
    }
    with pytest.raises(FrameworkError) as error:
        await host.call("fixture.act", params)
    assert error.value.code == "PROCESS_EXITED"
    assert store.unknown_actions(run_id) == ["uncertain"]
    with pytest.raises(FrameworkError) as error:
        await host.call("fixture.act", params)
    assert error.value.code == "ACTION_ALREADY_DISPATCHED"
    assert len([e for e in store.events(run_id) if e["kind"] == "action.intent"]) == 1


async def test_trace_failure_prevents_device_dispatch(host, store, run_id, monkeypatch):
    observation = fixture_observation(store, run_id)

    def fail(*args, **kwargs):
        raise FrameworkError("TRACE_UNAVAILABLE", "Disk full")

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(FrameworkError) as error:
        await host.call(
            "fixture.act",
            {
                "action": {
                    "id": "no_dispatch",
                    "type": "tap",
                    "x": 1,
                    "y": 1,
                    "observation_id": observation["id"],
                    "session_id": run_id,
                },
                "observation": observation,
            },
        )
    assert error.value.code == "TRACE_UNAVAILABLE"
    assert (
        store.db.execute("SELECT COUNT(*) FROM actions WHERE run_id=?", (run_id,)).fetchone()[0]
        == 0
    )


async def test_cancel_inflight(host, store, run_id):
    pending = asyncio.create_task(host.call("fixture.slow", {}, timeout=10))
    await asyncio.sleep(0.3)
    store.cancel(run_id)
    with pytest.raises(FrameworkError) as error:
        await asyncio.wait_for(pending, 5)
    assert error.value.code == "CANCELLED"


async def test_old_observation_rejected(host, store, run_id):
    old = fixture_observation(store, run_id)
    newer = {**old, "id": "new-frame"}
    store.event(run_id, "observation.captured", {"observation": newer})
    with pytest.raises(FrameworkError) as error:
        await host.call(
            "fixture.act",
            {
                "action": {
                    "id": "old",
                    "type": "tap",
                    "x": 1,
                    "y": 1,
                    "observation_id": old["id"],
                    "session_id": run_id,
                },
                "observation": old,
            },
        )
    assert error.value.code == "STALE_OBSERVATION"


async def test_parent_deadline_propagates_as_timeout(host):
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.15):
            await host.call("fixture.slow", {}, timeout=10)
