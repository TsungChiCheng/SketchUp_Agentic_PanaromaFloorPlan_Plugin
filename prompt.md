# Notebook Floor-Plan Test Prompts

Source of truth: `test.ipynb`.

| Index | Notebook Step | Endpoint | Prompt / Payload Driver |
|---:|---|---|---|
| 1 | Backend health and routes | `GET /health`, `GET /openapi.json` | Verify `/agent/orchestrate`, `/generate/panorama`, and `/artifacts/download` exist |
| 2 | Shared render payload helper | local notebook helper | `valid_render_payload(...)` uses `style: modern interior` and render `output_resolution: 1024x1024` |
| 3 | Discussion draft capture | `POST /agent/orchestrate` | `Floor plan for 1. Living with front door, width 10, height 20, 2. Kitchen with width 10, height 10, 3. Office with width 10, height 10. 4. Living has door to Office, but no door to Kitchen. 5. A wall between Kitchen and Office.6. Connection (Living, Kitchen), (Living, Office). 7. Must be rectangular shape. ` |
| 4 | Continued discussion with existing draft | skipped local assertion | Continued `/agent/orchestrate` call is commented out; notebook asserts parsed room names are `Living`, `Kitchen`, `Office` |
| 5 | Plot trigger | `POST /agent/orchestrate` | `plot the floor plan` plus `temporary_floor_plan_draft` |
| 6 | Artifact download | `POST /artifacts/download` | `svg_path`, `preview_image_path`, and `decoration_path` from step 5 |
| 7 | Display floor plan before panorama | local notebook display | Display downloaded SVG, falling back to PNG preview on SVG parse errors |
| 8 | Panorama generation | `POST /generate/panorama` | `decoration_path` from step 5, `style: modern interior`, `output_resolution: 1024x576`, and `PANORAMA_TIMEOUT_SECONDS` |
| 9 | Panorama downloads | `POST /artifacts/download` | Every path in `panorama_image_paths` returned by step 8 |
| 10 | Visual preview | local notebook display | Display floor plan and each downloaded panorama option |

Required routes checked by the notebook:

- `/agent/orchestrate`
- `/generate/panorama`
- `/artifacts/download`

Expected generated local artifacts:

- `outputs/notebook-floor-plan/*.svg`
- `outputs/notebook-floor-plan/*.png`
- `outputs/notebook-floor-plan/*.layout.json`
- `outputs/notebook-floor-plan/panorama/*.png`

Notebook runtime settings:

- `BACKEND_URL` defaults to `http://127.0.0.1:8000`.
- `NOTEBOOK_REQUEST_TIMEOUT_SECONDS` defaults to `240`.
- `NOTEBOOK_PANORAMA_TIMEOUT_SECONDS` defaults to `720`.
- `OPENAI_API_KEY` is required because floor-plan plotting uses the LLM-backed floor-plan tools.

Panorama assertions:

- The endpoint is called directly through `/generate/panorama`, not `/agent/orchestrate`.
- `scene_description` mentions a person at the floor-plan center coordinate.
- `scene_description` mentions the left hemisphere direction and the Living room.
- The generated prompts ask for two direct 16:9 panorama options using image generation only.
- The generated image prompts include a compact `FLOOR_PLAN_GEOMETRY_JSON` block.
- The generated image prompts state that JSON geometry is the source of truth and rooms must not be reordered.
- OpenAI panorama provider requests use `size: auto`, then the backend normalizes panels to the requested 16:9 `output_resolution`.
- All two panorama artifacts are `1024x576` when the per-panel request is `1024x576`.
