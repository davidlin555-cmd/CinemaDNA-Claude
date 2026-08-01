import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from render.router import route_shot
from render.local_acting_backend import LocalActingBackend
from render.backend import RenderRequest, BackendCapabilities, MediaSink
from asset_brain.common.bundle import Bundle
import json

def test_route_shot_acting_intercept():
    contract = {
        "shot_id": "SH001",
        "shot_type": "CLOSEUP",
        "scene_type": "acting",
        "duration_sec": 4.0,
        "difficulty": "normal"
    }
    decision = route_shot(contract)
    assert decision["model"] == "local-acting-v1"
    assert decision["tier"] == "local"
    assert "本地微表情" in decision["reason"]

def test_route_shot_normal():
    contract = {
        "shot_id": "SH002",
        "shot_type": "ESTABLISHING",
        "duration_sec": 4.0,
        "difficulty": "normal"
    }
    decision = route_shot(contract)
    assert decision["model"] == "local-video-v1"

@patch("urllib.request.urlopen")
def test_local_acting_backend_render(mock_urlopen):
    with TemporaryDirectory() as td:
        root = Path(td)
        bundle = Bundle(root, "test_bundle")
        bundle.ensure()

        # Write dummy reference image
        img_path = root / bundle.bundle_id / "02_cast" / "char1.png"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(b"dummy image bytes")

        request = RenderRequest(
            shot_id="SH001",
            task_id="task1",
            story_id="test_story",
            bundle_id="test_bundle",
            contract_hash="hash",
            model="local-acting-v1",
            tier="local",
            duration_sec=4.0,
            seed=123,
            prompt="test",
            reference_assets={
                "character_images": {"c1": "02_cast/char1.png"}
            }
        )

        # Mock successful response
        mock_response = MagicMock()
        mock_response.read.return_value = b"video data"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        backend = LocalActingBackend(bundle_root=root)
        sink = MediaSink(bundle)

        rendered = backend.render(request, sink=sink)

        assert rendered["file_relpath"] == "09_downloads/task1/SH001.mp4"
        assert rendered["backend"] == "local-acting-v1"
        assert rendered["is_generated_footage"] is True

        # Verify file is written
        written_path = sink.abs_path(rendered["file_relpath"])
        assert written_path.exists()
        assert written_path.read_bytes() == b"video data"
