"""Measure host overhead using synthetic images and a local planner; no hardware input."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import tempfile
import time
from pathlib import Path

from PIL import Image

from espcomputeruse.models import Plan
from espcomputeruse.observation import Observation
from espcomputeruse.runner import Runner
from espcomputeruse.simulator import SimulatedTarget


def summarize(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "p50_ms": round(statistics.median(samples), 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3)}


class Observer:
    def capture(self):
        return Observation(Image.new("RGB", (16, 16)), (0, 0), (16, 16), None)


class ImmediatePlanner:
    async def plan(self, goal, observation, history):
        return Plan(observation_id=observation.id, status="done", reason="Local benchmark", actions=[])


class NoInput:
    async def request(self, command):
        raise AssertionError("host benchmark must never send input")

    async def close(self):
        pass


async def benchmark(iterations):
    fixture = SimulatedTarget().capture().image.resize((1280, 720))
    serialization = []
    for index in range(iterations + 3):
        observation = Observation(fixture, (0, 0), fixture.size, None)
        started = time.perf_counter()
        # Save, audit metadata, planner payload, and serve the same screenshot.
        observation.jpeg()
        observation.metadata()
        observation.payload()
        observation.jpeg()
        elapsed = (time.perf_counter() - started) * 1000
        if index >= 3:
            serialization.append(elapsed)

    planning = []
    with tempfile.TemporaryDirectory(prefix="esp-host-benchmark-") as directory:
        for index in range(iterations + 3):
            runner = Runner(Observer(), ImmediatePlanner(), NoInput(), Path(directory) / str(index),
                            execute=False, settle_ms=0)
            started = time.perf_counter()
            result = await runner.run("Host benchmark")
            assert result["status"] == "agent_done", result
            elapsed = (time.perf_counter() - started) * 1000
            if index >= 3:
                planning.append(elapsed)
    return {"scope": "synthetic host overhead; no hardware or screen presentation latency",
            "python": platform.python_version(), "platform": platform.system(),
            "image_size": list(fixture.size),
            "observation_serialization": summarize(serialization),
            "immediate_planner_roundtrip": summarize(planning)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 500:
        parser.error("iterations must be 1..500")
    result = json.dumps(asyncio.run(benchmark(args.iterations)), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    print(result, end="")


if __name__ == "__main__":
    main()
