# PanoramaFloorPlan Agentic Render Pipeline — Implementation Notes

## Completed Architecture

- Existing FastAPI backend remains the public API gateway.
- PNG generation is wrapped by `backend/png_tool.py`.
- Prompt text and agent readiness checks are centralized in `backend/prompts.py`.
- Agent system prompts, tool descriptions, image prompts, intent-classifier prompts, and prompt message builders are all defined in `backend/prompts.py`.
- Point-cloud generation is wrapped by `backend/point_cloud_tool.py`.
- Agent pipeline execution is implemented in `backend/agent_pipeline.py` with a LangChain/OpenAI tool-calling path and deterministic fallback.
- Dialog intent routing is implemented in `backend/orchestrator.py` and dispatches to generate, edit, discuss, floor-plan discussion, floor-plan plot, or clarification paths.
- Depth processing is isolated in `depth_service/`.
- Docker Compose runs `backend` and `depth-service` with shared artifact directories.

## Endpoint Checklist

- [x] `GET /health`
- [x] `POST /agent/suggest-prompt`
- [x] `POST /render` compatibility endpoint
- [x] `POST /uploads/viewport`
- [x] `POST /artifacts/download`
- [x] `POST /generate/png`
- [x] `POST /edit/image`
- [x] `POST /generate/point-cloud`
- [x] `POST /agent/orchestrate`
- [x] `POST /agent/run`
- [x] `GET /health` on depth service
- [x] `POST /depth/point-cloud` on depth service

## Artifact Flow

```text
SketchUp viewport -> exports/*.png
SketchUp plugin -> /uploads/viewport -> backend exports/*.png
/generate/png -> outputs/*.png
/edit/image -> outputs/*.png
/generate/point-cloud -> pointclouds/*.ply by default + pointclouds/*_depth_preview.png; OBJ output also writes .mtl and texture PNG sidecars
/agent/run -> outputs/*.png + pointclouds/*.ply by default
/agent/orchestrate floor_plan_plot -> outputs/*.svg + outputs/*.layout.json + outputs/*.png placeholder
SketchUp plugin -> /artifacts/download -> local outputs/ and pointclouds/
```

## Agent Runtime

`/agent/run` first attempts the LangChain/OpenAI path when `OPENAI_API_KEY` is configured and `langchain-openai` is installed. The agent is instructed to call:

- `generate_png`
- `generate_point_cloud`

If the LLM path cannot import, plan, call tools, or complete both artifacts, the backend falls back to the deterministic PNG -> point-cloud sequence and includes a warning in the response.

Before either agent path runs, `assess_agent_readiness()` rejects conversational-only or underspecified prompts so inputs such as `Hi` do not generate artifacts.

`/agent/orchestrate` is the SketchUp dialog entrypoint. It classifies user intent into `generate`, `edit`, `discuss`, `floor_plan_discuss`, `floor_plan_plot`, `room_render_generate`, or `other`, then dispatches to the matching sub-agent/tool path. Edit requests require a latest PNG path and call the image edit tool instead of `/agent/run`.

The dialog's visible `Chat` button and hidden keyboard shortcut (`Cmd+Enter` on macOS, `Ctrl+Enter` elsewhere) both call `runAgentPipeline()`. Render and edit requests still use the Ruby `orchestrate_agent` callback because they need a live SketchUp viewport export. Floor-plan discussion prompts, `Plot Floor Plan`, and `Generate Room Renders` bypass Ruby callbacks and call `${backendUrl}/agent/orchestrate` directly from HtmlDialog `fetch`, sending draft or decoration JSON state with placeholder viewport metadata. The direct path hydrates SVG, PNG, and room-render previews through `/artifacts/download`.

SketchUp-local actions remain guarded Ruby callbacks: importing render PNGs, revealing/importing point clouds, generating point clouds from a selected render, and opening local floor-plan files in the larger viewer. If a callback is unavailable, the dialog reports that immediately instead of leaving the user on a pending state.

Floor-plan follow-up classification is owned by the LLM intent classifier. The classifier user message includes the existing `temporary_floor_plan_draft` JSON, which lets prompts like `Office 7x9 adjacent with a door` update the current draft without frontend room-name rules.

Room-render classification is also backend-owned. The orchestrator routes `room_render_generate` to the room render tool when a decoration JSON path is available. If the caller provides a complete draft but no decoration path, the backend plots the floor plan first and then renders rooms; if neither exists, it returns a normal clarification response instead of a server error.

## SketchUp Import Tools

The import step is handled in the SketchUp plugin, not the backend, because it needs access to `Sketchup.active_model`.

- `import_render` imports generated PNG files as SketchUp images.
- `reveal_point_cloud` reveals the generated geometry file locally.
- `import_point_cloud` imports textured OBJ through SketchUp's generic importer after downloading its `.mtl` and texture sidecars. PLY/LAS/LAZ import never uses generic `model.import`; it only runs through a detected Scan Essentials Ruby import adapter.

After `/agent/orchestrate` or `/agent/run` returns artifacts, the chat dialog asks the user whether to import the PNG. For geometry artifacts, the dialog always offers Reveal. PLY/LAS import requires a compatible importer such as Scan Essentials; textured OBJ remains available as an optional mesh format.

If Scan Essentials does not expose a concrete Ruby API, `import_point_cloud` reports that direct import is unavailable and asks the user to reveal/import manually.

## Depth Anything V2 Integration Point

The depth service uses Depth Anything V2 metric model inference through `estimate_depth()` in `depth_service/service.py` while preserving:

- input image path validation
- output OBJ/PLY/LAS writing
- coordinate convention: `x` is image horizontal, `y` is max-depth-shifted estimated depth (`max_depth - depth`), and `z` is image vertical/up shifted so the minimum Z is 0
- preview generation
- response schema
- tests using mocked or fixture depth output so CI does not need GPU or model weights

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
- Call `/agent/orchestrate` with a latest PNG path and an edit prompt, then confirm it calls the edit path.
- Call `/agent/orchestrate` with a `temporary_floor_plan_draft` and `user_prompt: "plot the floor plan"`, then confirm it returns SVG, decoration JSON, and PNG placeholder artifacts.
- Call `/agent/orchestrate` with `user_prompt: "generate room renders"` and `latest_floor_plan_decoration_path`, then confirm it returns room PNG artifacts.
- Call `/agent/orchestrate` with `user_prompt: "generate room renders"` and only a complete draft, then confirm it plots first and returns room PNG artifacts.
- In SketchUp, render from the plugin dialog and verify previews still work.
- In SketchUp, render from the plugin dialog and verify backend logs show `/uploads/viewport` followed by `/agent/orchestrate`.
- In SketchUp, discuss a floor plan, add a follow-up room, click `Plot Floor Plan`, and verify backend logs show `/agent/orchestrate`, `FloorPlanDecorationTool`, and `FloorPlanPlotTool` with no preceding `/uploads/viewport` for that floor-plan request.
- In SketchUp, click `Generate Room Renders` after plotting and verify backend logs show `/agent/orchestrate` and `RoomRenderTool` with no preceding `/uploads/viewport`.
- Confirm the composer shows the `Chat` button and no visible shortcut hint.
- Reveal the generated geometry file from the plugin dialog.
- Reveal the generated PLY from the plugin dialog.
- If SketchUp Studio Scan Essentials is detected on a supported OS, optional PLY/LAS outputs can be imported manually through Scan Essentials.
