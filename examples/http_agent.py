"""HTTP contract example (simulator only), not a production vision model.

Run: .venv/bin/uvicorn examples.http_agent:app --host 127.0.0.1 --port 9001
Point HTTPPlanner('http', 'http://127.0.0.1:9001/plan') at it with a SimulatedTarget.
"""
from fastapi import FastAPI, HTTPException
from PIL import Image

from espcomputeruse.observation import Observation
from espcomputeruse.simulator import DemoPlanner

app = FastAPI()


@app.post("/plan")
async def plan(body: dict):
    data = body["observation"]
    if "focused" not in data.get("context", ""):
        raise HTTPException(400, "This example only supports the built-in simulator")
    obs = Observation(Image.new("RGB", (data["width"], data["height"])), tuple(data["origin"]),
                      tuple(data["logical_size"]), tuple(data["cursor"]), data["context"], id=data["id"])
    result = await DemoPlanner().plan(body["goal"], obs, body.get("history", []))
    return result.model_dump()
