from datetime import datetime, timezone
from typing import Any

from point_cloud_tool import generate_point_cloud
from prompts import AGENT_SYSTEM_PROMPT, assess_agent_readiness
from png_tool import generate_png
from schemas import (
    AgentRunRequest,
    AgentRunResponse,
    PngGenerationRequest,
    PointCloudGenerationRequest,
)
from settings import Settings


def run_agent_pipeline(request: AgentRunRequest, settings: Settings) -> AgentRunResponse:
    started_at = _utc_timestamp()
    api_calls = [_api_call("POST /agent/run", request.user_prompt, started_at)]
    readiness = assess_agent_readiness(request.user_prompt)
    if not readiness.ready:
        return AgentRunResponse(
            status="failed",
            agent_model=settings.agent_model,
            artifacts=[],
            api_calls=api_calls,
            trace=["agent:start", "agent:needs_more_information"],
            started_at=started_at,
            completed_at=_utc_timestamp(),
            warnings=[readiness.reason or "More information is required before generating an image."],
            error_message=readiness.reason or "More information is required before generating an image.",
        )

    warnings: list[str] = []

    if not settings.openai_api_key:
        warnings.append("OPENAI_API_KEY is not configured; deterministic tool orchestration was used.")
        return _run_deterministic_pipeline(request, settings, warnings=warnings, api_calls=api_calls, started_at=started_at)

    if not _langchain_tools_available():
        warnings.append("LangChain OpenAI packages are not installed; deterministic tool orchestration was used.")
        return _run_deterministic_pipeline(request, settings, warnings=warnings, api_calls=api_calls, started_at=started_at)

    try:
        return _run_langchain_agent(request, settings, api_calls=api_calls, started_at=started_at)
    except Exception as exc:
        warnings.append(f"LangChain agent failed; deterministic tool orchestration was used. Reason: {exc}")
        return _run_deterministic_pipeline(request, settings, warnings=warnings, api_calls=api_calls, started_at=started_at)


def _run_deterministic_pipeline(
    request: AgentRunRequest,
    settings: Settings,
    warnings: list[str] | None = None,
    trace: list[str] | None = None,
    api_calls: list[dict[str, Any]] | None = None,
    started_at: str | None = None,
) -> AgentRunResponse:
    trace = [*(trace or ["agent:start"]), "agent:deterministic:start"]
    api_calls = [*(api_calls or [])]
    png_request = PngGenerationRequest.model_validate(request.model_dump(exclude={"pointcloud_output_format"}))
    trace.append("tool:generate_png")
    api_calls.append(_api_call("tool:generate_png", request.user_prompt))
    png = generate_png(png_request, settings)

    point_cloud_request = PointCloudGenerationRequest(
        image_path=png.output_image_path,
        camera=request.camera,
        output_format=request.pointcloud_output_format,
    )
    trace.append("tool:generate_point_cloud")
    api_calls.append(_api_call("tool:generate_point_cloud", request.user_prompt))
    point_cloud = generate_point_cloud(point_cloud_request, settings)
    trace.append("agent:deterministic:complete")

    combined_warnings = [*(warnings or []), *png.warnings, *point_cloud.warnings]

    return AgentRunResponse(
        status="success",
        agent_model=settings.agent_model,
        png=png,
        point_cloud=point_cloud,
        artifacts=[
            {"type": "png", "path": png.output_image_path, "artifact_id": png.artifact_id},
            {
                "type": "point_cloud",
                "path": point_cloud.pointcloud_path,
                "preview_image_path": point_cloud.preview_image_path,
                "artifact_id": point_cloud.artifact_id,
            },
        ],
        api_calls=api_calls,
        trace=trace,
        started_at=started_at,
        completed_at=_utc_timestamp(),
        warnings=combined_warnings,
    )


def _run_langchain_agent(
    request: AgentRunRequest,
    settings: Settings,
    api_calls: list[dict[str, Any]] | None = None,
    started_at: str | None = None,
) -> AgentRunResponse:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import StructuredTool
    from langchain_openai import ChatOpenAI

    trace = ["agent:start", "langchain:tools_available", "agent:llm:start"]
    api_calls = [*(api_calls or [])]
    warnings: list[str] = []
    state: dict[str, Any] = {"png": None, "point_cloud": None}

    def generate_png_tool() -> dict[str, Any]:
        trace.append("tool:generate_png")
        api_calls.append(_api_call("tool:generate_png", request.user_prompt))
        png_request = PngGenerationRequest.model_validate(request.model_dump(exclude={"pointcloud_output_format"}))
        png = generate_png(png_request, settings)
        state["png"] = png
        return png.model_dump()

    def generate_point_cloud_tool(image_path: str | None = None) -> dict[str, Any]:
        png = state.get("png")
        resolved_image_path = image_path or (png.output_image_path if png else None)
        if not resolved_image_path:
            raise ValueError("generate_point_cloud requires an image_path or a generated PNG artifact.")

        trace.append("tool:generate_point_cloud")
        api_calls.append(_api_call("tool:generate_point_cloud", request.user_prompt))
        point_cloud_request = PointCloudGenerationRequest(
            image_path=resolved_image_path,
            camera=request.camera,
            output_format=request.pointcloud_output_format,
        )
        point_cloud = generate_point_cloud(point_cloud_request, settings)
        state["point_cloud"] = point_cloud
        return point_cloud.model_dump()

    tools = [
        StructuredTool.from_function(
            func=generate_png_tool,
            name="generate_png",
            description="Generate a rendered PNG from the current SketchUp viewport and user prompt.",
        ),
        StructuredTool.from_function(
            func=generate_point_cloud_tool,
            name="generate_point_cloud",
            description="Convert the generated PNG into a color point cloud using Depth Anything V2.",
        ),
    ]
    tools_by_name = {tool.name: tool for tool in tools}

    llm = ChatOpenAI(model=settings.agent_model, temperature=0, api_key=settings.openai_api_key)
    llm_with_tools = llm.bind_tools(tools)
    api_calls.append(
        {
            "timestamp": _utc_timestamp(),
            "kind": "api",
            "name": "OpenAI Chat Completions via LangChain",
            "model": settings.agent_model,
            "prompt": request.user_prompt or "",
        }
    )
    messages: list[Any] = [
        SystemMessage(
            content=AGENT_SYSTEM_PROMPT
        ),
        HumanMessage(
            content=(
                f"Style: {request.style}\n"
                f"User prompt: {request.user_prompt or ''}\n"
                f"Output point cloud format: {request.pointcloud_output_format}"
            )
        ),
    ]

    for _ in range(4):
        ai_message = llm_with_tools.invoke(messages)
        messages.append(ai_message)
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            break

        for tool_call in tool_calls:
            name = tool_call.get("name")
            args = tool_call.get("args") or {}
            tool_call_id = tool_call.get("id")
            if name not in tools_by_name:
                raise ValueError(f"Agent requested unknown tool: {name}")

            trace.append(f"agent:llm:tool_call:{name}")
            result = tools_by_name[name].invoke(args)
            messages.append(ToolMessage(content=_tool_message_content(result), tool_call_id=tool_call_id))

        if state.get("png") and state.get("point_cloud"):
            break

    png = state.get("png")
    point_cloud = state.get("point_cloud")
    if not png or not point_cloud:
        warnings.append("LangChain agent did not complete required tools; deterministic fallback was used.")
        return _run_deterministic_pipeline(
            request,
            settings,
            warnings=warnings,
            trace=trace,
            api_calls=api_calls,
            started_at=started_at,
        )

    trace.append("agent:llm:complete")
    warnings.extend([*png.warnings, *point_cloud.warnings])
    return AgentRunResponse(
        status="success",
        agent_model=settings.agent_model,
        png=png,
        point_cloud=point_cloud,
        artifacts=[
            {"type": "png", "path": png.output_image_path, "artifact_id": png.artifact_id},
            {
                "type": "point_cloud",
                "path": point_cloud.pointcloud_path,
                "preview_image_path": point_cloud.preview_image_path,
                "artifact_id": point_cloud.artifact_id,
            },
        ],
        api_calls=api_calls,
        trace=trace,
        started_at=started_at,
        completed_at=_utc_timestamp(),
        warnings=warnings,
    )


def _tool_message_content(result: Any) -> str:
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, str):
        return result
    return str(result)


def _langchain_tools_available() -> bool:
    try:
        from langchain_core.tools import StructuredTool  # noqa: F401
        from langchain_openai import ChatOpenAI  # noqa: F401
    except Exception:
        return False
    return True


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_call(name: str, prompt: str | None, timestamp: str | None = None) -> dict[str, Any]:
    call = {
        "timestamp": timestamp or _utc_timestamp(),
        "kind": "mcp/api",
        "name": name,
        "prompt": prompt or "",
    }
    print(f"[{call['name']}] [{call['timestamp']}] [{call['prompt']}]", flush=True)
    return call
