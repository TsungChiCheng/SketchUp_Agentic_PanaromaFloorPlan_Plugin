from datetime import datetime, timezone
import base64
import mimetypes
from pathlib import Path
from uuid import uuid4

import httpx

from prompts import compose_image_edit_prompt, compose_image_generation_prompt
from schemas import ImageEditRequest, PromptSuggestionResponse, RenderRequest, RenderResponse
from settings import Settings
from mock_renderer import write_mock_output


class RenderConfigurationError(RuntimeError):
    pass


class RenderServiceError(RuntimeError):
    pass


def create_render_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"render_{timestamp}_{uuid4().hex[:8]}"


def render_image(
    request: RenderRequest,
    prompt: PromptSuggestionResponse,
    settings: Settings,
) -> RenderResponse:
    provider = settings.render_provider
    if provider == "mock":
        return _render_mock(prompt, settings)
    if provider == "gemini":
        return _render_gemini(request, prompt, settings)
    if provider == "openai":
        return _render_openai(request, prompt, settings)
    raise RenderConfigurationError(
        f"Unsupported RENDER_PROVIDER '{provider}'. Use 'mock', 'gemini', or 'openai'."
    )


def edit_image(request: ImageEditRequest, settings: Settings) -> RenderResponse:
    provider = settings.render_provider
    source_path = _resolve_reference_image_path(request.image_path, settings)
    if provider == "mock":
        prompt = PromptSuggestionResponse(
            enhanced_prompt=request.prompt,
            negative_prompt=request.negative_prompt or "",
            validation_warnings=["Mock provider returned a placeholder edit image."],
        )
        return _render_mock(prompt, settings)
    if provider != "openai":
        raise RenderConfigurationError("Image editing currently requires RENDER_PROVIDER=openai or mock.")
    if not settings.openai_api_key:
        raise RenderConfigurationError(
            "RENDER_PROVIDER=openai requires OPENAI_API_KEY. Set OPENAI_API_KEY or use RENDER_PROVIDER=mock."
        )

    full_prompt = compose_image_edit_prompt(request.prompt, request.negative_prompt)
    return _render_openai_edit(
        source_path=source_path,
        full_prompt=full_prompt,
        output_resolution=request.output_resolution,
        prompt=PromptSuggestionResponse(
            enhanced_prompt=request.prompt,
            negative_prompt=request.negative_prompt or "",
        ),
        settings=settings,
    )


def _render_mock(prompt: PromptSuggestionResponse, settings: Settings) -> RenderResponse:
    render_id = create_render_id()
    output_path = write_mock_output(settings.output_dir, render_id)
    return RenderResponse(
        status="success",
        render_id=render_id,
        output_image_path=str(output_path),
        enhanced_prompt=prompt.enhanced_prompt,
        negative_prompt=prompt.negative_prompt,
        recommendations=prompt.recommendations,
        warnings=prompt.validation_warnings,
    )


def _render_gemini(
    request: RenderRequest,
    prompt: PromptSuggestionResponse,
    settings: Settings,
) -> RenderResponse:
    if not settings.gemini_api_key:
        raise RenderConfigurationError(
            "RENDER_PROVIDER=gemini requires GEMINI_API_KEY. Set GEMINI_API_KEY or use RENDER_PROVIDER=mock."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RenderConfigurationError(
            "Gemini rendering requires the google-genai package. Install backend requirements first."
        ) from exc

    render_id = create_render_id()
    output_path = settings.output_dir / f"{render_id}.png"
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    contents: list[object] = [prompt.enhanced_prompt]
    viewport_path = _resolve_viewport_path(request.viewport_image_path, settings)
    mime_type = mimetypes.guess_type(viewport_path.name)[0] or "image/png"
    contents.append(
        types.Part.from_bytes(data=viewport_path.read_bytes(), mime_type=mime_type)
    )

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
        )
        for part in response.parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                image = part.as_image()
                image.save(output_path)
                return RenderResponse(
                    status="success",
                    render_id=render_id,
                    output_image_path=str(output_path),
                    enhanced_prompt=prompt.enhanced_prompt,
                    negative_prompt=prompt.negative_prompt,
                    recommendations=prompt.recommendations,
                    warnings=prompt.validation_warnings,
                )
    except Exception as exc:
        raise RenderServiceError(f"Gemini render failed: {exc}") from exc

    raise RenderServiceError("Gemini response did not contain an image.")


def _render_openai(
    request: RenderRequest,
    prompt: PromptSuggestionResponse,
    settings: Settings,
) -> RenderResponse:
    if not settings.openai_api_key:
        raise RenderConfigurationError(
            "RENDER_PROVIDER=openai requires OPENAI_API_KEY. Set OPENAI_API_KEY or use RENDER_PROVIDER=mock."
        )

    viewport_path = _resolve_viewport_path(request.viewport_image_path, settings)
    full_prompt = compose_image_generation_prompt(prompt)

    return _render_openai_edit(
        source_path=viewport_path,
        full_prompt=full_prompt,
        output_resolution=request.render_options.output_resolution,
        prompt=prompt,
        settings=settings,
    )


def _render_openai_edit(
    source_path: Path,
    full_prompt: str,
    output_resolution: str,
    prompt: PromptSuggestionResponse,
    settings: Settings,
) -> RenderResponse:
    render_id = create_render_id()
    output_path = settings.output_dir / f"{render_id}.png"
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with source_path.open("rb") as image_file:
            response = httpx.post(
                "https://api.openai.com/v1/images/edits",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={
                    "model": settings.openai_image_model,
                    "prompt": full_prompt,
                    "n": "1",
                    "size": output_resolution,
                    "output_format": "png",
                },
                files={"image": (source_path.name, image_file, mimetypes.guess_type(source_path.name)[0] or "image/png")},
                timeout=120.0,
            )
    except Exception as exc:
        raise RenderServiceError(f"OpenAI render request failed: {exc}") from exc

    if response.status_code >= 400:
        raise RenderServiceError(f"OpenAI render failed: {response.status_code} {_extract_error_message(response)}")

    try:
        data = response.json()
        image_b64 = data["data"][0]["b64_json"]
        output_path.write_bytes(base64.b64decode(image_b64))
    except Exception as exc:
        raise RenderServiceError("OpenAI render response did not contain a valid base64 image.") from exc

    return RenderResponse(
        status="success",
        render_id=render_id,
        output_image_path=str(output_path),
        enhanced_prompt=prompt.enhanced_prompt,
        negative_prompt=prompt.negative_prompt,
        recommendations=prompt.recommendations,
        warnings=prompt.validation_warnings,
    )


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return str(payload)[:500]


def _resolve_viewport_path(path_value: str, settings: Settings) -> Path:
    export_dir = settings.export_dir.resolve()
    raw_path = Path(path_value)
    if raw_path.is_absolute():
        candidate = raw_path.resolve()
    elif raw_path.parts and raw_path.parts[0] == export_dir.name:
        candidate = (export_dir.parent / raw_path).resolve()
    else:
        candidate = (export_dir / raw_path).resolve()

    if candidate != export_dir and export_dir not in candidate.parents:
        raise RenderConfigurationError("viewport_image_path must resolve under EXPORT_DIR.")
    if not candidate.exists():
        raise RenderConfigurationError(f"viewport_image_path does not exist: {candidate}")
    return candidate


def _resolve_reference_image_path(path_value: str, settings: Settings) -> Path:
    raw_path = Path(path_value)
    allowed_roots = [settings.export_dir.resolve(), settings.output_dir.resolve()]
    if raw_path.is_absolute():
        candidate = raw_path.resolve()
    elif raw_path.parts and raw_path.parts[0] in {root.name for root in allowed_roots}:
        matching = next(root for root in allowed_roots if root.name == raw_path.parts[0])
        candidate = (matching.parent / raw_path).resolve()
    else:
        candidate = (settings.output_dir / raw_path).resolve()

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise RenderConfigurationError("image_path must resolve under EXPORT_DIR or OUTPUT_DIR.")
    if not candidate.exists():
        raise RenderConfigurationError(f"image_path does not exist: {candidate}")
    return candidate
