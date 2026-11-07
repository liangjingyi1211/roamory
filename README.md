# Roamory V7

Google ADK + Streamlit + SQLite 旅行规划器。保留三个专家和 V3 完整生成流程，新增仅支持替换、增加、单日重新规划的 AI 局部修改。工作台提供每日时间线、M1 地点地图与 M2 官方地点详情，左下角新建入口提供独立创建与历史旅程视图。地图不提供路线、交通方式、ETA 或交通费用。

V7 新增旅行插图首页、「我的行程」与「我的旅行日记」，支持在景点、餐厅和酒店卡片记录感想与上传照片。实现与验收见 [V7 实施报告](V7_IMPLEMENTATION.md)。

打开首页后，在左侧创建旅程，或通过右侧入口打开已保存行程与日记。行程页的「记录」按钮提供图文输入、上传预览、移除、保存和取消；日记库直接展示文字与图片，标题旁的「编辑」按钮可直接修改文字、添加/移除图片。日记独立保存，不参与 Agent 推荐，不修改行程版本、地图或时间线。地点被替换或删除后，原记录仍可在日记库浏览和编辑。

页面入口：`?view=new` 创建首页、`?view=trips` 我的行程、`?view=journals` 我的旅行日记、`?trip=<id>` 指定行程。没有指定旅程时默认打开首页。

## 启动与配置

```powershell
uv sync --locked
uv run --env-file .env streamlit run streamlit_app.py
```

打开 http://localhost:8501。继续使用已有的 `GOOGLE_API_KEY`、`ADK_MODEL` 和后端 `GOOGLE_MAPS_API_KEY`；开发过程中未修改实际密钥。

Python 包现为 `roamory`（例如 `from roamory.storage import TripStore`），项目元数据名称为 `roamory`。已有运行进程需重启以加载新包；同源组件会以 `roamory.ui.roamory_workspace` 注册，页面标题与文字 Logo 均为 Roamory。部署继续使用上述 Streamlit 启动命令；本仓库没有项目级 package.json、Dockerfile 或托管平台配置。

重命名兼容说明见 [更名记录](ROAMORY_RENAME.md)。新环境使用 `ROAMORY_DB`、`ROAMORY_LOG_FILE`；旧的 `TRAVELMIND_DB`、`TRAVELMIND_LOG_FILE` 仍可读取，新变量优先。没有显式数据库配置时，优先使用已存在的 `data/roamory.sqlite3`，否则自动复用历史 `data/travelmind.sqlite3`；全新安装默认创建前者。不会自动移动、合并或清空数据库，也不会改写历史日记内容。

交互地图需要独立的 **`GOOGLE_MAPS_BROWSER_API_KEY`**：

1. 在 Google Cloud 中启用 Maps JavaScript API、Places API (New) 和结算；浏览器密钥的 API 限制也需允许这两项。M1 与 M2 权限独立，M1 正常不代表 M2 详情已获准。
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

以下是保留的完整初始生成流程。聊天使用独立的局部修改流程，不会调用此入口覆盖整个 TripPlan。

```text
TripRequest → Coordinator 确定日期与空槽
  → 目的地解析 + 真实景点检索（含兴趣）
  → Activity Agent：每日景点、顺序、建议时间
  → 按每日景点坐标动态检索餐厅
  → Food Agent：从当天对应餐次候选选择 ID
  → 按每日最后景点附近检索酒店
  → Hotel Agent：结合所有日期活动、相邻日期与已选内容选择每晚酒店
  → 后端时间编排、引用校验、重叠提示
  → TripPlan → SQLite → 前端
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
- 名称输入无需检索、验证、地理编码。编辑仅操作目标日期，在 SQLite 事务中同步地点和时间线；失败保留原数据与前端编辑内容。
- 历史 V2 数据缺少 timeline 时读取兼容补建；不会自动重跑 Agent，也不会批量重写原数据库。首次保存修改才持久化迁移结果。

## 前端与地图

Streamlit 原有生成表单迁移到独立创建视图，历史旅程入口在该视图内。顶部由 AI 聊天区替代旧表单/下拉框，移除了 JSON 导出入口。主界面由静态 HTML/CSS/JS 双向组件承载，无 React/npm 运行依赖或独立 HTTP 服务。

- 桌面：左侧日期摘要卡；右侧时间线与地图两栏。窄屏：横向日期列表、时间线、地图依次排列。
- 日期选择完全在前端执行，不提交生成请求，不调用 Agent。
- Google Maps JavaScript API 使用 AdvancedMarkerElement，按当天 Timeline 引用提取有真实坐标的地点。切换日期或编辑时清理旧 Marker，复用同一 Map 实例并 fitBounds。
- Marker 点击与时间线地点点击双向高亮，InfoWindow 展示名称、类型、地址。没有坐标的人工地点仅显示时间线提示，不生成 Marker。
- M1 下方增加 M2。只有明确点击地点时才用其 Google place_id 调用官方 Place.fetchFields；不自动匹配人工名称。按需加载、并发去重和过期结果检查避免详情错位。照片和来源署名仅用于当前面板，不写入 SQLite。
- 页面不调用 Directions、Routes 或 Geocoding API，不绘制任何路线。
- 餐厅详情仅显示旅行日期对应星期的源营业时段，支持多时段与前日跨午夜延续。没有数据时显示“暂无数据”，不把缺失推断成休息或 24 小时营业。

## V6 工作区与持久化

V6 已移除整段旅行费用估算：不生成总额、区间或类别小计。历史 JSON 的 `estimated_total_cost` 字段在读取时忽略，再保存时不写回；地点自己的价格等级、门票和每晚参考价格属性保留。

当前桌面工作区为左右两栏：左侧上 M1、下 M2，使用 `position:sticky; top:0` 保持可见；右侧依次为横向日期、每日行程和 AI 助手，使用浏览器顶层 `window/document` 正常纵向滚动。小屏恢复顶部并排地图，避免行程过窄。同源组件将已有 DOM 呈现到主文档，原 iframe 仅保留事件通信，不承担页面滚动。无独立旅行大标题或 Timeline 内部滚动条；M2 与展开的聊天记录按内容需要内部滚动。蓝色主题及原有地图、手动编辑和三意图聊天功能保留。布局细节见 [Sticky 布局说明](STICKY_LAYOUT.md)。

默认库为 `data/roamory.sqlite3`，可用 `ROAMORY_DB` 指定。页面刷新从 SQLite 恢复，日期选择仅保存在浏览器会话。适用于本机单用户，未提供账号隔离；部署请使用持久化磁盘。日志为 `data/backend.log`（可用 `ROAMORY_LOG_FILE` 修改），不记录密钥；测试通过 fixture 隔离审计，避免污染用户运行日志。

## 文件与验证

| 文件 | 主要职责 |
|---|---|
| `roamory/agent.py` | 三专家分阶段调度、按日动态检索和候选校验 |
| `roamory/places.py` | Text Search/Nearby Search、地域复核、缓存 |
| `roamory/models.py` | V2 契约 + TimelineItem、引用与时间约束 |
| `roamory/timeline.py` | 编排、排序、冲突检测、迁移、当日营业时间 |
| `roamory/storage.py` | SQLite 局部事务编辑、时间权威同步 |
| `roamory/ui.py` | 浏览器密钥隔离、视图数据和编辑事件桥接 |
| `roamory/pages.py` | Hero 首页、已有行程库、旅行日记库与编辑 |
| `roamory/journal.py` | 独立日记模型、SQLite 读写、地点关联与并发版本保护 |
| `roamory/assistant.py` | 三意图结构化解析、目标解析、专家局部调度、受限补丁与回滚 |
| `roamory/frontend/` | 日期选择、时间线、编辑表单、Google Maps 生命周期 |
| `streamlit_app.py` | 页面路由、工作区挂载、编辑与日记事件分流 |
| `tests/test_tools.py`、`tests/test_assistant.py` | 生成与编辑回归、V4 范围保护、并发与迁移 |
| `tests/test_journal.py` | 日记持久化、归属校验、旧数据与首页创建/导航回归 |

```powershell
uv run python -m pytest -q
uv run python -m compileall -q roamory streamlit_app.py
node --check roamory/frontend/app.js
node --check roamory/frontend/place-details.js
node --test --experimental-test-isolation=none tests/frontend_workspace.test.cjs
```

静态组件无需 npm 构建。当前验证结果：34 项 Python 测试、6 项前端交互测试通过；Python 编译、JavaScript 语法和 Git 差异检查通过。Chrome 使用临时数据库验证六组尺寸下的原生主窗口滚动、侧栏地图、日期切换、地点编辑与日记新增/编辑/整页重读，以及桌面和手机首页、行程库和日记库。实际浏览器上传图片后，已验证卡片预览、保存重读、日记图片展示，以及通过独立编辑按钮修改文字和替换图片。

浏览器复验：`.venv/Scripts/python.exe tests/preview_blue.py --host`；仅检查首页和列表可追加 `--pages-only`。截图在 `artifacts/v7-host/`。本轮使用合成行程、离线地图与受控服务替身，未重新执行真实 Gemini/Places 在线调用，不把这些测试视为在线权限验证。

### 已知边界

没有交通规划，也不保证多城市行程的交通可执行性；国家级目的地建议细化为城市。Activity 初始候选仍有 20 个上限；当前未增加城市拆分或无限补查。到离时间继续通过备注表达，没有独立的确定性到离字段。营业时段契约无法无损表达源数据缺少关闭时间的 24 小时表示，故不推断全天营业。

日记仍为本机单用户数据，不提供账号隔离；每个地点卡片一条可编辑的图文记录，文字上限 10000 字符，也支持只有图片。每条最多 6 张 JPG/PNG/WebP 静态图片，每张不超过 5MB、4000 万像素。图片原件与预览保存在同一 SQLite 文件的独立图片表中，不需要外部图床。移除图片仅在保存后生效，取消不改动存储；暂不提供整条日记删除。时间标注 UTC。采用独立 revision 防止覆盖其他窗口的新编辑，冲突后需刷新再提交。旧纯文字记录无需重建；图片字段默认空列表，新增图片表自动创建，TripPlan JSON 不新增日记或图片字段。

参考：[Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search)、[Maps JavaScript API 密钥](https://developers.google.com/maps/documentation/javascript/troubleshooting)、[Streamlit 双向组件](https://docs.streamlit.io/develop/concepts/custom-components/components-v1/intro)。
