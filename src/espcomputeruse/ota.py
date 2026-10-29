"""Authenticated OTA over the existing BLE/serial control channel; no HID input."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from pathlib import Path


def inspect_image(path: str | Path) -> tuple[bytes, str, str]:
    data = Path(path).read_bytes()
    if len(data) < 256 or data[0] != 0xE9:
        raise ValueError("expected an ESP application firmware.bin, not a ZIP/merged image")
    chip = {5: "ESP32-C3", 9: "ESP32-S3"}.get(int.from_bytes(data[12:14], "little"))
    if not chip or int.from_bytes(data[32:36], "little") != 0xABCD5432:
        raise ValueError("unsupported chip or missing ESP application descriptor")
    return data, chip, data[176:208].hex()


async def upload(transport, image: str | Path, token: str, progress=None) -> dict:
    data, chip, elf_hash = inspect_image(image)
    before = await transport.request({"op": "ota_status"})
    if before["chip"] != chip:
        raise ValueError("image chip does not match connected device")
    if len(data) > before["slot_bytes"]:
        raise ValueError("image exceeds inactive OTA slot")
    if before["pending_verify"]:
        raise ValueError("current firmware must be confirmed before another update")
    await transport.request({"op": "disarm"})
    begin = await transport.request({"op": "ota_begin", "token": token,
                                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    session = begin["session"]
    chunk_size = min(240, begin["chunk_bytes"])
    started = time.monotonic()
    binary = bool(before.get("ota_binary") and hasattr(transport, "ota_stream") and transport.ota_packet_size() > 20)
    # A failed link is never reconnected/replayed. The device aborts on
    # disconnect or 30-second inactivity without changing its boot slot.
    if binary:
        await transport.ota_stream(session, data, begin["binary_window"], progress)
    else:
        for offset in range(0, len(data), chunk_size):
            chunk = data[offset:offset + chunk_size]
            reply = await transport.request({"op": "ota_chunk", "session": session, "offset": offset,
                                             "data": base64.b64encode(chunk).decode("ascii")})
            if reply.get("received") != offset + len(chunk):
                raise RuntimeError("OTA offset acknowledgement mismatch")
            if progress:
                progress(offset + len(chunk), len(data))
    await transport.request({"op": "ota_finish", "session": session})
    elapsed = time.monotonic() - started
    return {"target_slot": begin["target_slot"], "elf_sha256": elf_hash,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "mode": "ble_binary" if binary else "json", "transfer_seconds": round(elapsed, 3),
            "bytes_per_second": round(len(data) / elapsed)}


async def verify_boot(factory, expected: dict, token: str, *, confirm=True, timeout=45) -> dict:
    """Reconnect only after an acknowledged finish; poll read-only boot status."""
    deadline = time.monotonic() + timeout
    last_error = None
    await asyncio.sleep(3)
    while time.monotonic() < deadline:
        transport = factory()
        try:
            try:
                status = await transport.request({"op": "ota_status"})
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(2)
                continue
            if status["running_slot"] != expected["target_slot"] or status["elf_sha256"] != expected["elf_sha256"]:
                raise RuntimeError("booted slot or ELF hash does not match uploaded firmware")
            if confirm:
                await transport.request({"op": "ota_confirm", "token": token})
                status = await transport.request({"op": "ota_status"})
                if status["pending_verify"]:
                    raise RuntimeError("firmware confirmation did not persist")
            return status
        finally:
            await transport.close()
    raise TimeoutError(f"new firmware did not become available: {last_error}")
