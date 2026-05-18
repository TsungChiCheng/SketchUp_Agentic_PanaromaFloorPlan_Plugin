import base64

import httpx
from fastapi.testclient import TestClient

from main import app
from schemas import AgentRunResponse, PngGenerationResponse, PointCloudGenerationResponse
from test_schemas import valid_render_payload


def test_render_uses_mock_provider_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post("/render", json=valid_render_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["render_id"].startswith("render_")
    assert body["output_image_path"].endswith(".png")
    assert tmp_path.joinpath(body["output_image_path"].split("/")[-1]).exists()


def test_render_gemini_without_api_key_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = TestClient(app)

    response = client.post("/render", json=valid_render_payload())

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_render_openai_without_api_key_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)

    response = client.post("/render", json=valid_render_payload())

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_render_openai_writes_returned_image(monkeypatch, tmp_path) -> None:
    export_dir = tmp_path / "exports"
    output_dir = tmp_path / "outputs"
    export_dir.mkdir()
    export_dir.joinpath("current_view.png").write_bytes(b"input image")

    monkeypatch.setenv("RENDER_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
    monkeypatch.setenv("EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/images/edits"
        assert kwargs["data"]["model"] == "gpt-image-1.5"
        assert kwargs["data"]["size"] == "1024x1024"
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(b"output image").decode("ascii")}]},
        )

    monkeypatch.setattr("renderers.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post("/render", json=valid_render_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert output_dir.joinpath(body["output_image_path"].split("/")[-1]).read_bytes() == b"output image"


def test_generate_png_endpoint_uses_render_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post("/generate/png", json=valid_render_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["artifact_id"].startswith("render_")
    assert body["provider"] == "mock"


def test_upload_viewport_writes_export_file(monkeypatch, tmp_path) -> None:
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("EXPORT_DIR", str(export_dir))
    client = TestClient(app)

    response = client.post(
        "/uploads/viewport",
        json={
            "filename": "viewport_test.png",
            "content_base64": base64.b64encode(b"viewport image").decode("ascii"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "success",
        "image_path": "viewport_test.png",
        "filename": "viewport_test.png",
        "size_bytes": len(b"viewport image"),
    }
    assert export_dir.joinpath("viewport_test.png").read_bytes() == b"viewport image"


def test_upload_viewport_rejects_invalid_base64(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    client = TestClient(app)

    response = client.post(
        "/uploads/viewport",
        json={"filename": "viewport_test.png", "content_base64": "not base64"},
    )

    assert response.status_code == 422
    assert "base64" in response.json()["detail"]


def test_download_artifact_returns_output_file(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output_dir.joinpath("render.png").write_bytes(b"render bytes")
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    client = TestClient(app)

    response = client.post("/artifacts/download", json={"path": "/app/outputs/render.png"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["filename"] == "render.png"
    assert body["content_base64"] == base64.b64encode(b"render bytes").decode("ascii")
    assert body["size_bytes"] == len(b"render bytes")


def test_edit_image_endpoint_uses_mock_provider(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output_dir.joinpath("source.png").write_bytes(b"input image")
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    client = TestClient(app)

    response = client.post(
        "/edit/image",
        json={"image_path": "source.png", "prompt": "make the room brighter"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["enhanced_prompt"] == "make the room brighter"
    assert output_dir.joinpath(body["output_image_path"].split("/")[-1]).exists()


def test_edit_image_openai_writes_returned_image(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output_dir.joinpath("source.png").write_bytes(b"input image")
    monkeypatch.setenv("RENDER_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/images/edits"
        assert kwargs["data"]["prompt"].startswith("make the room brighter")
        assert "Use the supplied image as the visual reference" in kwargs["data"]["prompt"]
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(b"edited image").decode("ascii")}]},
        )

    monkeypatch.setattr("renderers.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post(
        "/edit/image",
        json={"image_path": "source.png", "prompt": "make the room brighter"},
    )

    assert response.status_code == 200
    body = response.json()
    assert output_dir.joinpath(body["output_image_path"].split("/")[-1]).read_bytes() == b"edited image"


def test_generate_point_cloud_endpoint_calls_depth_service(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output_dir.joinpath("render.png").write_bytes(b"render image")
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    def fake_post(*args, **kwargs):
        assert args[0] == "http://depth-service:8001/depth/point-cloud"
        assert kwargs["json"]["image_path"] == str(output_dir / "render.png")
        return httpx.Response(
            200,
            json={
                "status": "success",
                "artifact_id": "pointcloud_test",
                "pointcloud_path": "/app/pointclouds/pointcloud_test.ply",
                "preview_image_path": "/app/pointclouds/pointcloud_test_depth_preview.png",
                "output_format": "ply",
                "sidecar_paths": [],
                "point_count": 10,
                "depth_model": "depth-anything-v2-metric-indoor-small",
                "warnings": [],
                "error_message": None,
            },
        )

    monkeypatch.setattr("point_cloud_tool.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post("/generate/point-cloud", json={"image_path": "/app/outputs/render.png"})

    assert response.status_code == 200
    assert response.json()["pointcloud_path"].endswith(".ply")


def test_generate_point_cloud_endpoint_resolves_uploaded_viewport_filename(monkeypatch, tmp_path) -> None:
    export_dir = tmp_path / "exports"
    output_dir = tmp_path / "outputs"
    export_dir.mkdir()
    output_dir.mkdir()
    export_dir.joinpath("viewport_test.png").write_bytes(b"viewport image")
    monkeypatch.setenv("EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    def fake_post(*args, **kwargs):
        assert args[0] == "http://depth-service:8001/depth/point-cloud"
        assert kwargs["json"]["image_path"] == str(export_dir / "viewport_test.png")
        return httpx.Response(
            200,
            json={
                "status": "success",
                "artifact_id": "pointcloud_test",
                "pointcloud_path": "/app/pointclouds/pointcloud_test.ply",
                "preview_image_path": "/app/pointclouds/pointcloud_test_depth_preview.png",
                "output_format": "ply",
                "point_count": 10,
                "depth_model": "depth-anything-v2-metric-indoor-small",
                "warnings": [],
                "error_message": None,
            },
        )

    monkeypatch.setattr("point_cloud_tool.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post("/generate/point-cloud", json={"image_path": "viewport_test.png"})

    assert response.status_code == 200
    assert response.json()["pointcloud_path"].endswith(".ply")


def test_agent_run_orchestrates_png_then_point_cloud(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))

    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "status": "success",
                "artifact_id": "pointcloud_test",
                "pointcloud_path": "/app/pointclouds/pointcloud_test.ply",
                "preview_image_path": "/app/pointclouds/pointcloud_test_depth_preview.png",
                "output_format": "ply",
                "point_count": 10,
                "depth_model": "depth-anything-v2-metric-indoor-small",
                "warnings": [],
                "error_message": None,
            },
        )

    monkeypatch.setattr("point_cloud_tool.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post("/agent/run", json=valid_render_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert [artifact["type"] for artifact in body["artifacts"]] == ["png", "point_cloud"]
    assert "tool:generate_png" in body["trace"]
    assert "tool:generate_point_cloud" in body["trace"]
    assert any("deterministic tool orchestration" in warning for warning in body["warnings"])
    assert body["started_at"]
    assert body["completed_at"]
    assert [call["name"] for call in body["api_calls"]] == [
        "POST /agent/run",
        "tool:generate_png",
        "tool:generate_point_cloud",
    ]
    assert body["api_calls"][0]["prompt"] == valid_render_payload()["user_prompt"]


def test_orchestrator_routes_add_to_edit_when_latest_png_exists(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output_dir.joinpath("latest.png").write_bytes(b"latest image")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))

    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "status": "success",
                "artifact_id": "pointcloud_test",
                "pointcloud_path": "/app/pointclouds/pointcloud_test.ply",
                "preview_image_path": "/app/pointclouds/pointcloud_test_depth_preview.png",
                "output_format": "ply",
                "point_count": 10,
                "depth_model": "depth-anything-v2-metric-indoor-small",
                "warnings": [],
                "error_message": None,
            },
        )

    payload = {
        **valid_render_payload(),
        "user_prompt": "add a sofa to this room",
        "latest_png_path": "latest.png",
    }
    monkeypatch.setattr("point_cloud_tool.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "edit"
    assert [call["name"] for call in body["api_calls"]] == [
        "POST /agent/orchestrate",
        "tool:edit_image",
        "tool:generate_point_cloud",
    ]
    assert "tool:generate_png" not in body["trace"]


def test_agent_run_asks_for_more_information_for_greeting() -> None:
    payload = valid_render_payload()
    payload["user_prompt"] = "Hi"
    client = TestClient(app)

    response = client.post("/agent/run", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["png"] is None
    assert body["point_cloud"] is None
    assert "agent:needs_more_information" in body["trace"]
    assert "greeting" in body["error_message"]
    assert body["api_calls"][0]["name"] == "POST /agent/run"
    assert body["api_calls"][0]["prompt"] == "Hi"


def test_agent_run_uses_langchain_agent_when_openai_key_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    def fake_run_langchain_agent(request, settings, **kwargs):
        return AgentRunResponse(
            status="success",
            agent_model=settings.agent_model,
            png=PngGenerationResponse(
                status="success",
                artifact_id="render_agent",
                output_image_path="/app/outputs/render_agent.png",
                preview_image_path="/app/outputs/render_agent.png",
                enhanced_prompt="Enhanced",
                negative_prompt="Negative",
                provider="mock",
            ),
            point_cloud=PointCloudGenerationResponse(
                status="success",
                artifact_id="pointcloud_agent",
                pointcloud_path="/app/pointclouds/pointcloud_agent.ply",
                preview_image_path="/app/pointclouds/pointcloud_agent_depth_preview.png",
                output_format="ply",
                point_count=10,
                depth_model="depth-anything-v2-metric-indoor-small",
            ),
            artifacts=[
                {"type": "png", "path": "/app/outputs/render_agent.png", "artifact_id": "render_agent"},
                {
                    "type": "point_cloud",
                    "path": "/app/pointclouds/pointcloud_agent.ply",
                    "preview_image_path": "/app/pointclouds/pointcloud_agent_depth_preview.png",
                    "artifact_id": "pointcloud_agent",
                },
            ],
            trace=["agent:start", "agent:llm:start", "agent:llm:tool_call:generate_png", "agent:llm:complete"],
            api_calls=kwargs["api_calls"],
            started_at=kwargs["started_at"],
            completed_at=kwargs["started_at"],
        )

    monkeypatch.setattr("agent_pipeline._langchain_tools_available", lambda: True)
    monkeypatch.setattr("agent_pipeline._run_langchain_agent", fake_run_langchain_agent)
    client = TestClient(app)

    response = client.post("/agent/run", json=valid_render_payload())

    assert response.status_code == 200
    body = response.json()
    assert "agent:llm:tool_call:generate_png" in body["trace"]


def test_agent_run_falls_back_when_langchain_agent_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))

    def fake_run_langchain_agent(request, settings, **kwargs):
        raise RuntimeError("tool planning failed")

    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "status": "success",
                "artifact_id": "pointcloud_test",
                "pointcloud_path": "/app/pointclouds/pointcloud_test.ply",
                "preview_image_path": "/app/pointclouds/pointcloud_test_depth_preview.png",
                "output_format": "ply",
                "point_count": 10,
                "depth_model": "depth-anything-v2-metric-indoor-small",
                "warnings": [],
                "error_message": None,
            },
        )

    monkeypatch.setattr("agent_pipeline._langchain_tools_available", lambda: True)
    monkeypatch.setattr("agent_pipeline._run_langchain_agent", fake_run_langchain_agent)
    monkeypatch.setattr("point_cloud_tool.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post("/agent/run", json=valid_render_payload())

    assert response.status_code == 200
    body = response.json()
    assert "tool:generate_png" in body["trace"]
    assert "tool:generate_point_cloud" in body["trace"]
    assert any("LangChain agent failed" in warning for warning in body["warnings"])


def test_render_missing_viewport_path_is_validation_error() -> None:
    payload = valid_render_payload()
    payload.pop("viewport_image_path")
    client = TestClient(app)

    response = client.post("/render", json=payload)

    assert response.status_code == 422


def test_unknown_render_provider_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_PROVIDER", "banana-cloud")
    client = TestClient(app)

    response = client.post("/render", json=valid_render_payload())

    assert response.status_code == 503
    assert "Unsupported RENDER_PROVIDER" in response.json()["detail"]


def test_gemini_provider_rejects_paths_outside_export_dir(monkeypatch, tmp_path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not really an image")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    payload = valid_render_payload()
    payload["viewport_image_path"] = str(outside)
    monkeypatch.setenv("RENDER_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("EXPORT_DIR", str(export_dir))
    client = TestClient(app)

    response = client.post("/render", json=payload)

    assert response.status_code == 503
    assert "under EXPORT_DIR" in response.json()["detail"]


def test_suggest_prompt_endpoint() -> None:
    client = TestClient(app)
    render_payload = valid_render_payload()
    payload = {
        "style": render_payload["style"],
        "user_prompt": render_payload["user_prompt"],
        "camera": render_payload["camera"],
        "model": render_payload["model"],
    }

    response = client.post("/agent/suggest-prompt", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["enhanced_prompt"]
    assert body["negative_prompt"]
    assert isinstance(body["recommendations"], list)
    assert isinstance(body["validation_warnings"], list)
