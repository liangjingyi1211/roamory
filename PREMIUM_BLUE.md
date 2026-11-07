# TravelMind Premium Blue

本次设计替代 V5 初版的绿色主题，仅调整前端样式与信息展示。Agent、Coordinator、API、地图数据流及业务逻辑保持不变。

## 设计系统

使用已安装的 UI/UX Pro Max，重新读取 Skill 并执行 `professional SaaS minimal blue` 设计系统搜索。根据用户明确要求，采用克制的蓝色、纯色表面与字体层级，未采用搜索输出中的玻璃背景或营销页结构。

唯一主题来源为 `travelmind/frontend/theme.css`。静态组件直接加载该文件，Streamlit 外层读取同一文件并注入样式，不再各自维护颜色副本。

| 角色 | 颜色 |
| --- | --- |
| 背景 / 表面 | `#F7F9FC` / `#FFFFFF` |
| 正文 / 辅助文字 | `#172B4D` / `#5C6D85` |
| 主品牌 / 强交互 | `#183B6B` / `#102E55` |
| 焦点和链接 | `#356DCC` |
| 选中背景 / Hover | `#EAF1FB` / `#F0F5FC` |
| 边框 / 强调边框 | `#E2E8F0` / `#A5BBDA` |

辅助文字在推荐的 `#64748B` 基础上略加深，以提高浅蓝背景上的可读性。餐饮使用灰蓝、住宿使用紫蓝，同时保留文字类别；错误与警告保留各自语义色。所有组件颜色均引用共享变量，包含按钮、输入框、消息、时间线、地图容器和悬浮入口。

字体层级为主标题 32px、区域标题 22–24px、地点名 18–20px、正文 15–16px、地址时间 14px、辅助信息 13px。使用系统字体，无外部字体依赖。

## 组件与密度

- 旅程 Header 将品牌、目的地、日期和费用合并，目的地成为主标题；费用在桌面靠右，小屏自然换行。动态文本继续转义后显示。
- AI 区域移除顶部色带，内边距从 24px 收紧至 16×20px，减少标题、记录和输入框间距；发送按钮保持海军蓝。
- 日期栏默认白底边框，Hover 浅蓝，Selected 海军蓝实底。
- 桌面日期栏为 168px，大屏为 184px；时间线与地图区约 1.2:1，三栏间距 20px。
- 时间线卡片内边距与垂直间距收紧；所有操作按钮仍至少 44px 高，保持独立事件处理。
- M1/M2 使用相同边框、圆角和标题样式；地图与详情间距 16px，保留原有 Marker 和 Places 逻辑。
- 900px 以下改为日期网格、时间线、地图详情依次排列；减少动态效果偏好、可见焦点与新建悬浮按钮均保留。

## 验证

- `uv run python -m pytest -q`：14 项通过，包含保存、编辑和页面刷新验证。页面断言同步检查新 Header 的旅程标题。
- `node --test --experimental-test-isolation=none tests/frontend_workspace.test.cjs`：2 项通过。
- 两份 JavaScript 的 `node --check`、Python 语法与 `git diff --check`：通过。
- 本机 Chrome 无头浏览器在 1440 / 1024 / 768 / 375px 下运行实际静态组件，检查无横向溢出、5 张卡片完整渲染、日期切换、选中卡片和 M2 内容更新。
- 截图及布局数据位于 `artifacts/premium-blue/`，可使用 `.venv/Scripts/python.exe tests/preview_blue.py` 重现。

预览使用合成行程和离线地图，不读取用户旅程，不请求真实 Places。外层页面通过 Streamlit AppTest 回归验证；截图覆盖内嵌工作区。真实 Google Maps/Places 在线联动未在此次浏览器检查中验证。

项目使用原生 HTML/CSS/JavaScript，无 npm 构建步骤，也未增加 UI 框架。
