import json
import uuid

SERVICE_UUID = "c3a00001-7e45-4f9b-9d83-9bb45a2c1000"
COMMAND_UUID = "c3a00002-7e45-4f9b-9d83-9bb45a2c1000"
REPLY_UUID = "c3a00003-7e45-4f9b-9d83-9bb45a2c1000"
MAX_LINE = 511


class TransportError(RuntimeError):
    pass


class UncertainDelivery(TransportError):
    """Input may have executed. Stop; never automatically retry it."""


def frame(command: dict) -> tuple[dict, bytes]:
    request = {**command, "v": 1, "id": uuid.uuid4().hex}
    encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    if len(encoded) - 1 > MAX_LINE:
        raise ValueError("command exceeds firmware frame limit")
    return request, encoded


def check_reply(reply: dict, expected_id: str) -> dict:
    if type(reply.get("v")) is not int or reply.get("v") != 1 or reply.get("id") != expected_id or type(reply.get("ok")) is not bool:
        raise TransportError("invalid or mismatched acknowledgement")
    if not reply["ok"]:
        raise TransportError(f"device rejected command: {reply.get('error', 'unknown')}")
    return reply
