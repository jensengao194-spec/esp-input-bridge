from __future__ import annotations

import asyncio
import math
import time

from .hid import chord, text_reports
from .models import MoveTo, Move, Click, Scroll, Key, TypeText, Wait


class Stopped(RuntimeError):
    pass


class Executor:
    def __init__(self, transport, observer, stop: asyncio.Event, token="", execute=False,
                 max_age=120, audit=lambda *args: None, stop_file=None, output="auto"):
        self.transport, self.observer, self.stop = transport, observer, stop
        self.token, self.execute, self.max_age, self.audit = token, execute, max_age, audit
        self.stop_file = stop_file
        self.consumed = set()
        if output not in ("auto", "ble", "usb"):
            raise ValueError("HID output must be auto, ble or usb")
        self.output = output

    def check_stop(self):
        if self.stop.is_set() or (self.stop_file and self.stop_file.exists()):
            raise Stopped("stop requested")

    async def send(self, command):
        self.check_stop()
        if not self.execute:
            self.audit("dry_run", command)
            return {}
        self.audit("send", {k: v for k, v in command.items() if k != "token"})
        reply = await self.transport.request(command)
        self.audit("ack", reply)
        return reply

    async def pause(self, seconds):
        # Also notice the STOP file between short waits.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            try:
                await asyncio.wait_for(self.stop.wait(), min(0.05, max(0.001, deadline-time.monotonic())))
            except TimeoutError:
                pass
        self.check_stop()

    def prepare(self, plan, observation):
        if plan.observation_id != observation.id or observation.id in self.consumed:
            raise ValueError("stale, mismatched or already-consumed observation")
        if time.monotonic() - observation.captured_at > self.max_age:
            raise ValueError("observation expired; recapture before acting")
        commands = []
        for action in plan.actions:
            if isinstance(action, MoveTo):
                if observation.cursor is None:
                    raise ValueError("move_to requires cursor feedback on the target desktop")
                commands.append(("target", observation.target(action.x, action.y)))
            elif isinstance(action, Move):
                commands.append(("wire", {"op": "move", "dx": action.dx, "dy": action.dy}))
            elif isinstance(action, Click):
                commands.extend(("wire", {"op": "click", "button": {"left": 1, "right": 2, "middle": 4}[action.button]})
                                for _ in range(action.count))
            elif isinstance(action, Scroll):
                commands.append(("wire", {"op": "scroll", "steps": action.steps}))
            elif isinstance(action, Key):
                commands.append(("wire", chord(action.keys)))
            elif isinstance(action, TypeText):
                commands.extend(("wire", c) for c in text_reports(action.text))
            elif isinstance(action, Wait):
                commands.append(("wait", action.ms/1000))
        # Validate the entire batch before any side effect (including unsupported Unicode).
        return commands

    async def move_to(self, target):
        if not self.execute:
            self.audit("dry_run_target", {"x": target[0], "y": target[1]})
            return
        for _ in range(60):
            self.check_stop()
            cursor = await asyncio.to_thread(self.observer.cursor)
            if cursor is None:
                raise ValueError("cursor feedback unavailable; stopping before click")
            dx, dy = target[0]-cursor[0], target[1]-cursor[1]
            if math.hypot(dx, dy) <= 3:
                return
            # Conservative closed loop: acceleration/Retina scaling are not assumed to be 1:1.
            def step(delta):
                return max(-80, min(80, round(delta*0.55))) if abs(delta) >= 2 else 0
            await self.send({"op": "move", "dx": step(dx), "dy": step(dy)})
            await self.pause(0.04)
        raise ValueError("pointer failed to converge; no following click executed")

    async def apply(self, plan, observation):
        self.check_stop()
        commands = self.prepare(plan, observation)
        self.consumed.add(observation.id)
        if commands:
            await self.send({"op": "arm", "token": self.token, "output": self.output})
        for kind, value in commands:
            self.check_stop()
            if kind == "target":
                await self.move_to(value)
            elif kind == "wait":
                await self.pause(value)
            else:
                await self.send(value)
        return len(commands)

    async def release(self):
        if self.execute:
            # Cleanup must bypass the stop flag. Never replay the original input.
            try:
                async with asyncio.timeout(5):
                    await self.transport.request({"op": "disarm"})
                self.audit("disarmed", {})
            except Exception as exc:
                self.audit("cleanup_error", {"error": str(exc)})
