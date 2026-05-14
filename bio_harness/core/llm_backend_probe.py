from __future__ import annotations

from importlib import import_module
from typing import Any


def _probe_plan(llm: Any) -> dict[str, Any]:
    skills = [
        {
            "name": "bash_run",
            "description": "Run a short shell command in the workspace.",
            "parameters": {
                "command": {
                    "type": "string",
                    "description": "Shell command.",
                    "required": True,
                },
            },
        }
    ]
    return llm.think("Return a one-step test plan that echoes ok.", skills)


def _load_bio_llm() -> type[Any]:
    return getattr(import_module("bio_harness.core.llm"), "BioLLM")


def probe_llm_backend(
    *,
    llm_backend: str | None = None,
    model_name: str | None = None,
    host: str | None = None,
    probe_text: bool = False,
    probe_plan: bool = False,
) -> dict[str, Any]:
    try:
        bio_llm_cls = _load_bio_llm()
    except (ImportError, ModuleNotFoundError) as exc:
        missing_dependency = str(getattr(exc, "name", "") or "").strip()
        message = "llm runtime dependencies unavailable"
        if missing_dependency:
            message += f": missing dependency {missing_dependency}"
        return {
            "available": False,
            "message": message,
            "diagnostics": {
                "backend": str(llm_backend or "").strip(),
                "host": str(host or "").strip(),
                "model_name": str(model_name or "").strip(),
            },
            "exception_class": exc.__class__.__name__,
            "missing_dependency": missing_dependency,
        }

    llm = bio_llm_cls(
        model_name=(str(model_name).strip() or None) if model_name is not None else None,
        host=(str(host).strip() or None) if host is not None else None,
        llm_backend=(str(llm_backend).strip() or None) if llm_backend is not None else None,
    )
    available, message = llm.is_available()
    report: dict[str, Any] = {
        "available": bool(available),
        "message": message,
        "diagnostics": llm.diagnostics(),
    }
    if available and probe_text:
        report["text_probe"] = llm.generate_text("Return plain text only.", "Reply with ok only.")
    if available and probe_plan:
        report["plan_probe"] = _probe_plan(llm)
    return report
