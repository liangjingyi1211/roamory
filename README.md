# TravelMind (Local Google ADK starter)

A **four-agent** ADK travel-planning demonstration. Unlike the DataCamp article's fixed 3-HTTP-call host, this version uses **LLM-driven ADK sub-agent delegation**, and has **executable demo catalog tools** and a deterministic budget calculator. It is **not A2A yet**. A2A is a separate interoperability protocol; use official ADK `to_a2a` or `RemoteA2aAgent` for the later upgrade.

## Windows + PowerShell + uv

Install Python 3.11 or 3.12 and `uv`. In this folder:

```powershell
uv python install 3.12
uv sync --python 3.12
Copy-Item .env.example .env
Copy-Item travelmind/.env.example travelmind/.env
```

**Edit both `.env` files and fill in GOOGLE_API_KEY (get from Google AI Studio).** Never commit keys. `GOOGLE_GENAI_USE_VERTEXAI=FALSE` uses the Gemini Developer API. Google account/API key must have access to the model. The app makes remote LLM API calls; 'local' here means the Python services execute locally.

### Option A: ADK debug UI (start here)

```powershell
uv run --env-file .env adk web .
```

Open http://127.0.0.1:8000 and choose `travelmind`. Try:

- `我已经订好了去东京的机票，帮我推荐活动，不用查航班。`
- `从新加坡去东京，2027-04-01 到 2027-04-08，预算 1500 SGD，安排航班、酒店和活动，检查总预算。`

Inspect event / transfer traces in the ADK web console. Tool selection is model-dependent; if it doesn't delegate, try a more explicit request and inspect the log.

### Option B: Streamlit UI

In another terminal (or stop ADK UI):

```powershell
uv run --env-file .env streamlit run streamlit_app.py
```

Open http://localhost:8501.

### Tests

```powershell
uv run pytest -q
```

## Limitations

- Search tools return **illustrative local static catalog** entries for Tokyo, Kyoto, Singapore, not live availability/verified prices.
- Delegation is model-driven; not guaranteed in every turn.
- InMemorySessionService sessions do not persist across Streamlit submissions in this starter.
- Google ADK APIs and model IDs change; recheck official docs if an SDK or model error appears.
- This demo does **not** implement the actual A2A protocol. The DataCamp tutorial's POST `/run` is ordinary REST, not the complete standardized A2A transport/messages.

## Next: true A2A

See official docs:
- https://adk.dev/a2a/quickstart-exposing/
- https://adk.dev/a2a/quickstart-consuming/

Exposing an ADK agent is supported with `google.adk.a2a.utils.agent_to_a2a.to_a2a(root_agent, port=8001)` then `uvicorn` serving the resulting ASGI app. Consuming agents use `RemoteA2aAgent` with the remote server's agent card URL. Do **not** simply rename `/run` to A2A.

## Source comparison

Tutorial: https://www.datacamp.com/zh/tutorial/agent-development-kit-adk
ADK docs: https://adk.dev/get-started/python/
