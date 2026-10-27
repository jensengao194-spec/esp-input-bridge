import asyncio
import json
import os
import re
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from espcomputeruse.agents import HTTPPlanner
from espcomputeruse.server import create_app
from espcomputeruse.simulator import SimulatedTarget
from espcomputeruse.transports import BLETransport, SerialTransport, finish_transaction
from espcomputeruse.protocol import UncertainDelivery


@pytest.mark.parametrize("kind", ["http", "chat", "responses"])
async def test_http_agent_contract(kind):
    obs = SimulatedTarget().capture()
    result = {"observation_id": obs.id, "status": "act", "reason": "focus", "actions": [{"type": "click"}]}
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if kind == "http":
            assert body["observation"]["image_base64"]
            return httpx.Response(200, json=result)
        if kind == "chat":
            assert request.url.path == "/v1/chat/completions"
            assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
        assert request.url.path == "/v1/responses"
        assert body["store"] is False
        assert body["input"][0]["content"][1]["type"] == "input_image"
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(result)}]}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = HTTPPlanner(kind, "https://model.example/v1", "vision-model", client=client)
    assert (await agent.plan("goal", obs, [])).observation_id == obs.id
    assert len(seen) == 1
    await agent.close()


async def test_refused_or_incomplete_response_never_produces_action():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": "incomplete", "output": []})))
    agent = HTTPPlanner("responses", "https://model.example/v1", "vision", client=client)
    with pytest.raises(ValueError, match="complete"):
        await agent.plan("goal", SimulatedTarget().capture(), [])
    await agent.close()


async def test_ble_fragmentation_and_ack_routing():
    transport = BLETransport("fake", timeout=0.1)
    class Client:
        is_connected = True
        def __init__(self):
            self.buffer, self.sizes = bytearray(), []
        async def write_gatt_char(self, uuid, data, response):
            self.sizes.append(len(data))
            assert response is True
            self.buffer.extend(data)
            if data.endswith(b"\n"):
                req = json.loads(self.buffer)
                ack = json.dumps({"v": 1, "id": req["id"], "ok": True}).encode()+b"\n"
                # An unrelated response must not be mistaken for the current transaction.
                transport._notify(None, b'{"v":1,"id":"old","ok":true}\n')
                for i in range(0, len(ack), 7):
                    transport._notify(None, ack[i:i+7])
    transport.client = Client()
    assert (await transport.request({"op": "arm", "token": "x"*64}))["ok"]
    assert max(transport.client.sizes) <= 20


async def test_ble_missing_ack_not_replayed():
    transport = BLETransport("fake", timeout=0.01)
    class Client:
        is_connected = True
        calls = 0
        async def write_gatt_char(self, uuid, data, response):
            self.calls += 1
    transport.client = Client()
    with pytest.raises(UncertainDelivery):
        await transport.request({"op": "click", "button": 1})
    assert transport.client.calls == 4  # Exactly one frame, fragmented into 20-byte writes.


async def test_cancel_finishes_frame_before_cleanup():
    events = []
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def transaction():
        events.append("frame-start")
        entered.set()
        await proceed.wait()
        events.append("frame-end")
    async def owner():
        try:
            await finish_transaction(transaction())
        finally:
            events.append("disarm")
    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert events == ["frame-start"]
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["frame-start", "frame-end", "disarm"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY integration")
async def test_real_serial_transport_over_pseudoterminal():
    import pty
    master, slave = pty.openpty()
    transport = SerialTransport(os.ttyname(slave), timeout=0.5)
    transport.ready = True
    captured = []
    def device():
        buf = bytearray()
        while not buf.endswith(b"\n"):
            buf.extend(os.read(master, 256))
        req = json.loads(buf)
        captured.append(req)
        os.write(master, b"boot message\n")
        ack = json.dumps({"v": 1, "id": req["id"], "ok": True}).encode()+b"\n"
        os.write(master, ack[:8])
        os.write(master, ack[8:])
    worker = threading.Thread(target=device, daemon=True)
    worker.start()
    try:
        assert (await transport.request({"op": "key", "mod": 0, "keys": [4]}))["ok"]
        worker.join(timeout=1)
        assert captured[0]["keys"] == [4]
    finally:
        await transport.close()
        os.close(master)
        os.close(slave)


def test_dashboard_auth_origin_demo_and_stop(tmp_path):
    with TestClient(create_app(output=tmp_path)) as client:
        html = client.get("/").text
        token = re.search(r"const token='([^']+)'", html).group(1)
        headers = {"X-Session-Token": token}
        assert client.post("/api/start", json={}).status_code == 403
        assert client.post("/api/start", json={}, headers={**headers, "Origin": "https://foreign.example"}).status_code == 403
        assert client.get("/api/status", headers={**headers, "Host": "attacker.example"}).status_code == 400
        start = client.post("/api/start", json={"demo": True, "goal": "test"}, headers=headers)
        assert start.status_code == 200
        assert client.post("/api/start", json={}, headers=headers).status_code == 409
        assert client.post("/api/stop", json={}, headers=headers).status_code == 200
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            state = client.get("/api/status", headers=headers).json()
            if state["run"]["status"] == "stopped":
                break
            time.sleep(0.02)
        assert state["run"]["status"] == "stopped"
        assert state["hardware_enabled"] is False
