from __future__ import annotations

import asyncio
import json
import time

from .protocol import COMMAND_UUID, REPLY_UUID, SERVICE_UUID, MAX_LINE, UncertainDelivery, frame, check_reply


async def finish_transaction(awaitable):
    """Do not interleave disarm with an in-flight frame on task cancellation."""
    transaction = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(transaction)
    except asyncio.CancelledError:
        try:
            await transaction
        except Exception:
            pass  # Original cancellation takes priority; caller will attempt disarm.
        raise


class SerialTransport:
    def __init__(self, port: str, baudrate=115200, timeout=3.0):
        import serial
        self.serial = serial.Serial(port, baudrate, timeout=0.05, write_timeout=timeout)
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.ready = False

    def _request(self, command):
        request, encoded = frame(command)
        try:
            if not self.ready:
                # Opening some USB-UART boards resets them. Only wait before the first request.
                time.sleep(2)
                self.serial.reset_input_buffer()
                self.ready = True
            self.serial.write(encoded)
            self.serial.flush()
            deadline, buffer = time.monotonic() + self.timeout, bytearray()
            while time.monotonic() < deadline:
                chunk = self.serial.read(256)
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, _, tail = buffer.partition(b"\n")
                    buffer = bytearray(tail)
                    try:
                        reply = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        continue  # ROM/boot logging is not protocol data.
                    if isinstance(reply, dict) and reply.get("id") == request["id"]:
                        return check_reply(reply, request["id"])
                if len(buffer) > MAX_LINE:
                    raise UncertainDelivery("oversized serial reply; input state unknown")
        except (OSError, TimeoutError) as exc:
            raise UncertainDelivery("serial I/O failed; input state unknown, no retry") from exc
        raise UncertainDelivery("serial ACK timeout; input may have executed, no retry")

    async def request(self, command):
        async with self.lock:
            return await finish_transaction(asyncio.to_thread(self._request, command))

    async def close(self):
        await asyncio.to_thread(self.serial.close)


class BLETransport:
    def __init__(self, address: str, timeout=3.0):
        self.address, self.timeout = address, timeout
        self.client = None
        self.buffer = bytearray()
        self.replies = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.discard = False

    def _notify(self, _, data):
        # Characteristic packets are arbitrary byte chunks, not complete JSON frames.
        for byte in data:
            if byte == 10:
                if not self.discard:
                    try:
                        reply = json.loads(self.buffer)
                        if isinstance(reply, dict):
                            self.replies.put_nowait(reply)
                    except (ValueError, UnicodeDecodeError):
                        pass
                self.buffer.clear()
                self.discard = False
            elif not self.discard:
                if len(self.buffer) >= MAX_LINE:
                    self.buffer.clear()
                    self.discard = True
                else:
                    self.buffer.append(byte)

    async def _connect(self):
        if self.client is not None:
            if not self.client.is_connected:
                raise UncertainDelivery("BLE disconnected; refusing automatic reconnect/replay")
            return
        from bleak import BleakClient
        self.client = BleakClient(self.address, services=[SERVICE_UUID])
        await self.client.connect()
        # Authenticated GATT requires OS pairing during the USB-opened setup window.
        await self.client.start_notify(REPLY_UUID, self._notify)

    async def request(self, command):
        async with self.lock:
            await self._connect()
            return await finish_transaction(self._exchange(command))

    async def _exchange(self, command):
        request, encoded = frame(command)
        try:
            async with asyncio.timeout(self.timeout):
                for offset in range(0, len(encoded), 20):
                    await self.client.write_gatt_char(COMMAND_UUID, encoded[offset:offset+20], response=True)
                while True:
                    reply = await self.replies.get()
                    if reply.get("id") == request["id"]:
                        return check_reply(reply, request["id"])
        except (TimeoutError, OSError) as exc:
            raise UncertainDelivery("BLE write/ACK failed; input state unknown, no retry") from exc
        except Exception as exc:
            from .protocol import TransportError
            if isinstance(exc, TransportError):
                raise
            raise UncertainDelivery("BLE transaction failed; input state unknown, no retry") from exc

    async def close(self):
        if self.client:
            await self.client.disconnect()
