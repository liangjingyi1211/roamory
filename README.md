# TravelMind V3

Google ADK + Streamlit + SQLite 旅行规划器。三个专家按阶段生成真实地点推荐，后端确定性编排每日时间线；左侧选择日期，右侧同时展示当天时间线与 Google Maps。地图只展示地点，不提供路线、交通方式、ETA 或交通费用。

## 启动与配置

```powershell
uv sync --locked
uv run --env-file .env streamlit run streamlit_app.py
```

打开 http://localhost:8501。继续使用已有的 `GOOGLE_API_KEY`、`ADK_MODEL` 和后端 `GOOGLE_MAPS_API_KEY`；开发过程中未修改实际密钥。

交互地图需要独立的 **`GOOGLE_MAPS_BROWSER_API_KEY`**：

1. 在 Google Cloud 中启用 Maps JavaScript API 和结算。
2. 为该浏览器密钥设置 HTTP Referer 限制，例如 `http://localhost:8501/*`、`http://127.0.0.1:8501/*` 以及实际部署域名。验证端口 8511 需要单独允许。
3. 将其加入本机 `.env`，重启应用。不要把密钥提交到 Git。
4. 后端密钥仅供服务端 Places API (New)。浏览器配置为空或与后端相同时，程序不发送后端密钥，并在地图区域显示原因。

浏览器密钥会按 Google 的客户端 API 机制用于加载 SDK，它应通过域名和 API 限制保护。没有云账号管理权限时，程序不能替用户检查/修改密钥的云端限制；加载失败会显示授权、网络或超时提示。未配置浏览器密钥时，行程生成、时间线和编辑仍可使用，但不会显示伪造地图。

ADK Web 调试同一份 Coordinator：

```powershell
uv run --env-file .env adk web .
```

输入完整日期的 JSON（Streamlit 使用表单收集）：

```json
{
  "destination": "Sydney, Australia",
  "start_date": "2027-04-01",
  "end_date": "2027-04-03",
  "preferences": {
    "interests": ["museum", "park"],
    "food_preferences": ["不吃辣"],
    "travel_pace": "moderate",
    "additional_notes": null
  }
}
```

ADK Web 输出结构化 TripPlan；通过 Streamlit 生成或显式调用 TripStore.save 才会写入 SQLite。

## 数据流与 Agent

```text
TripRequest → Coordinator 确定日期与空槽
  → 目的地解析 + 真实景点检索（含兴趣）
  → Activity Agent：每日景点、顺序、建议时间
  → 按每日景点坐标动态检索餐厅
  → Food Agent：从当天对应餐次候选选择 ID
  → 按每日最后景点附近检索酒店
  → Hotel Agent：结合所有日期活动、相邻日期与已选内容选择每晚酒店
  → 后端时间编排、引用校验、重叠提示
  → 费用估算 → TripPlan → SQLite → 前端
```

专业 Agent 只输出自身的地点选择，Coordinator 从 API 返回值映射名称、地址、坐标等事实；不会让模型补造缺失属性。餐厅按 `day_index → meal_type` 限定，酒店按 `day_index` 限定，不能借用其他日期候选。酒店允许相同，也允许更换；离开日默认无酒店。

### 动态检索

- 使用 Places API (New) Text Search 解析目的地和检索景点；使用 **Nearby Search** 按真实活动坐标检索餐厅、酒店。
- 早餐靠近首个景点，午餐在当天前半段与中段景点相关区域，晚餐靠近最后景点；酒店以当天最后景点为中心。
- 初始半径 2km，只有没有候选时扩大到 8km，最多两次。国家和圆形范围由后端复核；直线距离仅用于地点粗筛，不是路线或交通计算。
- 同一目的地、类别、坐标、半径进程内缓存 15 分钟；不会为每个 Agent 重复解析目的地。
- 当天没有景点坐标时，仅可回退到已解析的明确城市中心。国家级目的地没有活动坐标则保留空槽，不退回全国随机推荐。
- 营业时间不用于硬性过滤。查不到的推荐保留空槽，错误与缺口写入当天备注和 `data/backend.log`。

## 时间线与编辑

保留 V2 的 `breakfast/lunch/dinner/activities/hotel` 字段，新增 `DailyPlan.timeline` 和 `timeline_warnings`。TimelineItem 只保存关联 ID，不复制地点详情。

```json
{
  "id": "timeline-instance-uuid",
  "item_type": "meal",
  "reference_id": "restaurant-instance-uuid",
  "meal_type": "lunch",
  "title": "餐厅名称",
  "start_time": "12:00:00",
  "end_time": "13:00:00",
  "notes": null
}
```

- **Timeline 是时间的单一权威来源**。Activity.planned_start_time/end_time 是兼容投影，保存、加载及编辑时同步。
- 首次编排参考活动建议时间与时长，为三餐保留通常时段；缺少活动时长时使用明确的 120 分钟规划默认值。必要时顺延活动，全天放不下的项目保留空时间。默认时长与时间均是规划建议，不是地点事实。
- 全部时间是目的地当地钟表时间。历史模型输出的 `Z` 等时区标记按建议钟表时间规范化，不进行伪造的跨时区换算。
- 编辑时间后只重新排序和提示重叠/不通常用餐时段，不擅自移动其他项目。倒置时间或无效引用拒绝保存；合法但重叠的时间保留并提示。
- 替换餐厅/酒店保留该槽位的时间，更换地点清空旧来源事实；删除时清理旧引用。景点排序同步分配已有活动时段，手动新增景点默认“时间待安排”。
- 名称输入无需检索、验证、地理编码。编辑仅操作目标日期，在 SQLite 事务中同步地点、时间线和费用；失败保留原数据与前端编辑内容。
- 历史 V2 数据缺少 timeline 时读取兼容补建；不会自动重跑 Agent，也不会批量重写原数据库。首次保存修改才持久化迁移结果。

## 前端与地图

Streamlit 原有生成表单、会话入口和费用概览保留，主界面由一个静态 HTML/CSS/JS 双向组件承载，无 React/npm 运行依赖或独立 HTTP 服务。

- 桌面：左侧日期摘要卡；右侧时间线与地图两栏。窄屏：横向日期列表、时间线、地图依次排列。
- 日期选择完全在前端执行，不提交生成请求，不调用 Agent。
- Google Maps JavaScript API 使用 AdvancedMarkerElement，按当天 Timeline 引用提取有真实坐标的地点。切换日期或编辑时清理旧 Marker，复用同一 Map 实例并 fitBounds。
- Marker 点击与时间线地点点击双向高亮，InfoWindow 展示名称、类型、地址。没有坐标的人工地点仅显示时间线提示，不生成 Marker。
- 页面不调用 Directions、Routes 或 Geocoding API，不绘制任何路线。
- 餐厅详情仅显示旅行日期对应星期的源营业时段，支持多时段与前日跨午夜延续。没有数据时显示“暂无数据”，不把缺失推断成休息或 24 小时营业。

## 费用与持久化

不接受预算输入，不含机票或本地交通。只计算有依据的景点、餐饮、住宿类别金额；缺价不当成 0，币种不混加。Places 的价格等级不转换为金额；当前没有可靠餐饮金额来源，因此通常显示总价信息不足，有已知金额时按币种展示部分小计。

默认库为 `data/travelmind.sqlite3`，可用 `TRAVELMIND_DB` 指定。页面刷新从 SQLite 恢复，日期选择仅保存在浏览器会话。适用于本机单用户，未提供账号隔离；部署请使用持久化磁盘。日志为 `data/backend.log`（可用 `TRAVELMIND_LOG_FILE` 修改），不记录密钥；测试通过 fixture 隔离审计，避免污染用户运行日志。

## 文件与验证

| 文件 | 主要职责 |
|---|---|
| `travelmind/agent.py` | 三专家分阶段调度、按日动态检索和候选校验 |
| `travelmind/places.py` | Text Search/Nearby Search、地域复核、缓存 |
| `travelmind/models.py` | V2 契约 + TimelineItem、引用与时间约束 |
| `travelmind/timeline.py` | 编排、排序、冲突检测、迁移、当日营业时间 |
| `travelmind/storage.py` | SQLite 局部事务编辑、时间权威同步 |
| `travelmind/ui.py` | 浏览器密钥隔离、视图数据和编辑事件桥接 |
| `travelmind/frontend/` | 日期选择、时间线、编辑表单、Google Maps 生命周期 |
| `streamlit_app.py` | 表单、费用概览、工作区挂载与保存反馈 |
| `tests/test_tools.py` | 保留原测试文件，增量验证 V3 核心行为 |

```powershell
uv run pytest -q
uv run python -m compileall -q travelmind streamlit_app.py
node --check travelmind/frontend/app.js
```

静态组件无需 npm 构建。开发期间已通过真实 Places/Gemini 三天悉尼行程：每天 2 个景点、三餐均有推荐、前两晚酒店不同、离开日无酒店，统一时间线无直接重叠。验证数据与用户库分开保存。

本次验证结果：8 项 pytest 测试通过，Python 编译、JavaScript 语法和 Git 差异检查通过。无头 Chrome 实际验证了桌面/手机布局、手机无横向溢出、日期切换、时间编辑与重叠提示、地点替换/删除和刷新恢复；操作前后生成日志数量相同。独立 JavaScript API 测试替身验证了 Marker 清理、双向高亮、缺坐标处理，以及跨日期复用单一 Map 实例。一次性浏览器检查脚本验证后删除。

地图的真实授权和图块展示需要配置独立浏览器密钥后验证；测试替身只能验证 Marker 更新和交互逻辑，不能证明云端 API 已启用或授权成功。

### 已知边界

没有交通规划，也不保证多城市行程的交通可执行性；国家级目的地建议细化为城市。Activity 初始候选仍有 20 个上限；当前未增加城市拆分或无限补查。到离时间继续通过备注表达，没有独立的确定性到离字段。营业时段契约无法无损表达源数据缺少关闭时间的 24 小时表示，故不推断全天营业。编辑时未实现多人并发版本冲突提示。

参考：[Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search)、[Maps JavaScript API 密钥](https://developers.google.com/maps/documentation/javascript/troubleshooting)、[Streamlit 双向组件](https://docs.streamlit.io/develop/concepts/custom-components/components-v1/intro)。
