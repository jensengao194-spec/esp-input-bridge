import asyncio
import json

import pytest

from espcomputeruse.protocol import MAX_LINE, TransportError, UncertainDelivery
from espcomputeruse.transports import BLETransport, finish_transaction


async def test_repeated_cancellation_does_not_interrupt_frame_or_cleanup_early():
    entered, proceed = asyncio.Event(), asyncio.Event()
    events = []

    async def transaction():
        events.append("frame-start")
        entered.set()
        try:
            await proceed.wait()
            events.append("frame-end")
        finally:
            events.append("transaction-finished")

    async def owner():
        try:
            await finish_transaction(transaction())
        finally:
            events.append("disarm")

    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    snapshot = list(events)
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert snapshot == ["frame-start"]
    assert events == ["frame-start", "frame-end", "transaction-finished", "disarm"]


async def test_cancellation_preserved_when_transaction_fails():
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def transaction():
        entered.set()
        await proceed.wait()
        raise OSError("device disconnected")

    task = asyncio.create_task(finish_transaction(transaction()))
    await entered.wait()
    task.cancel()
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task


class Client:
    is_connected = True

    def __init__(self, transport):
        self.transport = transport
        self.frame = bytearray()
        self.entered = asyncio.Event()
        self.request = None
        self.events = []

    async def write_gatt_char(self, uuid, data, response):
        self.frame.extend(data)
        if self.frame.endswith(b"\n"):
            self.request = json.loads(self.frame)
            self.frame.clear()
            self.events.append("request")
            self.entered.set()

    def reply(self):
        self.events.append("ack")
        data = json.dumps({"v": 1, "id": self.request["id"], "ok": True}).encode() + b"\n"
        self.transport._notify(None, data)

    async def disconnect(self):
        self.events.append("disconnect")
        self.is_connected = False


async def test_close_waits_for_inflight_ack_and_is_terminal():
    transport = BLETransport("fake", timeout=1)
    client = transport.client = Client(transport)
    sending = asyncio.create_task(transport.request({"op": "ping"}))
    await client.entered.wait()
    closing = asyncio.create_task(transport.close())
    await asyncio.sleep(0)
    before_ack = list(client.events)
    client.reply()
    try:
        assert (await sending)["ok"]
        await closing
        assert before_ack == ["request"]
        assert client.events == ["request", "ack", "disconnect"]
        with pytest.raises(TransportError, match="closed"):
            await transport.request({"op": "click", "button": 1})
        assert client.events == ["request", "ack", "disconnect"]
    finally:
        await asyncio.gather(sending, closing, return_exceptions=True)


async def test_close_before_connect_never_opens_link():
    transport = BLETransport("fake")
    connected = False

    async def connect():
        nonlocal connected
        connected = True
        raise AssertionError("closed transport attempted connection")

    transport._connect = connect
    await transport.close()
    with pytest.raises(TransportError, match="closed"):
        await transport.request({"op": "ping"})
    assert not connected


async def test_disconnect_wakes_ack_wait_without_replay():
    transport = BLETransport("fake", timeout=5)
    client = transport.client = Client(transport)
    sending = asyncio.create_task(transport.request({"op": "click", "button": 1}))
    await client.entered.wait()
    client.is_connected = False
    try:
        transport._on_disconnect(client)
        with pytest.raises(UncertainDelivery, match="disconnected"):
            await asyncio.wait_for(sending, .5)
        with pytest.raises(UncertainDelivery, match="disconnected"):
            await transport.request({"op": "click", "button": 1})
        assert client.events == ["request"]
    finally:
        if not sending.done():
            sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)


async def test_ble_ignores_stale_duplicate_and_oversized_notifications():
    transport = BLETransport("fake", timeout=1)
    client = transport.client = Client(transport)
    # Unsolicited replies must not affect a later request.
    for _ in range(1000):
        transport._notify(None, b'{"v":1,"id":"old","ok":true}\n')
    sending = asyncio.create_task(transport.request({"op": "ping"}))
    await client.entered.wait()
    transport._notify(None, b"x" * (MAX_LINE + 10) + b"\n")
    transport._notify(None, b"\xff\n[]\nnull\n")
    client.reply()
    client.reply()
    assert (await sending)["ok"]
