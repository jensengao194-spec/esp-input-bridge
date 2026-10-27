"""Exercise the packaged launcher against its separately installed runtime."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def verify(runtime):
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT/"plugins/esp-input-bridge/server/launch.py"), "--demo"],
        env={"ESP_INPUT_RUNTIME": str(runtime.resolve())})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            assert names == ["act", "observe", "stop"]
            observed = await session.call_tool("observe", {"image": True})
            assert not observed.isError
            assert [c.type for c in observed.content] == ["text", "image"]
            state = json.loads(observed.content[0].text)
            for batch in [
                [{"type": "move_to", "x": 180, "y": 245}, {"type": "click"}],
                [{"type": "type_text", "text": "Hello ESP32-C3!"}],
                [{"type": "move_to", "x": 130, "y": 340}, {"type": "click"}],
            ]:
                result = await session.call_tool("act", {"observation_id": state["observation_id"], "actions": batch})
                assert not result.isError, result
                assert [c.type for c in result.content] == ["text"]
                state = json.loads(result.content[0].text)
            target = json.loads(state["context"])
            assert target["submitted"] and target["value"] == "Hello ESP32-C3!"
            assert not (await session.call_tool("stop", {})).isError
            return {"status": "verified_packaged_plugin", "mode": state["mode"], "tools": names,
                    "target": target, "physical_hardware_tested": False}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(asyncio.run(verify(args.runtime)), indent=2))


if __name__ == "__main__":
    main()
