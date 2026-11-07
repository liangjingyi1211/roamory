# 地图与行程布局

## 当前：左侧地图，右侧行程

按最新要求，桌面工作区为约 45:55 的两栏 Grid：左侧上 M1、下 M2，共同容器 `position:sticky;top:0;height:100dvh`；右侧依次为日历、当天行程和 AI 助手，继续使用浏览器主窗口自然滚动。地图仍无模块标题、边框、圆角与阴影。低于 900px 时恢复顶部并排地图，以避免行程内容过窄。

侧栏模式的滚动定位留白改为 16px，不再为整列地图高度预留顶部空间。清理新建悬浮入口的页尾占位，确保滚到底时侧栏也不偏移；M2 底部保留可滚动留白，避免入口遮挡详情操作。

修改 `style.css`、`shell.css`、`surface.js`、`app.js` 及浏览器测试。3 项交互测试与六组真实浏览器尺寸检查通过，包含主窗口滚轮、原生滚动条拖到底、地图坐标不变、日期切换和实际编辑后重绘。最新截图及数据位于 `artifacts/left-maps-host/`。浏览器测试使用临时合成行程和离线地图；未重新验证 Google 在线服务。

## 上一版顶部双地图方案（历史记录）

本次仅修改前端布局、滚动呈现及相应测试，Premium Blue 主题变量、后端逻辑和事件数据契约不变。已重新查阅 UI/UX Pro Max，重点检查 sticky 遮挡、焦点与响应式滚动。

## 当前结构

工作区从上到下为：全宽 M1/M2 → 横向日历 → 每日 Timeline → AI 助手。

- Streamlit 工作区取消 `max-width` 和左右 padding。新建表单与历史旅程页面继续使用原有宽度样式。
- M1/M2 约 55:45 并排，小屏 1:1；去掉标题栏、图例、卡片边框、圆角与阴影，保留语义标签、Marker 和详情获取所需 DOM 节点。
- 地图区使用 `position:sticky; top:0`，占据正常文档流，不使用 fixed 或额外占位块。桌面高度为 `clamp(260px,44dvh,560px)`，手机约 40dvh，低高度窗口约 44dvh。
- 移除独立旅行标题和日期区，地图下直接进入紧凑日历与时间线。主体两侧仅保留内容阅读与操作所需间距，没有最大宽度限制。
- Timeline 取消 `overflow-y:auto`，与助手共享主文档自然滚动。M2 和展开的聊天记录仅在内容过长时内部滚动。

现有架构是 Streamlit 静态组件 iframe。已修复此前错误：不再锁定外层高度或让 iframe 承担页面滚动。`surface.js` 把现有工作区 DOM 呈现到同源主文档中，组件 iframe 缩至零高度，仅保留原有消息通信；样式限定在工作区内，Maps SDK 后加入的样式同步到主文档。**原生顶层 `window/document` 是主滚动容器，M1/M2 的共同容器也位于此文档中，使用 CSS sticky 吸顶。** 不使用 fixed 地图或手动占位。

切换日期与 Marker 选择时，滚动原生主窗口，并为 sticky 地图区留出可见空间。取消外层 `100dvh` 固定高度与 `overflow:hidden`；工作区页面隐藏 Streamlit 自身工具栏，让共同地图容器位于浏览器视口 `top:0`。地图查询、编辑、持久化及三意图 AI 修改逻辑均未改动。

## 文件

- `travelmind/frontend/index.html`：移除地图标题、图例、独立旅程概览和多余顶部标识。
- `travelmind/frontend/style.css`：Sticky 全宽地图、单文档滚动、较大地图高度、紧凑日期和宽版卡片。
- `travelmind/frontend/shell.css`：取消工作区最大宽度、固定高度、overflow 锁定与框架内部主滚动。
- `travelmind/frontend/surface.js`：现有 DOM 同源呈现与样式范围隔离；保留原 iframe 消息桥接，处理卸载清理与 SDK 样式。
- `travelmind/frontend/app.js`、`place-details.js`：使用呈现区域的 DOM 与主窗口进行渲染和滚动，不修改数据契约或 Google 数据读取。
- `tests/frontend_workspace.test.cjs`、`tests/preview_blue.py`：更新滚动验证。
- `README.md`、`V6_IMPLEMENTATION.md`：标记当前布局与历史方案的区别。

## 验证

- 现有 Python 回归测试：14 项通过。
- 前端交互测试：3 项通过，覆盖独立编辑事件、Marker/M2 选择、无坐标地点、日期切换。
- Chrome 真实 Streamlit 页面：1440×1000、1024×768、768×1024、375×812、1440×560、812×375 六种尺寸。实际发送鼠标滚轮输入后，顶层 `window.scrollY` 均增加 400px；拖动原生浏览器滚动条均到达 `scrollHeight-innerHeight`，发送按钮可见。日历和时间线向上移动，M1/M2 两个面板的 top 均保持 0。
- 真实手动编辑经过原 iframe 通信保存，并触发 Streamlit 重新渲染；新名称正确出现，M2 旧选择清除，工作区没有重复挂载。
- JavaScript 语法检查与 `git diff --check` 通过。本项目直接加载静态资源，没有 npm 构建步骤。

浏览器截图与尺寸数据位于 `artifacts/window-scroll-host/`。复验命令：`.venv/Scripts/python.exe tests/preview_blue.py --host`。使用临时合成行程、离线地图，不访问用户数据库，不产生真实模型或 Google Places 请求；真实在线地图服务未重新验收。同源静态组件为当前项目的部署方式；未将跨域组件部署列为支持目标。
