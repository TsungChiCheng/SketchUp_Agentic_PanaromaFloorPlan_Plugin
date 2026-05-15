# Notebook Floor-Plan Test Prompts

Source of truth: `test.ipynb`.

| Index | Notebook Step | Endpoint | Prompt / Payload Driver |
|---:|---|---|---|
| 1 | Discussion draft capture | `POST /agent/orchestrate` | `Floor plan with Living 12x14 and Kitchen 8x10 adjacent with a door` |
| 2 | Continued discussion with existing draft | `POST /agent/orchestrate` | `Office 8x4 on the right corner and only one door to the living room`|
| 3 | Plot trigger | `POST /agent/orchestrate` | `plot the floor plan` plus `temporary_floor_plan_draft`|
| 4 | Artifact download | `POST /artifacts/download` | `svg_path`, `preview_image_path`, and `decoration_path` from step 3 |
| 5 | Room render generation | `POST /agent/orchestrate` | `generate room renders` plus `latest_floor_plan_decoration_path` from step 3 and `selected_room_names: []` |
| 6 | Room render downloads | `POST /artifacts/download` | Every `output_image_path` returned by step 5 |

Required routes checked by the notebook:

- `/agent/orchestrate`
- `/artifacts/download`

Expected generated local artifacts:

- `outputs/notebook-floor-plan/*.svg`
- `outputs/notebook-floor-plan/*.png`
- `outputs/notebook-floor-plan/*.layout.json`
- `outputs/notebook-floor-plan/room-renders/*.png`
