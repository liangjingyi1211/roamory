# TravelMind V7 — 规划与记录

后续增强：记录现已支持图片上传、预览与持久化；日记库直接展示照片，并以标题旁的「编辑」按钮取代原折叠区。点击即可修改文字、添加或移除图片，提供保存和取消。当前完整回归为 30 项 Python、6 项前端测试通过。

## 方案与文件

基于当前 Streamlit + 原生 JavaScript 工作区增量实现，没有重建前端或增加 UI 框架。已读取 UI/UX Pro Max，复用 Premium Blue 主题、系统字体、边框和可见焦点规范；Hero 使用静态 SVG，无新增图片服务或外部素材请求。

| 文件 | 职责与变更 |
| --- | --- |
| `streamlit_app.py` | 首页、行程库、日记库与指定行程的路由；新增独立 journal 事件分支 |
| `travelmind/pages.py` | 新创建页、已有行程列表、日记筛选、浏览和再次编辑 |
| `travelmind/journal.py` | TravelJournalEntry、独立表、关联验证、事务保存与版本冲突 |
| `travelmind/ui.py` | 向组件单独传入当前旅程的 journal_entries，不混入计划数据 |
| `travelmind/frontend/journey-hero.svg` | 沿海城市与旅程停靠点的静态矢量插图 |
| `travelmind/frontend/index.html` | 工作区导航：规划旅程、我的行程、我的旅行日记 |
| `travelmind/frontend/app.js` | 地点记录按钮、编辑区、保存请求、已保存记录重载 |
| `travelmind/frontend/shell.css` | Hero、分栏表单、导航卡片与日记阅读样式 |
| `travelmind/frontend/style.css` | 行程页记录按钮、输入区与导航样式 |
| `tests/test_journal.py` | 新增 9 项 Python 用例，含三类记录的参数化测试 |
| `tests/frontend_workspace.test.cjs` | 日记独立事件和 revision 校验，同时保留地图与编辑测试 |
| `tests/preview_blue.py` | 六组尺寸回归、真实卡片记录保存/编辑/重读、首页和列表截图 |
| `README.md` | 当前入口、功能、验证命令与边界 |

未修改 Activity/Food/Hotel Agent、Coordinator、Places 检索、Timeline、TripPlan 模型和 TripStore 编辑逻辑；未重新引入费用估算或交通规划。

## 初始页与导航

首页上方为旅行插图 Hero：品牌导航、简短主文案、城市海岸 SVG。下方左侧为创建表单，保留目的地、起止日期、兴趣、餐饮偏好、节奏和补充说明；显著的「创建行程」按钮继续调用原 generate_plan，规划中显示 spinner，失败保留输入。

右侧两张完整可点击的卡片分别进入「我的行程」「我的旅行日记」，并展示当前数量。手机下自然堆叠，不制造横向溢出。插图选择器限定为 Hero 的直接 SVG 子元素，避免影响 Streamlit 标题锚点图标。

- 根地址、`?view=new`：首页，不再隐式打开最近旅程。
- `?view=trips`：复用 TripStore.list_trips/get，列出全部保存旅程的目的地、日期和天数；可直接打开。
- `?view=journals`：按更新时间倒序浏览，支持按旅程筛选；显示所属旅程、日期、地点、记录与更新时间、正文、查看行程及编辑入口。
- `?trip=<id>`：原有行程页，刷新继续恢复该旅程。左右布局、原生窗口滚动、M1/M2、聊天与全部编辑保留。

旧「最近旅程」列表统一迁移到行程库，新建悬浮入口仍可回到首页。

## 日记模型与 SQLite

TravelJournalEntry 位于独立 `journal.py`，字段包括：id、trip_id、day_id、item_type、item_id、title、content、images、created_at、updated_at、revision，以及 trip_destination/day_date/place_name 快照。JournalImage 保存图片 id、文件名、实际 MIME、宽高与字节数。

`item_type` 当前支持 activity / restaurant / hotel。快照只记录当前计划中用户选定地点的名称与归属，不生成虚构详情。创建和更新时间为 UTC；正文上限 10000 字符，可以仅有图片，但文字与图片不能同时为空。

日记使用现有 `TRAVELMIND_DB` SQLite 文件中的独立 `travel_journals` 表，`CREATE TABLE/INDEX IF NOT EXISTS` 增量初始化，不改已有 trips 结构或数据。唯一约束 `(trip_id,day_id,item_type,item_id)` 确保每个卡片一条记录。列表按 updated_at 倒序。

图片另存于同库 `journal_images` 表，包含 entry_id、原始图片 BLOB 与缩小的预览 BLOB。前端组件仅接收预览，避免反复传输所有原图；日记库通过 Streamlit 的图片显示能力读取原图。使用 Pillow 验证实际格式、尺寸并制作预览，已作为直接依赖加入 pyproject.toml / uv.lock。只允许 JPG/PNG/WebP 静态图，每条最多 6 张、每张 5MB 且不超过 4000 万像素。文件名不用于文件系统路径。

图片新增、保留与移除和日记正文在同一事务中提交，失败不会留下部分记录或图片。只能保留属于本条记录的图片 ID；旧客户端未提供图片参数时保留原有图片。旧纯文字 JSON 的 images 默认为空列表，无需改写历史数据。

保存使用 `BEGIN IMMEDIATE`：验证所属旅程、日期和该类型地点 ID，再创建或更新日记。已有记录必须携带匹配的 revision，成功后自增，created_at 不变。重复创建或旧版本覆盖会被拒绝；事务失败不留下部分记录。

日记保存不更新 trips.payload、version 或 updated，也不触发 Agent、Places、Timeline 或费用逻辑。正文不传入推荐上下文。历史 TripPlan 无需迁移或重建。

地点被替换或删除后，原记录及地点快照仍保留，可在日记库继续编辑；新地点使用新对象 ID，不继承原记录。首次创建记录时若地点已被删除，则拒绝保存，避免错误归属。

## 卡片记录接入

真实的景点、餐厅、酒店卡片均增加「记录」；没有具体地点的空餐饮槽位仍保留原添加入口，不创建无对象归属的记录。

点击后在卡片下方展开轻量文本编辑区，支持保存与取消；已有记录显示「编辑记录」并预填正文。按钮使用独立点击处理，不触发卡片选择。新事件 `kind=journal` 通过原组件通信传回，由独立 JournalStore 处理；原手动编辑和 assistant 事件不变。

编辑区支持文件多选、图片预览和移除，保存时发送新图片与保留图片 ID。日记库的独立「编辑」按钮直接展开文字、已有图片及新增上传控件，不再使用 expander。点击编辑时冻结原 revision 和记录快照，因此即使提交时页面重读到新版本，也不会覆盖其他窗口的修改；取消会丢弃本次未保存的文字和图片变动。

成功保存后组件收到当前旅程的 journal_entries，更新按钮及内容；若 TripPlan 未改变，则不重建 Marker、不重置地图视野或重新请求 M2，原地图选择保持。失败时编辑区和输入保留。所有用户正文通过文本节点或 HTML 转义展示，不能作为 HTML 执行。

## 测试结果

- `uv run python -m pytest -q`：**30 passed**。覆盖原生成与三意图、手动编辑、历史兼容、日记持久化和归属，以及图片原件/预览持久化、三种格式、大小与数量限制、图片移除/替换、图片归属、旧文本兼容、取消和冻结版本的并发保护。
- `node --test --experimental-test-isolation=none tests/frontend_workspace.test.cjs`：**6 passed**。包括原 M1/M2 选择与独立编辑事件、日记按钮预填、保存日记不刷新地图，以及图片选择、预览移除和仅图片保存事件。
- JavaScript 语法检查、Python compileall、git diff --check：通过。项目直接加载静态资源，无 npm build 步骤。
- Chrome 临时数据库实测六组行程页尺寸：1440×1000、1024×768、768×1024、375×812、1440×560、812×375，包含原生主窗口滚轮、拖动浏览器滚动条到底、地图固定、日期切换和地点编辑。
- 实际从卡片新增记录、再次编辑同一记录，再整页进入日记库，正文正确重读；行程库展示正确。首页 1440px 和 375px 检查通过，无横向溢出，Hero 高度受控。
- 浏览器通过真实文件选择输入上传测试 PNG，验证卡片图片预览、保存与重新打开；日记页图片正确显示，点击「编辑」后修改文字、移除旧图并上传新图，保存后内容与图片一致。新增截图 `journal-with-photo.png`。
- 截图及布局数据位于 `artifacts/v7-host/`，包括 home-1440.png、home-375.png、trip-library.png、journal-library.png。

完整浏览器复验：`.venv/Scripts/python.exe tests/preview_blue.py --host`。只检查首页/列表：追加 `--pages-only`。测试使用临时数据库与合成行程，不写入用户旅程，不产生真实模型或 Places 请求。

## 当前限制

- 单用户本机存储，无账号隔离；部署需保留 SQLite 文件。
- 每张地点卡片一条可编辑图文记录；尚无整条日记删除、图片裁剪、独立日总结或整段旅程总结编辑器。图片仅用于个人记录，不参与 Agent 规划。
- 日记时间明确标为 UTC；列表当前不分页。
- 旧记录保留记录时的地点信息，不自动随地点更名改写。
- 本轮未重新验收真实 Gemini 与 Google Maps/Places 在线调用；这些流程的核心代码未改动，离线浏览器与替身测试不代表线上权限验证。
