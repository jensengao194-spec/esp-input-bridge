from __future__ import annotations

import asyncio
import json
import time
import sys

from .protocol import COMMAND_UUID, REPLY_UUID, SERVICE_UUID, OTA_DATA_UUID, MAX_LINE, TransportError, UncertainDelivery, frame, check_reply


async def finish_transaction(awaitable):
    """Do not interleave disarm with an in-flight frame on task cancellation."""
    transaction = asyncio.create_task(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(transaction)
        except asyncio.CancelledError:
            if transaction.cancelled():
                raise
            # A second cancellation must not cancel the draining frame either:
            # to_thread() can leave the serial worker running after task cancellation.
            cancelled = True
            continue
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        if cancelled:
            raise asyncio.CancelledError
        return result


class SerialTransport:
    def __init__(self, port: str, baudrate=115200, timeout=3.0):
        import serial
        # Avoid the USB Serial/JTAG reset/download combination while opening C3.
        self.serial = serial.Serial(None, baudrate, timeout=0.05, write_timeout=timeout)
        self.serial.dtr = True
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.ready = False
        self._closed = False

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
                chunk = self.serial.read(min(512, self.serial.in_waiting or 1))
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
            if self._closed:
                raise TransportError("serial transport is closed")
            return await finish_transaction(asyncio.to_thread(self._request, command))

    async def close(self):
        async with self.lock:
            self._closed = True
            await finish_transaction(asyncio.to_thread(self.serial.close))


class BLETransport:
    def __init__(self, address: str, timeout=3.0):
        self.address, self.timeout = address, timeout
        self.client = None
        self.buffer = bytearray()
        self._pending_id = None
        self._pending_reply = None
        self.lock = asyncio.Lock()
        self.discard = False
        self._closed = False

    def _notify(self, _, data):
        # Characteristic packets are arbitrary byte chunks, not complete JSON frames.
        for byte in data:
            if byte == 10:
                if not self.discard:
                    try:
                        reply = json.loads(self.buffer)
                        if (isinstance(reply, dict) and reply.get("id") == self._pending_id
                                and self._pending_reply is not None and not self._pending_reply.done()):
                            self._pending_reply.set_result(reply)
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

    def _on_disconnect(self, client):
        if client is not self.client:
            return
        self.buffer.clear()
        self.discard = False
        if self._pending_reply is not None and not self._pending_reply.done():
            # A sentinel avoids an unobserved Future exception if a write also fails.
            self._pending_reply.set_result(None)

    async def _connect(self):
        if self.client is not None:
            if not self.client.is_connected:
                raise UncertainDelivery("BLE disconnected; refusing automatic reconnect/replay")
            return
        from bleak import BleakClient
        target = self.address
        if sys.platform == "darwin":
            # macOS keeps the HID link alive after a control client exits. Such
            # devices may stop advertising and cannot be found by Bleak's scan.
            # Retrieve the exact known UUID, never choose a different device.
            from Foundation import NSUUID
            from bleak.backends.device import BLEDevice
            from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
            manager = CentralManagerDelegate()
            await manager.wait_until_ready()
            identifier = NSUUID.alloc().initWithUUIDString_(self.address)
            if identifier is None:
                raise ValueError("macOS BLE address must be a CoreBluetooth UUID")
            peripherals = manager.central_manager.retrievePeripheralsWithIdentifiers_([identifier])
            if peripherals:
                peripheral = peripherals[0]
                target = BLEDevice(self.address, peripheral.name(), (peripheral, manager))
        self.client = BleakClient(target, services=[SERVICE_UUID], timeout=max(15, self.timeout),
                                  disconnected_callback=self._on_disconnect)
        await self.client.connect()
        # Authenticated GATT requires OS pairing during the USB-opened setup window.
        # An empty frame is harmless and triggers the OS pairing dialog. macOS
        # Bleak characteristic reads have a fixed 20s timeout, too short for PIN entry.
        await self.client.write_gatt_char(COMMAND_UUID, b"\n", response=True)
        await self.client.start_notify(REPLY_UUID, self._notify)

    async def request(self, command):
        async with self.lock:
            if self._closed:
                raise TransportError("BLE transport is closed")
            await self._connect()
            return await finish_transaction(self._exchange(command))

    async def _exchange(self, command):
        request, encoded = frame(command)
        pending = asyncio.get_running_loop().create_future()
        self._pending_id, self._pending_reply = request["id"], pending
        try:
            async with asyncio.timeout(self.timeout):
                packet = max(20, min(244, getattr(self.client, "mtu_size", 23) - 3))
                response = True
                services = getattr(self.client, "services", None)
                characteristic = services.get_characteristic(COMMAND_UUID) if services else None
                if characteristic and "write-without-response" in getattr(characteristic, "properties", ()):
                    response = False
                    packet = min(packet, characteristic.max_write_without_response_size)
                for offset in range(0, len(encoded), packet):
                    await self.client.write_gatt_char(COMMAND_UUID, encoded[offset:offset+packet], response=response)
                reply = await pending
                if reply is None:
                    raise UncertainDelivery("BLE disconnected before ACK; input state unknown, no retry")
                return check_reply(reply, request["id"])
        except (TimeoutError, OSError) as exc:
            raise UncertainDelivery("BLE write/ACK failed; input state unknown, no retry") from exc
        except Exception as exc:
            if isinstance(exc, TransportError):
                raise
            raise UncertainDelivery("BLE transaction failed; input state unknown, no retry") from exc
        finally:
            self._pending_id, self._pending_reply = None, None
            pending.cancel()

    def ota_packet_size(self):
        characteristic = self.client.services.get_characteristic(OTA_DATA_UUID)
        if characteristic is None:
            return 0
        return min(512, self.client.mtu_size - 3, characteristic.max_write_without_response_size)

    async def ota_stream(self, session, data, window, progress=None):
        """Binary packets with offset + session; one checkpoint per bounded window."""
        window = min(8, max(1, window))
        session_bytes = bytes.fromhex(session)
        if len(session_bytes) != 16:
            raise ValueError("invalid OTA session nonce")
        async with self.lock:
            if self._closed:
                raise TransportError("BLE transport is closed")
            await self._connect()
            payload = self.ota_packet_size() - 20
            if payload <= 0:
                raise ValueError("BLE MTU too small for binary OTA")
            for start in range(0, len(data), payload * window):
                end = min(len(data), start + payload * window)
                async def send_window():
                    async with asyncio.timeout(self.timeout):
                        for offset in range(start, end, payload):
                            packet = session_bytes + offset.to_bytes(4, "little") + data[offset:min(end, offset + payload)]
                            await self.client.write_gatt_char(OTA_DATA_UUID, packet, response=False)
                        ack = await self._exchange({"op": "ota_sync", "session": session, "offset": end})
                        if ack.get("received") != end:
                            raise UncertainDelivery("OTA stream offset mismatch; no replay")
                try:
                    await finish_transaction(send_window())
                except (TimeoutError, OSError) as exc:
                    raise UncertainDelivery("OTA binary write/checkpoint failed; no replay") from exc
                if progress:
                    progress(end, len(data))

    async def close(self):
        async with self.lock:
            self._closed = True
            if self.client and self.client.is_connected:
                await finish_transaction(self.client.disconnect())
