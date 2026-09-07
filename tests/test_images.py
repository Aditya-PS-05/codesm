"""Untrusted image responses must not break a run or escape the local cache."""

import io

import httpx
from PIL import Image
import pytest

from codesm.agent import claude_science as science
from codesm.storage import images


@pytest.mark.parametrize("problem", ["redirect", "damaged", "oversized", "pixels"])
async def test_unusable_science_images_have_a_visible_fallback(monkeypatch, problem):
    stream = io.BytesIO()
    Image.new("RGB", (32, 18), "white").save(stream, format="PNG")
    data = stream.getvalue()
    requests = []
    def respond(request):
        requests.append(request)
        if problem == "redirect":
            return httpx.Response(302, headers={"location": "https://untrusted.test/collect"})
        return httpx.Response(200, content=b"not an image" if problem == "damaged" else data)
    if problem == "oversized":
        monkeypatch.setattr(science, "MAX_IMAGE_BYTES", 8)
    if problem == "pixels":
        monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 10)
    record = {"_uuid": "message-one", "_artifact_refs": {
        "plot.png": {"artifact_id": "plot", "version_id": "version-one"}}}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(respond)) as client:
        seen, downloaded, displayed = set(), {}, set()
        chunks = [c async for c in science.stream_images(client, record, "frame-one", "session", seen, downloaded, displayed)]
        assert len(chunks) == 1 and chunks[0].type == "image" and chunks[0].metadata["error"]
        assert images.image_path(chunks[0].metadata) is None
        assert not [c async for c in science.stream_images(client, record, "frame-one", "session", seen, downloaded, displayed)]
    assert len(requests) == 1 and requests[0].url.host == "127.0.0.1"
    assert not images.image_directory("session").exists()


def test_image_sources_and_cache_references_cannot_escape(tmp_path):
    record = {"_cell_images": {"tool": [
        {"filename": "figure.png", "content_type": {"bad": "type"}},
        {"filename": "figure.png", "path": "/workspace/figure with spaces.png"}]},
        "_artifact_refs": {"escape.png": {"version_id": "../../auth/nonce"},
                           "text.md": {"version_id": "not-an-image"}}}
    sources = list(science.image_sources(record, "frame-one"))
    assert len(sources) == 1
    url = httpx.URL(sources[0][0])
    assert url.path == "/api/compute/local/download"
    assert url.params["confine"] == "workspaces"
    assert url.params["path"] == "/workspace/figure with spaces.png"
    assert images.image_path({"path": "../../private.png"}) is None
    root = images.image_directory("session")
    root.parent.mkdir(parents=True)
    root.symlink_to(tmp_path, target_is_directory=True)
    assert images.image_path({"path": f"{root.name}/{'a' * 64}.png"}) is None
