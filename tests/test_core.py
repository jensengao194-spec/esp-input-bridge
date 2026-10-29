import asyncio
import json
import time

import pytest
from PIL import Image
from pydantic import ValidationError

from espcomputeruse.agents import strict_schema
from espcomputeruse.executor import Executor, Stopped
from espcomputeruse.hid import character, chord, decode, text_reports
from espcomputeruse.models import Plan
from espcomputeruse.observation import Observation
from espcomputeruse.protocol import TransportError, UncertainDelivery, frame, check_reply
from espcomputeruse.runner import Runner
from espcomputeruse.simulator import DemoPlanner, SimulatedTarget


def plan(obs, actions, status="act"):
    return Plan.model_validate({"observation_id": obs.id, "status": status, "reason": "test", "actions": actions})


@pytest.mark.parametrize("value", ["Hello ESP32-C3!", "~!@#$%^&*()_+{}|:\"<>?", "abc123-=[]\\;'`,./\n\t "])
def test_us_keyboard_roundtrip(value):
    assert "".join(decode(r["mod"], r["keys"][0]) for r in text_reports(value)) == value


def test_keyboard_chord_and_unicode():
    assert chord(["CMD", "SHIFT", "A"]) == {"op": "key", "mod": 10, "keys": [4]}
    with pytest.raises(ValueError):
        character("中")
    with pytest.raises(ValueError):
        chord(["A", "A"])
    with pytest.raises(ValueError):
        chord(["SHELL"])


@pytest.mark.parametrize("actions,status", [([], "act"), ([{"type": "click"}], "done"),
    ([{"type": "shell", "command": "echo no"}], "act"), ([{"type": "move", "dx": 128, "dy": 0}], "act"),
    ([{"type": "move_to", "x": True, "y": 2}], "act"), ([{"type": "click", "count": 10}], "act")])
def test_plan_rejects_invalid(actions, status):
    with pytest.raises(ValidationError):
        plan(SimulatedTarget().capture(), actions, status)


def test_retina_roi_coordinate_mapping():
    obs = Observation(Image.new("RGB", (1000, 500)), (-1440, 100), (2000, 1000), (0, 0))
    assert obs.target(250, 125) == (-940, 350)
    with pytest.raises(ValueError):
        obs.target(1000, 500)


def test_strict_schema_all_properties_required():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            assert "oneOf" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(strict_schema())


def test_protocol_frame_and_ack():
    req, data = frame({"op": "ping"})
    assert data.endswith(b"\n") and json.loads(data) == req
    assert len(req["id"]) == 32
    with pytest.raises(ValueError):
        frame({"op": "arm", "token": "a"*512})
    with pytest.raises(TransportError):
        check_reply({"v": 1, "id": "other", "ok": True}, req["id"])
    with pytest.raises(TransportError):
        check_reply({"v": 1, "id": req["id"], "ok": False, "error": "not_armed"}, req["id"])


async def test_whole_batch_preflight_prevents_partial_unicode():
    target = SimulatedTarget()
    obs = target.capture()
    ex = Executor(target, target, asyncio.Event(), execute=True)
    with pytest.raises(ValueError, match="ASCII"):
        await ex.apply(plan(obs, [{"type": "click"}, {"type": "type_text", "text": "abc中文"}]), obs)
    assert target.commands == []


async def test_snapshot_replay_and_expiry():
    target = SimulatedTarget()
    obs = target.capture()
    ex = Executor(target, target, asyncio.Event(), execute=True)
    p = plan(obs, [{"type": "move", "dx": 1, "dy": 2}])
    await ex.apply(p, obs)
    n = len(target.commands)
    with pytest.raises(ValueError, match="consumed"):
        await ex.apply(p, obs)
    assert len(target.commands) == n
    obs = target.capture()
    obs.captured_at = time.monotonic()-200
    with pytest.raises(ValueError, match="expired"):
        await ex.apply(plan(obs, [{"type": "click"}]), obs)


async def test_stopped_before_send():
    target = SimulatedTarget()
    stop = asyncio.Event()
    ex = Executor(target, target, stop, execute=True)
    obs = target.capture()
    stop.set()
    with pytest.raises(Stopped):
        await ex.apply(plan(obs, [{"type": "click"}]), obs)
    assert not target.commands


async def test_pointer_nonconvergence_never_clicks():
    target = SimulatedTarget()
    target.cursor = lambda: (0, 0)  # Frozen sensor.
    ex = Executor(target, target, asyncio.Event(), execute=True)
    async def quick_pause(_):
        pass
    ex.pause = quick_pause
    obs = target.capture()
    with pytest.raises(ValueError, match="converge"):
        await ex.apply(plan(obs, [{"type": "move_to", "x": 500, "y": 300}, {"type": "click"}]), obs)
    assert not any(c["op"] == "click" for c in target.commands)


async def test_full_simulated_hid_roundtrip(tmp_path):
    target = SimulatedTarget()
    runner = Runner(target, DemoPlanner(), target, tmp_path/"demo", execute=True, token="secret-never-log-this", settle_ms=0)
    result = await runner.run("Fill and submit")
    assert result["status"] == "verified_demo"
    assert result["steps"] == 4
    assert target.value == "Hello ESP32-C3!" and target.submitted
    assert not target.armed
    assert len(list(runner.run_dir.glob("*.jpg"))) == 4
    assert "secret-never-log-this" not in (runner.run_dir/"events.jsonl").read_text()
    assert (runner.run_dir/"result.json").exists()


async def test_preview_does_not_touch_hardware(tmp_path):
    target = SimulatedTarget()
    runner = Runner(target, DemoPlanner(), target, tmp_path/"preview", execute=False)
    result = await runner.run("preview")
    assert result["status"] == "dry_run"
    assert not target.commands


async def test_stop_interrupts_pending_model_and_disarms(tmp_path):
    class SlowPlanner:
        async def plan(self, *args):
            await asyncio.sleep(60)
    target = SimulatedTarget()
    runner = Runner(target, SlowPlanner(), target, tmp_path/"stop", execute=True)
    task = asyncio.create_task(runner.run("wait"))
    await asyncio.sleep(0.1)
    runner.stop.set()
    result = await asyncio.wait_for(task, 1)
    assert result["status"] == "stopped"
    assert [c["op"] for c in target.commands] == ["disarm"]


async def test_stop_file(tmp_path):
    target = SimulatedTarget()
    runner = Runner(target, DemoPlanner(), target, tmp_path/"file-stop", execute=True)
    (runner.run_dir/"STOP").touch()
    result = await runner.run("wait")
    assert result["status"] == "stopped"
    assert [c["op"] for c in target.commands] == ["disarm"]


async def test_stop_file_interrupts_inflight_planning(tmp_path):
    entered = asyncio.Event()

    class SlowPlanner:
        async def plan(self, *args):
            entered.set()
            await asyncio.Event().wait()

    target = SimulatedTarget()
    runner = Runner(target, SlowPlanner(), target, tmp_path/"planning-file-stop", execute=True)
    task = asyncio.create_task(runner.run("wait"))
    await entered.wait()
    (runner.run_dir/"STOP").touch()
    result = await asyncio.wait_for(task, 1)
    assert result["status"] == "stopped"
    assert [c["op"] for c in target.commands] == ["disarm"]


async def test_delivery_timeout_does_not_retry_input(tmp_path):
    class TimeoutTarget(SimulatedTarget):
        async def request(self, command):
            if command["op"] == "move":
                self.commands.append(command)
                raise UncertainDelivery("lost ACK")
            return await super().request(command)
    target = TimeoutTarget()
    runner = Runner(target, DemoPlanner(), target, tmp_path/"timeout", execute=True)
    result = await runner.run("fill")
    assert result["status"] == "error"
    assert len([c for c in target.commands if c["op"] == "move"]) == 1
    assert not any(c["op"] == "click" for c in target.commands)
    assert target.commands[-1]["op"] == "disarm"
