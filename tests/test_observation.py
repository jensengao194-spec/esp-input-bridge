import base64
import hashlib
import io

import pytest
from PIL import Image

from espcomputeruse.observation import Observation


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L"])
def test_observation_payload_hash_and_saved_image_agree(mode):
    observation = Observation(Image.new(mode, (64, 32)), (0, 0), (64, 32), None)
    saved = observation.jpeg()
    payload = observation.payload()
    assert base64.b64decode(payload["image_base64"]) == saved
    assert payload["sha256"] == hashlib.sha256(saved).hexdigest()
    with Image.open(io.BytesIO(saved)) as image:
        assert image.size == (64, 32) and image.mode == "RGB"


def test_observation_does_not_reencode_for_audit_payload_and_serving(monkeypatch):
    original_save = Image.Image.save
    encodes = []

    def save(image, *args, **kwargs):
        encodes.append(image.size)
        return original_save(image, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    first = Observation(Image.new("RGB", (64, 32), "red"), (0, 0), (64, 32), None)
    saved = first.jpeg()
    first.metadata()
    first.payload()
    assert first.jpeg() == saved
    second = Observation(Image.new("RGB", (64, 32), "blue"), (0, 0), (64, 32), None)
    assert second.metadata()["sha256"] != first.metadata()["sha256"]
    assert encodes == [(64, 32), (64, 32)]
