# TravelMind V6 实施与验收

> 当前布局已按后续用户要求调整为全宽 Sticky 双地图和主文档滚动，删除独立旅程标题及地图标题栏。最新结构与验证见 `STICKY_LAYOUT.md`；下文保留 V6 初版实施记录。

> 后续调整：按用户要求取消底部助手固定。AI 助手现位于 `journey-content` 内、时间线之后，与行程一起滚动；顶部地图和旅程标题仍固定。主 Grid 改为四行。下文五区与底部固定的描述为 V6 初版记录。

## 范围

根据 `TravelMind_V6_Codex_Prompt.md`，复用现有 Streamlit + 原生 HTML/CSS/JavaScript 组件及 Premium Blue 主题。未添加 UI 框架，未重写 Agent、检索或局部修改架构。

已读取 UI/UX Pro Max Skill，并应用可见焦点、收缩网格、滚动区域约束、触控区域与低高度降级原则。主题变量保持不变。

## 页面结构与滚动

内嵌组件采用五行 Grid：

1. TravelMind 品牌标题。
2. M1 地图 / M2 地点详情并排，约 55:45。
3. 目的地、旅行天数与起止日期。
4. `journey-content`：同一个垂直滚动容器中包含横向日期选择与当天时间线。
5. AI 助手：输入、发送、可展开且限高的聊天记录、状态与重试。

`.app-shell` 使用 `height:100dvh`、`grid-template-rows:auto clamp(180px,29dvh,300px) auto minmax(0,1fr) auto` 和 `overflow:hidden`。中间行设置 `min-height:0; overflow-y:auto`，其他主区域不随时间线滚动。日期列表为不换行的 flex 横向滚动，切换日期将行程滚动位置归零。

M2 是限高面板，内容自行滚动；聊天历史同样限高。低于 620px 的组件视口降级为内嵌文档滚动，中间区取消独立滚动，使地图、行程和发送按钮均能访问。未用绝对定位或负 margin 拼接主布局。

Streamlit 外层给其工具栏预留 3.75rem，组件高度填满剩余视口；仅工作区页面关闭外层主滚动。新建与历史页面保留正常滚动。实测并消除了默认区块间距导致的 12px 溢出。左下角新建入口保留，助手左侧为其留出空间。

## 联动与保留功能

- 切换 Day：刷新当天时间线与 Marker，清除上个日期的 M2 选择；不触发 Agent。
- 点击卡片或 Marker：保留原 `focusPlace`、选中 Marker、地图定位和 M2 详情获取链路。
- 手动增删替换地点、时间编辑、排序：保留事件契约、SQLite 持久化及时间线同步。
- 没有坐标不生成 Marker；没有 Place ID 仅显示已保存字段，不虚构 Google 详情。
- 三个专家、首次生成、动态检索及 replace / add / replan 三种聊天意图保留。

## 费用功能清理与兼容

删除：

- `tools.py` 的 `estimate_cost` 及金额求和、币种分组、完整性判断。
- `CostEstimate` 和 `CostCategoryEstimate` 模型及专属校验。
- `TripPlan.estimated_total_cost` 输出字段。
- Coordinator 生成后的计算阶段。
- 读取、手动编辑及 AI 局部保存中的重新计算调用。
- 页面总额、未知提示、类别小计、估算依据展开区域，以及费用容器样式。

`tools.py` 保留非费用用途的日期夜数计算。Restaurant.price_level、Activity.ticket_price / currency、Hotel.price_per_night / currency 等原始地点属性保留。

TripPlan 的 before validator 只移除历史 `estimated_total_cost` 字段，不修改调用方字典、不放宽其他额外字段校验。旧数据读取时无需改写数据库；后续保存自然不再写入该字段。新生成 JSON 与模型 Schema 不包含费用结果。运行时 Prompt 明确不支持费用总额、区间和预算预测。历史版本说明与用户提供的旧版 Prompt 文件作为记录保留。

## 修改文件

| 文件 | 修改 |
| --- | --- |
| `travelmind/frontend/index.html` | 五区布局、标题语义、地图移至顶部、聊天移至底部 |
| `travelmind/frontend/style.css` | 视口 Grid、滚动容器、横向日期、宽版时间线、低高度适配 |
| `travelmind/frontend/shell.css` | Streamlit 外层高度与滚动控制、悬浮按钮 |
| `travelmind/frontend/app.js` | 新旅程标题、日期排序与切换滚动复位、视口高度通知 |
| `streamlit_app.py` | 删除外层重复旅程标题、所有费用 UI，标记工作区模式 |
| `travelmind/models.py` | 删除费用模型和输出字段，加入历史字段兼容 |
| `travelmind/tools.py` | 删除估算函数，保留日期工具 |
| `travelmind/agent.py` | 删除费用阶段并限制费用输出 |
| `travelmind/storage.py` | 删除读取和编辑中的费用计算 |
| `travelmind/assistant.py` | 意图解析继续拒绝费用预测 |
| `tests/test_tools.py` | 更新页面断言，覆盖历史费用迁移、SQLite 再保存和价格属性保留 |
| `tests/frontend_workspace.test.cjs` | 覆盖卡片、按钮事件与 M1/M2 Marker 联动 |
| `tests/preview_blue.py` | 合成数据静态预览及真实 Streamlit 临时库浏览器验收 |
| `README.md` | 更新当前版本、数据流与持久化说明 |

## 验证结果

- `uv run python -m pytest -q`：14 项通过，覆盖完整生成、三意图局部修改、范围保护、版本并发、手动编辑和历史迁移。
- `node --test --experimental-test-isolation=none tests/frontend_workspace.test.cjs`：3 项通过，包括无坐标不创建 Marker、Marker 点击更新 M2、切换 Day 清除选择、不发送 Agent 请求。
- `node --check` 两份 JavaScript、Python compileall、`git diff --check`：通过。
- 当前真实数据库的 8 个旅程只读加载成功，未改写用户数据。
- Chrome 在真实 Streamlit 页面下验证 1440×1000、1024×768、768×1024、375×812、1440×560、812×375 六种尺寸：无页面横向溢出、正常高度地图不随行程滚动、助手位于视口内、手机日历横向滚动、低高度降级且发送按钮可滚动访问。
- 浏览器验证日期切换、卡片选中及无 Place ID 的 M2 内容更新。截图与布局数据在 `artifacts/v6-host/`。
- 运行时源码搜索不再存在 `estimate_cost`、`CostEstimate`、`CostCategoryEstimate` 或费用容器引用；旧字段名仅用于兼容处理。

浏览器复验：`.venv/Scripts/python.exe tests/preview_blue.py --host`。该脚本仅使用临时数据库和合成行程，地图密钥置空，不读取用户旅程，也不产生模型或 Places 调用。

本项目没有 npm 编译流程；前端直接加载静态 HTML/CSS/JavaScript，因此以语法检查、交互测试和真实浏览器渲染替代 npm build。

## 已知限制

真实 Google Maps/Places 网络请求和真实模型在线调用未在本轮重新验收；地图 SDK 联动使用替身测试，浏览器预览为离线地图。在线获取代码未改动。低高度窗口中顶部地图和底部助手会随文档滚动，这是为保证可访问性采取的明确降级。
