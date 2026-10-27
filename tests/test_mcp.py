import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from espcomputeruse.adapter import InputAdapter
from espcomputeruse.device import DeviceInfo, probe
from espcomputeruse.models import Plan
from espcomputeruse.simulator import SimulatedTarget


def actions(obs, raw):
    return Plan.model_validate({"observation_id": obs.id, "status": "act", "reason": "test", "actions": raw}).actions


@pytest.mark.parametrize("chip,usb,expected", [("ESP32-C3", False, ["ble"]), ("ESP32-S3", True, ["ble", "usb"])])
async def test_chip_probe_and_capabilities(chip, usb, expected):
    target = SimulatedTarget(chip, usb_ready=usb)
    info = await probe(target)
    assert info.chip == chip and info.outputs == expected
    assert info.token_configured


def test_c3_cannot_claim_usb_hid():
    with pytest.raises(ValidationError):
        DeviceInfo.model_validate({"chip": "ESP32-C3", "firmware": "0.2.0", "outputs": ["ble", "usb"],
                                   "ble_ready": True, "usb_ready": True, "active_output": "none", "token_configured": True,
                                   "ble_security": "sc_mitm_bonded"})


def test_old_unauthenticated_ble_firmware_is_rejected():
    with pytest.raises(ValidationError):
        DeviceInfo.model_validate({"chip": "ESP32-S3", "firmware": "0.2.0", "outputs": ["ble", "usb"],
                                   "ble_ready": True, "usb_ready": True, "active_output": "none", "token_configured": True})


async def test_adapter_rejects_usb_on_c3_before_input():
    target = SimulatedTarget()
    adapter = InputAdapter(target, target, mode="hardware", output="usb", token="x"*32)
    with pytest.raises(ValueError, match="does not support"):
        await adapter.observe()
    assert [c["op"] for c in target.commands] == ["ping"]


@pytest.mark.parametrize("output", ["auto", "usb", "ble"])
async def test_s3_output_selection_and_release(output):
    target = SimulatedTarget("ESP32-S3", usb_ready=True)
    adapter = InputAdapter(target, target, mode="hardware", output=output, token="x"*32, settle_ms=0)
    obs = await adapter.observe()
    await adapter.act(obs.id, actions(obs, [{"type": "move", "dx": 5, "dy": 7}]))
    arm = next(c for c in target.commands if c["op"] == "arm")
    assert arm["output"] == output
    assert not target.armed
    assert target.commands[-1]["op"] == "disarm"
    await adapter.close()


async def test_adapter_invalid_plan_does_not_arm_or_stop():
    target = SimulatedTarget()
    adapter = InputAdapter(target, target, settle_ms=0)
    obs = await adapter.observe()
    with pytest.raises(ValueError, match="ASCII"):
        await adapter.act(obs.id, actions(obs, [{"type": "type_text", "text": "中文"}]))
    assert not target.commands and not adapter.stop_event.is_set()
    await adapter.act(obs.id, actions(obs, [{"type": "move", "dx": 3, "dy": 0}]))


async def test_adapter_stop_is_terminal():
    target = SimulatedTarget()
    adapter = InputAdapter(target, target)
    obs = await adapter.observe()
    await adapter.stop()
    with pytest.raises(RuntimeError, match="stop requested"):
        await adapter.act(obs.id, actions(obs, [{"type": "click"}]))


async def test_real_stdio_mcp_roundtrip():
    params = StdioServerParameters(command=sys.executable, args=["-m", "espcomputeruse.mcp_server", "--demo"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == {"observe", "act", "stop"}
            assert len(json.dumps([t.model_dump() for t in tools])) < 10000
            result = await session.call_tool("observe", {"image": True})
            assert not result.isError
            assert [c.type for c in result.content] == ["text", "image"]
            assert "image_base64" not in result.content[0].text
            state = json.loads(result.content[0].text)
            for batch in [
                [{"type": "move_to", "x": 180, "y": 245}, {"type": "click"}],
                [{"type": "type_text", "text": "Hello ESP32-C3!"}],
                [{"type": "move_to", "x": 130, "y": 340}, {"type": "click"}],
            ]:
                result = await session.call_tool("act", {"observation_id": state["observation_id"], "actions": batch})
                assert not result.isError, result
                assert [c.type for c in result.content] == ["text"]
                state = json.loads(result.content[0].text)
            assert state["mode"] == "demo"
            target_state = json.loads(state["context"])
            assert target_state["submitted"] and target_state["value"] == "Hello ESP32-C3!"
            result = await session.call_tool("stop", {})
            assert not result.isError
            result = await session.call_tool("act", {"observation_id": state["observation_id"], "actions": [{"type": "click"}]})
            assert result.isError
