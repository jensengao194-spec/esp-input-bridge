from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .executor import Executor, Stopped


class Runner:
    def __init__(self, observer, planner, transport, run_dir: Path, execute=False, token="",
                 max_steps=20, max_age=120, settle_ms=200, output="auto"):
        if not 1 <= max_steps <= 200 or not 0 <= settle_ms <= 5000 or not 1 <= max_age <= 600:
            raise ValueError("invalid runner bounds")
        self.observer, self.planner, self.transport = observer, planner, transport
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.run_dir.chmod(0o700)
        self.stop = asyncio.Event()
        self.max_steps, self.settle_ms = max_steps, settle_ms
        self.events, self.latest = [], None
        from .simulator import SimulatedTarget
        mode = "simulation" if isinstance(observer, SimulatedTarget) else "hardware" if execute else "preview"
        self.state = {"status": "ready", "steps": 0, "execute": execute, "mode": mode, "run_dir": str(self.run_dir)}
        self.executor = Executor(transport, observer, self.stop, token, execute, max_age, self.audit,
                                 self.run_dir / "STOP", output=output)

    def audit(self, kind, data):
        event = {"time": time.time(), "kind": kind, "data": data}
        self.events.append(event)
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def capture(self, step):
        observation = await asyncio.to_thread(self.observer.capture)
        self.latest = observation
        (self.run_dir / f"{step:03d}.jpg").write_bytes(observation.jpeg())
        self.audit("observation", observation.metadata())
        return observation

    async def run(self, goal):
        if self.state["status"] != "ready":
            raise ValueError("runner is single-use")
        self.state["status"] = "running"
        self.audit("start", {"goal": goal, "execute": self.executor.execute})
        history = []
        try:
            for step in range(self.max_steps):
                self.executor.check_stop()
                observation = await self.capture(step)
                # Stop remains responsive while a remote model is generating.
                planning = asyncio.create_task(self.planner.plan(goal, observation, history))
                try:
                    while not planning.done():
                        await self.executor.pause(0.05)
                    plan = await planning
                finally:
                    if not planning.done():
                        planning.cancel()
                        await asyncio.gather(planning, return_exceptions=True)
                self.executor.check_stop()
                self.executor.prepare(plan, observation)
                self.audit("plan", plan.model_dump())
                self.state.update(steps=step+1, reason=plan.reason)
                if plan.status != "act":
                    self.state["status"] = "agent_done" if plan.status == "done" else "blocked"
                    break
                await self.executor.apply(plan, observation)
                history.append(plan.model_dump())
                if not self.executor.execute:
                    self.state["status"] = "dry_run"
                    break
                await self.executor.pause(self.settle_ms/1000)
            else:
                self.state["status"] = "step_limit"
            # Explicitly mark independent verification available for the built-in simulator only.
            from .simulator import SimulatedTarget
            if self.state["status"] == "agent_done" and isinstance(self.observer, SimulatedTarget):
                s = self.observer.state()
                self.state["verification"] = s
                self.state["status"] = "verified_demo" if s["submitted"] and s["value"] == "Hello ESP32-C3!" else "verification_failed"
        except Stopped:
            self.state["status"] = "stopped"
        except asyncio.CancelledError:
            self.state["status"] = "stopped"
            raise
        except Exception as exc:
            self.state.update(status="error", error=str(exc))
            self.audit("error", {"type": type(exc).__name__, "error": str(exc)})
        finally:
            await self.executor.release()
            try:
                await self.transport.close()
            finally:
                if hasattr(self.planner, "close"):
                    await self.planner.close()
                self.audit("finish", self.state.copy())
                (self.run_dir / "result.json").write_text(json.dumps(self.state, indent=2, ensure_ascii=False))
        return self.state
