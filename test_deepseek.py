
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

response = client.chat.completions.create(
    model="deepseek-flash",
    messages=[
        {
            "role": "system",
            "content": "你是一名专业的旅行规划助手。"
        },
        {
            "role": "user",
            "content": "推荐三个新加坡值得游玩的景点。"
        }
    ],
    extra_body={"thinking": {"type": "disabled"}},
)

print(response.choices[0].message.content)
