import sys

import httpx
import pytest

from espcomputeruse.agents import HTTPPlanner
from espcomputeruse.observation import DesktopObserver
from espcomputeruse.runner import Runner
from espcomputeruse.simulator import SimulatedTarget


async def test_external_agent_full_loop(tmp_path):
    from examples.http_agent import app
    target = SimulatedTarget()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    planner = HTTPPlanner("http", "http://local-agent/plan", client=client)
    runner = Runner(target, planner, target, tmp_path/"external-http", execute=True, settle_ms=0)
    result = await runner.run("fill and submit")
    assert result["status"] == "verified_demo"
    assert target.submitted and target.value == "Hello ESP32-C3!"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS permission preflight")
def test_capture_permission_failure_is_explicit(monkeypatch):
    import Quartz
    monkeypatch.setattr(Quartz, "CGPreflightScreenCaptureAccess", lambda: False)
    with pytest.raises(PermissionError, match="Screen Recording permission"):
        DesktopObserver().capture()
