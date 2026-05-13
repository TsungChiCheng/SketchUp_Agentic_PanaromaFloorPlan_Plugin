# Architech Agentic Render Pipeline — Implementation Notes

## Completed Architecture

- Existing FastAPI backend remains the public API gateway.
- PNG generation is wrapped by `backend/png_tool.py`.
- Prompt text and agent readiness checks are centralized in `backend/prompts.py`.
- Point-cloud generation is wrapped by `backend/point_cloud_tool.py`.
- Agent orchestration is implemented in `backend/agent_pipeline.py` with a LangChain/OpenAI tool-calling path and deterministic fallback.
- Depth processing is isolated in `depth_service/`.
- Docker Compose runs `backend` and `depth-service` with shared artifact directories.

## Endpoint Checklist

- [x] `GET /health`
- [x] `POST /agent/suggest-prompt`
- [x] `POST /render` compatibility endpoint
- [x] `POST /generate/png`
- [x] `POST /edit/image`
- [x] `POST /generate/point-cloud`
- [x] `POST /agent/run`
- [x] `GET /health` on depth service
- [x] `POST /depth/point-cloud` on depth service

## Artifact Flow

```text
SketchUp viewport -> exports/*.png
/generate/png -> outputs/*.png
/edit/image -> outputs/*.png
/generate/point-cloud -> pointclouds/*.ply by default + pointclouds/*_depth_preview.png
/agent/run -> outputs/*.png + pointclouds/*.ply by default
```

## Agent Runtime

`/agent/run` first attempts the LangChain/OpenAI path when `OPENAI_API_KEY` is configured and `langchain-openai` is installed. The agent is instructed to call:

- `generate_png`
- `generate_point_cloud`

If the LLM path cannot import, plan, call tools, or complete both artifacts, the backend falls back to the deterministic PNG -> point-cloud sequence and includes a warning in the response.

Before either agent path runs, `assess_agent_readiness()` rejects conversational-only or underspecified prompts so inputs such as `Hi` do not generate artifacts.

## SketchUp Import Tools

The import step is handled in the SketchUp plugin, not the backend, because it needs access to `Sketchup.active_model`.

- `import_render` imports generated PNG files as SketchUp images.
- `reveal_point_cloud` opens Finder at the generated geometry file.
- `import_point_cloud` imports OBJ through SketchUp's generic importer. PLY/LAS import still requires compatible point-cloud support.

After `/agent/run` returns artifacts, the chat dialog asks the user whether to import the PNG. For geometry artifacts, the dialog always offers Reveal. PLY/LAS import requires a compatible importer such as Scan Essentials; OBJ remains available as an optional mesh format.

If Scan Essentials exposes a concrete Ruby API, `import_point_cloud` should call that API before falling back to SketchUp's generic importer.

## Depth Anything V2 Integration Point

The depth service currently uses deterministic fallback depth. Replace `estimate_depth()` in `depth_service/service.py` with Depth Anything V2 metric model inference while preserving:

- input image path validation
- output OBJ/PLY/LAS writing
- preview generation
- response schema
- tests using mocked or fixture depth output

## Test Commands

```bash
docker compose run --rm backend pytest tests -q
docker compose run --rm depth-service pytest tests -q
```

## Manual Verification

- Start `docker compose up --build`.
- Confirm both health endpoints return `{"status":"ok"}`.
- Call `/generate/png` using `examples/render_request.json`.
- Call `/generate/point-cloud` with the generated PNG path.
- Call `/agent/run` and confirm both PNG and point-cloud artifacts are returned.
- In SketchUp, render from the plugin dialog and verify previews still work.
- Reveal the generated geometry file from the plugin dialog.
- Reveal the generated PLY from the plugin dialog.
- If SketchUp Studio Scan Essentials is detected on a supported OS, optional PLY/LAS outputs can be imported manually through Scan Essentials.
