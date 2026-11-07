# TravelMind V4 实施报告

## 1. 审查与实施范围

V3 实际架构为 ADK BaseAgent Coordinator + Activity/Food/Hotel 三个 LlmAgent，Streamlit 负责页面和 Python 服务调用，SQLite 保存 TripPlan。浏览器组件使用 Maps JavaScript API weekly 版本、AdvancedMarkerElement 和独立浏览器密钥；后端 Places API (New) 使用另一个密钥执行 Text/Nearby Search。地点的内部实例 ID、Timeline ID、Google place_id 是不同标识。

V4 保留完整初始生成流程，没有新工作流引擎、数据库、大型依赖或第四个专家。开发分为范围保护、M2 与状态联动、页面入口迁移、聊天联调、回归五个增量步骤，未执行 Git Commit。

| 文件 | 变化与原因 |
|---|---|
| `travelmind/assistant.py`（新增） | EditIntent/EditRequest/EditResponse、一次结构化意图解析、后端目标解析、复用相关专家、构造受限补丁、超时与结果反馈 |
| `travelmind/agent.py` | 动态检索支持按 day_id 和餐次限定；完整生成的默认行为保持原样 |
| `travelmind/models.py` | 仅给 TripPlan 增加默认 version=1；旧字段与地点契约保留 |
| `travelmind/storage.py` | 旧库迁移备份、版本列、手动编辑版本校验、受限日字段补丁、原子保存 |
| `travelmind/ui.py` | 带版本的编辑事件、按旅程隔离的聊天视图数据 |
| `travelmind/frontend/place-details.js`（新增） | 官方 Place.fetchFields、当天营业时间、照片归因、并发去重、过期请求忽略、失败降级 |
| `travelmind/frontend/app.js` | 独立日期/地点选择、卡片/Marker/M2 联动、聊天发送/加载/重试、修改后清理无效选择 |
| `travelmind/frontend/index.html`、`style.css` | 顶部紧凑聊天区、M1/M2 上下布局、响应式细节 |
| `streamlit_app.py` | 独立创建/历史视图、浏览器视口固定 FAB、聊天服务桥接；移除顶部旧入口与导出按钮 |
| `tests/test_tools.py` | 兼容新组件参数，保留现有生成/时间线/手动编辑验证 |
| `tests/test_assistant.py`（新增） | 三意图约束、范围保护、真实性、失败回滚、版本冲突与备份迁移 |

## 2. 两条独立工作流

完整生成仍为：输入 → Coordinator → Activity 检索/Agent → 每日 Food 检索/Agent → Hotel 检索/Agent → Timeline → 费用 → 保存。

局部聊天为：当前 trip_id + message + 选择上下文 + trip_version → 服务端加载真实 TripPlan → 单次 LLM 结构化解析 → 后端 Resolve → 明确日/字段/项目范围 → named 确定性更换，或 recommend 检索及唯一相关专家 → 验证候选 → Scoped Patch → Timeline/费用确定性同步 → 乐观锁保存 → 返回摘要、版本和更新日期。

现有项目没有 HTTP 路由，因此新增的是 `assistant_edit()` Python 服务入口与 Streamlit 双向组件事件，等效于任务书的 assistant/edit API；不重复建设一套 HTTP 服务。手动编辑只调用原 TripStore.edit，绝不进入 Intent Parser 或专家。

## 3. 三意图与目标解析

```json
{
  "status": "ready",
  "intents": [{
    "action": "replace",
    "target": "meal",
    "day_index": 2,
    "day_id": null,
    "meal_type": "lunch",
    "target_id": null,
    "target_name": null,
    "new_place_name": null,
    "preferences": "日料",
    "selection_mode": "recommend"
  }],
  "message": null
}
```

action 严格只有 replace / add / replan；解析状态独立为 ready / needs_clarification / not_supported。当前一次只支持一个操作，非 ready 必须没有 intents，多操作不得部分执行。

- replace：已有餐饮槽、唯一景点、已有当晚酒店；自动推荐排除原地点以免声称替换却选回同一家。
- add：新增一个景点或填充空餐饮/酒店；已占用槽位要求用户改用更换。
- replan：明确指定一个日期的整天或 meal/activity/hotel 类别；不会改动其他日期。单个餐次应使用 replace。
- 日期 ID 与编号必须指向同一天。目标 ID 必须存在于服务端当前对象；同名景点不能唯一匹配则澄清。界面选择只作补全上下文。
- named 的名称必须逐字出现在当前用户消息中，且不能只是“日料/博物馆”等泛类别；名称由用户输入，其余事实留空，不检索、不自动补全。
- 删除、时间修改、普通咨询、整个旅行重新生成等不是第四种意图。模型解析会拒绝，常见删除/改时间/全局重生成请求还有前置拦截。

Intent Parser 是同一 Gemini 模型的一次 JSON Schema 调用，不是新增专家。专家通过现有 ADK Agent 配置的 clone 运行，只附加局部范围约束；不能直接提交整份 TripPlan。

## 4. 范围保护、时间与持久化

Coordinator 只允许白名单字段 breakfast/lunch/dinner/activities/hotel。replace/add 景点只修改目标项或追加一项；meal/hotel 只修改精确槽位。单日重规划在临时副本内按 Activity→Food→Hotel 执行；任一阶段失败都不会提交半成品。

后端检索的诊断备注留在临时副本，防止单餐修改夹带其他字段。候选必须属于相应日期/餐次的真实检索池；无结果保留旧安排/空槽并解释。只找到部分三餐新候选时，未找到的餐次保留原值并在结果中说明。

Timeline 仍为时间权威。替换餐次/酒店保持槽位时间；替换活动复用内部实例 ID 以保留用户时间。新增或类别重规划的活动使用建议时间或待安排状态，其他对象时间不变。只有明确 replan 整天才重编该日全部时段。引用、排序、重叠提示和费用均由后端确定性同步。

TripPlan.version 和 SQLite version 列提供乐观锁：模型调用前读取快照，所有外部工作结束后在 BEGIN IMMEDIATE 事务中核对版本，再一次提交。聊天期间另一页面手动保存会使聊天提交返回 conflict，而不是覆盖新值。手动 UI 请求也附带当前版本。

旧 V3 数据 version 默认为 1。旧表没有 version 列时，先用 SQLite backup 保存为相邻的 `*.v3-backup.sqlite3`（不覆盖已有备份），再执行最小列迁移。原 payload 不批量重写，timeline 兼容逻辑继续保留。回退请先停止服务并保留当前库，再使用备份恢复；开发未替换或删除用户原行程。

## 5. M1/M2 状态流

selectedDay 与 selectedPlaceRef 独立维护。selectedPlaceRef 包含 dayId、itemType、内部 ID、place_id 和用于检查替换的内容指纹，绝不按名称或数组位置作为身份。

1. 点击 Day：更新 Timeline/M1 全日 Marker；清空地点选择与 M2，不触发详情检索或 Agent。
2. 点击地点卡片或 M1 Marker：选中相应内部对象，M1 保留全日 Marker、仅高亮/居中当前对象，M2 按其 place_id 获取详情。
3. 无坐标但有本地名称：时间线正常，M1 不造 Marker；M2 不按名称搜索。
4. 选择手动地点：M2 仅显示已有字段与“无可用地图详情”。
5. 替换、删除或切日期：清理旧选择；请求序号保证过时详情不能回填。

同一个 Map 实例跨日期复用。详情请求只在选择后发出；相同 place_id 的在途请求合并，同一已展示选择重复点击不请求。扩展详情只留在当前浏览器内存，不写入 SQLite，不长期缓存。

## 6. M2 官方方案、权限与计费

比较了官方 Places UI Kit 和 Place.fetchFields。UI Kit 目前文档仍包含实验性元素且需要核对额外启用条件；自定义面板更易复用现有 UI、仅显示旅行当天时段并控制字段，因此选择 **Maps JavaScript API 的 Place.fetchFields()**。

按需请求名称、地址、类别、评分、评论数量、Google Maps URL、价格等级、每周营业时段、照片和来源署名；UI 只计算并展示旅行当天时段，不展示整周，不把未知当休息。照片调用官方 getURI，保留 authorAttributions；数据归因和官方 logo 保留。没有评论正文爬取，没有 Google Maps 网页 iframe，没有名称匹配或额外地理编码。

浏览器密钥应在对应项目启用 Maps JavaScript API 和 Places API (New)，保留 HTTP Referer 限制，并在 API 限制中同时允许这两项；只允许 Maps JavaScript API 不等于允许 Places 详情。请求按 Google 当前字段/SKU 规则计费，评分、营业时间和照片可能提高字段层级；本实现不抓取全量详情、不批量加载所有地点，不报告未经核实的固定价格。

本轮真实测试中，M1 地图正常，M2 返回 **HTTP 403 / PERMISSION_DENIED**。错误面板已正确退回存储字段、当天营业时间、来源链接和重试，不影响 M1/Timeline。无法仅凭这两个状态确定是服务未启用还是 API Key 限制，仍需核对云端配置；不能声称真实详情授权成功。外部权限配置不由本地代码擅自修改。

参考：[Places UI Kit](https://developers.google.com/maps/documentation/javascript/places-ui-kit/overview)、[Place Details Elements](https://developers.google.com/maps/documentation/javascript/places-ui-kit/place-details)、[Place.fetchFields](https://developers.google.com/maps/documentation/javascript/place-details)、[Places 展示与归因](https://developers.google.com/maps/documentation/places/web-service/policies)。

## 7. 页面与反馈

主页面顶部是紧凑聊天区，不再出现旧创建折叠区/历史下拉框。FAB 固定在浏览器视口左下角，打开独立 `view=new` 视图；该视图提供创建表单、返回原旅程及最近旅程按钮。取消不覆盖原行程，创建失败保留输入。前端 JSON 导出入口已删除。

聊天消息按 trip_id 放在 Streamlit 会话中，不持久化到数据库，刷新/重启可能清空。当前旅程上下文、用户消息、处理结果明确区分。处理中禁止重复提交；失败可重试，冲突要求刷新。成功摘要显示 Day、字段与实际地点名。只有服务端保存成功的 TripPlan 才更新 Timeline/M1/M2/费用。

桌面保留日期/时间线/地理信息三栏，第三栏为 M1 在上、M2 在下；窄屏为日期→时间线→M1→M2。FAB 与组件滚动独立，保留键盘 focus 和 aria-label。

## 8. 验证结果

- `uv run pytest -q`：14 项通过。包括原 V3 回归、意图契约、澄清不写入、单槽位修改、自动候选校验、类别重规划、单日原子回滚、越界补丁拒绝、并发冲突、V3 备份迁移。
- Python compileall、两个 JavaScript 的 `node --check` 与 Git 差异检查通过。项目没有额外 npm 构建或已配置的独立 lint/类型检查流程。
- 真实 Gemini：推荐替换、明确名称替换均解析正确；删除与整个旅行重生成返回 not_supported。
- 真实 Food Agent + Places：在独立验证库中把 Day 2 午餐替换为 SUSHI KU，返回 success 和新版本，没有调用全旅程生成入口。
- 无头 Chrome：日期切换、M1/M2 选择与反向联动、过时请求忽略、重复点击不重取、单一 Map、失败降级/重试、人工地点不检索、真实聊天 named 保存、unsupported 不写入、手动替换后清空详情、FAB/创建/历史/返回、桌面/手机无横向溢出通过。Google SDK 测试替身只证明交互逻辑，不代表真实详情授权。
- 真实 M2 的权限测试已运行，结果为 403/PERMISSION_DENIED，非“未测试”。一次性浏览器测试脚本验证后删除，验证库与截图在已忽略的 data/ 中，不加入运行依赖。

## 9. 限制

一次聊天一个操作；不支持删除、改时间、咨询和全旅程重生成。没有新的交通或预算能力。对话不持久化；目前是本机单用户产品，不是多租户 HTTP 服务。请求最多 2000 字，解析上下文有大小限制，单旅程本进程内最少间隔 2 秒；解析超时 40 秒，专家 50 秒，局部执行总超时 150 秒；附近检索继续使用 V3 的有限半径扩大与缓存，不做无限循环。

M2 外部权限尚需配置确认。API 返回字段可能缺失，照片/评分不会补造。整体交通可行性、到离时刻的结构化约束、对话长期存储、多人协作等留待后续版本。
