from agent import suggest_prompt
from renderers import render_image
from schemas import PngGenerationRequest, PngGenerationResponse
from settings import Settings


def generate_png(request: PngGenerationRequest, settings: Settings) -> PngGenerationResponse:
    prompt = suggest_prompt(request)
    render = render_image(request, prompt, settings)
    return PngGenerationResponse(
        status=render.status,
        artifact_id=render.render_id,
        output_image_path=render.output_image_path,
        preview_image_path=render.output_image_path,
        enhanced_prompt=render.enhanced_prompt,
        negative_prompt=render.negative_prompt,
        provider=settings.render_provider,
        model=_provider_model(settings),
        recommendations=render.recommendations,
        warnings=render.warnings,
        error_message=render.error_message,
    )


def _provider_model(settings: Settings) -> str | None:
    if settings.render_provider == "openai":
        return settings.openai_image_model
    if settings.render_provider == "gemini":
        return settings.gemini_model
    return None
