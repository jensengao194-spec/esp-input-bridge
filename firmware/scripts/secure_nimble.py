"""Reject silent replacement of an existing bond in pinned NimBLE-Arduino 2.3.9.

Upstream's default handler deletes the old key and retries. Re-pairing this bridge
requires explicit USB `forget-bonds` instead. Middleware runs before compilation,
including on clean PlatformIO installs, and refuses an unexpected upstream body.
"""
from pathlib import Path

Import("env")


def patch(env, node):
    path = Path(node.srcnode().get_abspath())
    text = path.read_text()
    start = text.index("        case BLE_GAP_EVENT_REPEAT_PAIRING: {")
    end = text.index("        case BLE_GAP_EVENT_ENC_CHANGE:", start)
    replacement = """        case BLE_GAP_EVENT_REPEAT_PAIRING: {
            // ESP Input Bridge: preserve existing keys; USB reset is required.
            return BLE_GAP_REPEAT_PAIRING_IGNORE;
        }

"""
    old = text[start:end]
    if old != replacement:
        if "ble_store_util_delete_peer" not in old or "BLE_GAP_REPEAT_PAIRING_RETRY" not in old:
            raise RuntimeError("Unexpected NimBLE repeat-pairing implementation; audit before building")
        path.write_text(text[:start]+replacement+text[end:])
        print("ESP Input Bridge: disabled automatic BLE bond replacement")
    return node


env.AddBuildMiddleware(patch, "*/NimBLEServer.cpp")
