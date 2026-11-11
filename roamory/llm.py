"""DeepSeek JSON mode with application-side schema validation."""
import json

from google.adk.models.lite_llm import LiteLlm
from pydantic import BaseModel


class DeepSeekJsonLlm(LiteLlm):
    """Keep ADK output schemas while using DeepSeek's Chat JSON format."""

    async def generate_content_async(self, llm_request, stream=False):
        # ADK's default conversion sends json_schema, which this endpoint rejects.
        # Copy before adapting so retries and other users retain the original schema.
        request = llm_request.model_copy(deep=True)
        schema = request.config.response_schema or request.config.response_json_schema
        if schema is not None:
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                schema = schema.model_json_schema()
            elif isinstance(schema, BaseModel):
                schema = schema.model_dump(mode="json", exclude_none=True)
            request.append_instructions([
                "Return only one valid JSON object, without Markdown or commentary. "
                "Follow this JSON Schema exactly (including required fields and types):\n"
                + json.dumps(schema, ensure_ascii=False)
                + ('\nExample JSON envelope for recommendation results: {"days": []}. '
                   if "days" in schema.get("properties", {}) else "\n")
                + "Populate it with the requested selections; empty slots may be null "
                "only where the schema allows."
            ])
            # LiteLlm passes an already formatted response_schema dictionary through.
            request.config.response_schema = {"type": "json_object"}
            request.config.response_json_schema = None
        async for response in super().generate_content_async(request, stream=stream):
            yield response
