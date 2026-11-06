# TravelMind V2

基于 Google ADK 的旅行规划工具：真实 Places 候选地点、三个专业 Agent、结构化行程、可编辑每日卡片和本地持久化。保留原有 Python + Streamlit 架构，不新增前端框架、HTTP 服务或大型依赖。

## 启动

```powershell
uv sync --locked
uv run --env-file .env streamlit run streamlit_app.py
```

打开 http://localhost:8501。使用已有 `.env` 中的 `GOOGLE_API_KEY`、`GOOGLE_MAPS_API_KEY` 和 `ADK_MODEL`。本次开发未修改密钥；新部署才需要参照 `.env.example` 配置。Maps 项目需要启用 Places API (New)、结算，并允许服务端请求。密钥不会发往浏览器。

调试同一个 Coordinator：

```powershell
uv run --env-file .env adk web .
```

ADK Web 输入结构化请求，日期不能省略：

```json
{
  "destination": "Tokyo",
  "start_date": "2027-04-01",
  "end_date": "2027-04-03",
  "preferences": {
    "interests": ["博物馆", "公园"],
    "food_preferences": ["日式料理"],
    "travel_pace": "relaxed",
    "additional_notes": "第一天下午抵达，最后一天午后离开"
  }
}
```

Streamlit 直接用表单收集这些字段，不再输入预算或航班。ADK Web 用于生成调试；通过 Streamlit 生成或调用 `TripStore.save()` 才会写入行程库。

## 架构与主要文件

| 模块 | 作用 |
|---|---|
| `travelmind/models.py` | Pydantic 请求、地点、每日计划、TripPlan 与费用模型，校验日期和实例 ID |
| `travelmind/places.py` | Google Places Text Search (New)，显式字段掩码，15 分钟进程内缓存，错误直报 |
| `travelmind/agent.py` | ADK BaseAgent Coordinator 与 Activity / Food / Hotel 三个 LlmAgent；候选 ID 校验、按日填槽 |
| `travelmind/tools.py` | 确定性日期和费用运算，不推断缺失价格，不混加币种 |
| `travelmind/service.py` | 统一 Runner 入口，接收 TripRequest，返回 TripPlan 与执行轨迹 |
| `travelmind/storage.py` | SQLite 保存、读取、列表及按 trip/day/item ID 的局部编辑 |
| `streamlit_app.py` | 纵向折叠每日卡片、详情、名称编辑、删除、景点排序、费用与 JSON 导出 |
| `tests/test_tools.py` | 复用原测试文件，验证日期、费用、真实性、故障降级、协调流程及页面编辑保存 |

数据流：表单 → TripRequest 校验 → 后端初始化精确日期与空槽 → Places 三类候选检索 → Activity Agent → Food Agent → Hotel Agent → Coordinator 验证候选 ID / 日期并映射原始事实 → 确定性费用计算 → TripPlan → SQLite → 每日卡片。

采用顺序调度，使餐饮和住宿能读取已生成景点。专业 Agent 只输出本职选择，不输出整份 TripPlan；主协调器用程序执行完整流程，不依赖 LLM 自行转交或返回。两个入口共用 `root_agent`。Gemini 传输 Schema 与严格的业务模型分开，避免当前接口对 `additionalProperties` 的兼容性问题。

## 编辑与保存

本项目原先没有 HTTP API，因此复用 Streamlit Python 调用链，不另建路由。`TripStore.get()` 等效于获取接口；`TripStore.edit(trip_id, day_id, slot, action, ...)` 提供餐饮/酒店替换与清空、景点添加/替换/删除/排序。非法槽位、空名称、不存在的行程/日期/景点会报错。

所有编辑在 SQLite 事务内完成；更换只在保存成功时提交，取消保留原值。人工输入仅生成本地 ID 和名称，不自动检索或补全，替换已有推荐会清空原地点资料。每次修改仅确定性重算费用，不调用模型或 Places。

默认保存为 `data/travelmind.sqlite3`（已忽略 Git），可用 `TRAVELMIND_DB` 改位置。页面 URL 带 `trip` ID，刷新后重新读取数据库；没有 URL 时也可从已保存旅程选择。适用于本机单用户；无账号隔离、远程同步或多用户授权。部署时必须将数据库放在持久化卷上。折叠状态只属于页面 UI，不写入 TripPlan。

## 地点与费用边界

- 地点只来自 Places API，不再保留模拟数据。模型只能选择 API 返回的 ID；找不到的餐厅保持 `null`，某类检索失败会在行程中明确说明并保留其他结果。全部失败时会得到有明确错误备注的空行程，不能视为推荐成功。
- 查询先解析目的地边界与国家，Places 搜索带 `locationRestriction`，后端再次校验坐标和国家；缺少地理信息的候选不会交给 Agent。口味等自由文本只交给专家筛选，不拼入地点搜索，避免负面偏好改变搜索语义。无法解析目的地时明确失败，不降级为全世界搜索。输入国家仍可能涉及多个城市，实际旅行建议指定城市。
- `data/backend.log` 是轮转后端日志，记录目的地解析、查询、拒绝候选和专家选择 ID，不记录密钥。可用 `TRAVELMIND_LOG_FILE` 改位置。历史版本没有此日志；旧行程不会因修复而自动重写。
- 每轮一次目的地解析，再检索三类候选，每类最多 20 个；相同目的地和分类查询进程内缓存 15 分钟。长行程的候选可能不足，可重复推荐；当前不实现跨区域多轮搜索和路线时间计算。
- 营业时间仅展示，不对餐次做硬过滤。来源未提供时为空；缺少关闭时间的 24 小时表示无法用当前时段契约无损表达，因此不推断成全天营业。
- Google 的评分和价格等级按原始数据映射。菜系、门票、酒店每晚价格等没有可靠字段时保持空；`price_level` 不转换为消费金额。
- 目前没有可靠的餐饮金额、票价、酒店实时房价或汇率服务，因此通常返回 `low: null, high: null`，明确提示价格不足；这属于正确处理缺失信息。
- 若条目已有金额，只按币种分别提供“已知项目小计”；不会把未知价当 0，也不跨币种求和。默认排除机票，不宣称可预订。
- 建议游览时间由模型规划，不代表已验证开放时间或交通耗时。用户的到离时间说明交给专家考虑，但当前未建立确定性的到离时间约束字段。

## 对象示例

以下自动推荐字段仅展示结构；尖括号内容必须替换为实际 API 返回值，不能把此示例作为已核实地点使用：

```json
{
  "id": "local-instance-uuid",
  "name": "<Places displayName.text>",
  "place_id": "<Places id>",
  "address": "<Places formattedAddress 或 null>",
  "latitude": null,
  "longitude": null,
  "description": null,
  "source_url": "<Places googleMapsUri 或 null>",
  "cuisine": [],
  "price_level": null,
  "opening_hours": []
}
```

同一天可以同时包含餐厅空槽和仅名称的人工输入，字段始终保留：

```json
{
  "day_id": "day-uuid",
  "day_index": 1,
  "date": "2027-04-01",
  "title": "",
  "breakfast": null,
  "lunch": {
    "id": "manual-item-uuid",
    "name": "我自己选的餐厅",
    "address": null,
    "latitude": null,
    "longitude": null,
    "description": null,
    "source_url": null,
    "place_id": null,
    "cuisine": [],
    "price_level": null,
    "opening_hours": []
  },
  "dinner": null,
  "activities": [],
  "hotel": null,
  "notes": []
}
```

费用不足时的摘要（实际 categories 包含四类费用）：

```json
{
  "low": null,
  "high": null,
  "currency": null,
  "is_complete": false,
  "categories": [
    {"category": "food", "low": null, "high": null, "currency": null,
     "basis": "地点价格等级不等于金额，未推断价格", "coverage_note": "没有可信价格，不能按 0 计算"}
  ],
  "excluded_categories": ["flights"],
  "missing_information": ["food：价格、安排或统一币种信息不足"],
  "note": "总费用暂无法估算（价格信息不足）；类别金额仅为已知项目小计。机票不包含在内。"
}
```

## 验证

```powershell
uv run pytest -q
uv run python -m compileall -q travelmind streamlit_app.py
```

Streamlit 是 Python 页面，没有 npm 前端构建；测试使用 Streamlit AppTest 实际渲染并保存编辑，再重新加载验证持久化。测试内使用明确的 API fixture，不会在生产代码中启用模拟回退。

开发期间已通过现有密钥验证 Places 返回真实餐厅，以及真实 Gemini / ADK 两天行程生成：三个专业 Agent 均执行，活动、餐厅和酒店成功映射，离开日住宿为空。没有新增必需服务或依赖；没有提交 Git Commit。

实现参考：[Places Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search)、[Places 字段](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places)、[ADK LlmAgent](https://github.com/google/adk-python/blob/main/src/google/adk/agents/llm_agent.py)。
