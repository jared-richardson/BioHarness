import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pymupdf4llm
from pydantic import BaseModel, Field, ValidationError

from bio_harness.core.llm import BioHarnessError, BioLLM

logger = logging.getLogger(__name__)


class PipelineStep(BaseModel):
    tool: str = Field(description="The name of the bioinformatics tool used.")
    version: str | None = Field(None, description="The version of the tool used, if specified.")
    flags: List[str] = Field(default_factory=list, description="Flags or parameters used.")
    description: str | None = Field(None, description="Brief description of this step's purpose.")


class PipelineExtractionOutput(BaseModel):
    pipeline_steps: List[PipelineStep] = Field(description="Extracted pipeline steps.")


class Reader:
    """PDF and text analysis helper powered by local BioLLM."""

    def __init__(self, model_name: str | None = None, host: str | None = None, llm_backend: str | None = None):
        self.biollm = BioLLM(model_name=model_name, host=host, llm_backend=llm_backend)

    def pdf_to_markdown(self, file_path: Path) -> str:
        if not file_path.is_file():
            raise IOError(f"PDF file not found: {file_path}")
        try:
            return pymupdf4llm.to_markdown(file_path)
        except Exception as exc:
            logger.error("Error converting PDF to markdown: %s", exc)
            raise

    def summarize_markdown(self, markdown_text: str) -> str:
        instruction = (
            "Summarize this scientific document for a bioinformatics user. "
            "Include objective, inputs, methods, key tools, outputs, assumptions, and limitations."
        )
        return self.biollm.summarize_text(markdown_text, instruction)

    def extract_pipeline_logic(self, markdown_text: str) -> List[Dict[str, Any]]:
        system_prompt = """Extract every bioinformatics pipeline step from text.
Return ONLY valid JSON:
{
  "pipeline_steps": [
    {
      "tool": "ToolName",
      "version": "optional",
      "flags": ["-x 1", "--threads 8"],
      "description": "short purpose"
    }
  ]
}
"""
        user_query = f"Extract pipeline steps from:\n\n{markdown_text[:120000]}"
        raw_content = ""
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]

        try:
            llm_output_data = self.biollm._request_structured_response(
                stage="reader_pipeline_extraction",
                schema_model=PipelineExtractionOutput,
                messages=messages,
                num_predict=1200,
                normalizer=lambda x: x,
            )
            validated_output = PipelineExtractionOutput(**llm_output_data)
            return [step.model_dump() for step in validated_output.pipeline_steps]
        except ValidationError as exc:
            raise BioHarnessError(f"Pipeline extraction schema validation failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise BioHarnessError(f"LLM returned invalid JSON: {exc}; raw={raw_content}") from exc
        except Exception as exc:
            if self.biollm._backend.is_connectivity_error(exc) or "not found" in str(exc):
                raise BioHarnessError(f"LLM backend error during extraction: {exc}.") from exc
            raise
