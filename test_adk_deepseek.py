
import asyncio
import os

from dotenv import load_dotenv
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

load_dotenv()

async def main():
    assert os.getenv("DEEPSEEK_API_KEY"), "缺少 DEEPSEEK_API_KEY"

    model = LiteLlm(
        model="deepseek/deepseek-flash"
    )

    response_text = []

    async for response in model.generate_content_async(
        llm_request=types_placeholder(),
        stream=False,
    ):
        print(response)

# 此示例仅说明 LiteLlm 的接入方向；
# 完整 ADK 测试建议通过 Runner 调用 Agent。
