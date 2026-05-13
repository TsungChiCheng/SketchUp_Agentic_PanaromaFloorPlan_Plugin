# Architech Agentic Render Pipeline

SketchUp extension and local FastAPI services for AI-assisted architectural rendering and image-to-point-cloud generation.

The system now exposes three primary workflows:

- `POST /generate/png` generates a PNG render from a SketchUp viewport image and scene metadata.
- `POST /edit/image` edits an existing PNG/JPEG from `exports/` or `outputs/`.
- `POST /generate/point-cloud` converts a flat PNG into a colored PLY point cloud by default, with LAS/OBJ exports available through the depth service.
- `POST /agent/run` runs a LangChain/OpenAI tool-calling agent for PNG generation, then Depth Anything V2-compatible point-cloud generation, with deterministic fallback orchestration when the agent is unavailable.

## Architecture

```text
SketchUp Plugin
  -> FastAPI backend
     -> PNG generation tool
     -> image editing tool
     -> point-cloud generation tool
     -> LangChain/OpenAI agent pipeline
  -> Depth service
     -> Depth Anything V2-compatible depth stage
     -> RGB-D to colored PLY/LAS point cloud or OBJ mesh
```

The current implementation uses internal backend tools, not a standalone MCP server. Those tools are exposed by HTTP endpoints and are ready to be wrapped by MCP later if needed.

## Setup

Create or update `.env`:

```env
RENDER_PROVIDER=openai
OPENAI_API_KEY=your-openai-api-key
OPENAI_IMAGE_MODEL=gpt-image-1.5
AGENT_MODEL=gpt-4o-mini
DEPTH_MODEL=depth-anything-v2-metric-indoor-small
```

Start both services:

```bash
docker compose up --build
```

Health checks:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8001/health
```

## Endpoints

### Generate PNG

```bash
curl -X POST http://127.0.0.1:8000/generate/png \
  -H 'Content-Type: application/json' \
  --data @examples/render_request.json
```

The response includes the generated PNG path, enhanced prompt, provider, model, recommendations, and warnings.

### Edit Image

```bash
curl -X POST http://127.0.0.1:8000/edit/image \
  -H 'Content-Type: application/json' \
  -d '{"image_path":"/app/outputs/render.png","prompt":"make the room brighter"}'
```

The response includes a new PNG under `outputs/`. Image editing accepts source images under shared `exports/` or `outputs/`.

### Generate Point Cloud

```bash
curl -X POST http://127.0.0.1:8000/generate/point-cloud \
  -H 'Content-Type: application/json' \
  -d '{"image_path":"/app/outputs/render.png","output_format":"ply"}'
```

The response includes a colored `.ply` file by default and a depth preview PNG under `pointclouds/`. Use `"output_format":"las"` or `"output_format":"obj"` only when you specifically need those formats.

### Run Agent

```bash
curl -X POST http://127.0.0.1:8000/agent/run \
  -H 'Content-Type: application/json' \
  --data @examples/render_request.json
```

The agent endpoint returns both PNG and point-cloud artifacts plus a trace of the tool sequence. When `OPENAI_API_KEY` and the LangChain packages are available, `/agent/run` lets the configured `AGENT_MODEL` call the PNG and point-cloud tools. If the agent cannot run, the endpoint falls back to the deterministic PNG -> point-cloud sequence and includes a warning.

The agent rejects underspecified conversational input such as `Hi` before generating artifacts and returns a failed response asking for a rendering or editing instruction.

Prompt definitions live in `backend/prompts.py`.

## Render Providers

Available PNG providers:

- `mock` requires no credentials and writes deterministic placeholder output.
- `openai` calls OpenAI image edits using `OPENAI_IMAGE_MODEL`.
- `gemini` remains available as a legacy provider if `GEMINI_API_KEY` is supplied manually.

## Depth Service

The `depth-service` container owns point-cloud generation. It accepts PNG/JPEG images from shared `exports/` or `outputs/`, estimates depth, projects RGB-D pixels into 3D points, and writes colored PLY output by default. LAS and OBJ remain available through `output_format`.

The current code has a deterministic fallback depth estimator so the pipeline and tests run without model weights. The service boundary and `DEPTH_MODEL` setting are prepared for replacing that fallback with Depth Anything V2 model inference.

## SketchUp Plugin

Install the plugin by copying:

```text
sketchup_plugin/architech_ai_renderer.rb
sketchup_plugin/architech_ai_renderer/
```

into SketchUp's Plugins folder, then restart SketchUp and use:

```text
Extensions -> AI Render Assistant
```

The plugin can export the viewport, run the agent pipeline, preview PNG/depth artifacts, and offer import actions for generated files. PNG import is handled directly through SketchUp Ruby. Generated point-cloud files can always be revealed in Finder. New plugin-generated point clouds use PLY by default.

## Artifacts

- `exports/` stores SketchUp viewport PNGs.
- `outputs/` stores generated render PNGs.
- `pointclouds/` stores generated OBJ/PLY/LAS files and depth previews.

For SketchUp point-cloud import, use SketchUp Studio Scan Essentials on a supported operating system. On macOS, Reveal PLY works, but direct PLY/LAS point-cloud import is not available without Scan Essentials.

## Tests

Backend tests:

```bash
docker compose run --rm backend pytest tests -q
```

Depth-service tests:

```bash
docker compose run --rm depth-service pytest tests -q
```

## Documentation

- Product and architecture spec: `docs/spec.md`
- Implementation checklist: `docs/implementation.md`

## Current Limits

- Depth Anything V2 inference is represented by a deterministic fallback until model weights and runtime dependencies are installed.
- `.rcp` and `.rwp` export are out of scope; use `.ply` as the default open point-cloud format, with `.las` and `.obj` available when needed.
- No auth, payment, cloud queue, or production job scheduler.
