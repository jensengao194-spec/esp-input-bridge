"""Thin executor for an existing agent: no model, goal planner, or API calls."""
from __future__ import annotations

import asyncio
import json
import os
import tomllib
import uuid
from pathlib import Path

from .device import probe
from .executor import Executor, Stopped
from .models import Action, Plan
from .observation import DesktopObserver
from .simulator import SimulatedTarget
from .transports import BLETransport, SerialTransport


class InputAdapter:
    def __init__(self, observer, transport, *, mode="demo", token="", output="auto",
                 output_dir=None, max_age=120, settle_ms=150):
        if mode not in ("demo", "preview", "hardware") or not 0 <= settle_ms <= 5000:
            raise ValueError("invalid bridge mode or settle_ms")
        if not 1 <= max_age <= 600:
            raise ValueError("max_age must be 1..600")
        self.observer, self.transport, self.mode = observer, transport, mode
        self.stop_event, self.lock = asyncio.Event(), asyncio.Lock()
        self.latest = None
        self.device = {"chip": "simulated", "outputs": ["simulated"]} if mode == "demo" else None
        self.settle_ms = settle_ms
        self.run_dir = None
        if output_dir:
            self.run_dir = Path(output_dir) / uuid.uuid4().hex
            self.run_dir.mkdir(parents=True, mode=0o700)
            self.run_dir.chmod(0o700)
        self.executor = Executor(transport, observer, self.stop_event, token=token,
                                 execute=mode != "preview", max_age=max_age, audit=self.audit,
                                 stop_file=self.run_dir / "STOP" if self.run_dir else None, output=output)

    def audit(self, kind, data):
        if not self.run_dir:
            return
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "data": data}, ensure_ascii=False)+"\n")

    async def identify(self):
        if self.mode == "hardware":
            self.device = (await probe(self.transport)).model_dump()
            if self.executor.output not in ("auto", *self.device["outputs"]):
                raise ValueError(f"{self.device['chip']} does not support {self.executor.output} HID")

    async def _capture(self):
        self.latest = await asyncio.to_thread(self.observer.capture)
        if self.run_dir:
            (self.run_dir / f"{self.latest.id}.jpg").write_bytes(self.latest.jpeg())
        self.audit("observation", self.summary())
        return self.latest

    def summary(self):
        data = {"mode": self.mode, "stopped": self.stop_event.is_set(), "device": self.device}
        if self.latest:
            o = self.latest
            data.update(observation_id=o.id, width=o.image.width, height=o.image.height,
                        cursor=o.cursor, changed=round(o.changed, 5))
            if o.context:
                data["context"] = o.context[:2000]
        return data

    async def observe(self):
        async with self.lock:
            await self.identify()
            return await self._capture()

    async def act(self, observation_id: str, actions: list[Action]):
        async with self.lock:
            self.executor.check_stop()
            if self.latest is None:
                raise ValueError("call observe first")
            plan = Plan(observation_id=observation_id, status="act", reason="Agent input batch", actions=actions)
            # Reject invalid input before opening or arming any hardware.
            self.executor.prepare(plan, self.latest)
            try:
                if self.mode == "hardware":
                    await self.identify()
                await self.executor.apply(plan, self.latest)
                await self.executor.pause(self.settle_ms/1000)
            except BaseException:
                self.stop_event.set()
                raise
            finally:
                await self.executor.release()
            return await self._capture()

    async def stop(self):
        self.stop_event.set()
        await self.executor.release()
        return {"mode": self.mode, "stopped": True}

    async def close(self):
        await self.stop()
        await self.transport.close()


def load_adapter(config_path=None, *, demo=False):
    path = Path(config_path or os.environ.get("ESP_INPUT_CONFIG", "~/.config/esp-input-bridge/config.toml")).expanduser()
    if demo or (config_path is None and not path.exists()):
        target = SimulatedTarget()
        return InputAdapter(target, target, mode="demo")
    with path.open("rb") as f:
        config = tomllib.load(f)
    b, o, t = (config.get(k, {}) for k in ("bridge", "observation", "transport"))
    mode = b.get("mode", "demo")
    output_dir = b.get("output_dir")
    if output_dir:
        output_dir = path.parent / output_dir
    options = dict(mode=mode, output=t.get("output", "auto"), output_dir=output_dir,
                   max_age=b.get("max_age", 120), settle_ms=b.get("settle_ms", 150))
    if mode == "demo":
        target = SimulatedTarget()
        return InputAdapter(target, target, **options)
    context = path.parent / o["context_file"] if o.get("context_file") else None
    observer = DesktopObserver(o.get("monitor", 1), o.get("region"), o.get("max_width", 1280), context)
    if mode == "preview":
        from .config import NoTransport
        return InputAdapter(observer, NoTransport(), **options)
    if mode != "hardware":
        raise ValueError("bridge.mode must be demo, preview or hardware")
    token = os.environ.get(t.get("token_env", "ESP_HID_TOKEN"), "")
    if not 32 <= len(token) <= 96:
        raise ValueError("set the token_env environment variable to the provisioned 32..96 character token")
    if t.get("kind", "serial") == "serial":
        transport = SerialTransport(t["port"], t.get("baudrate", 115200), t.get("timeout", 3))
    elif t.get("kind") == "ble":
        transport = BLETransport(t["address"], t.get("timeout", 3))
    else:
        raise ValueError("transport.kind must be serial or ble")
    return InputAdapter(observer, transport, token=token, **options)
