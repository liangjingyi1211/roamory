# TravelMind V5 — UI/UX redesign

> 后续 Premium Blue 改造已替代本文的初版绿色配色与相关尺寸。当前主题、布局和最新验证记录见 `PREMIUM_BLUE.md`。

## 范围与审查

依据 `TravelMind_V5_Codex_Prompt.md`，仅调整前端展示及交互。实际技术栈为 Streamlit + 原生 JavaScript 静态组件，没有 React、npm 构建流程，继续复用现有技术。

原页面主要问题：大量正文为 10–12px；浅灰文字对比度偏低；日期、地点选中状态偏弱；时间线重复显示地点详情；按钮点击区域较小。原有状态通过组件内 selectedDay / activeId / selectedPlaceRef、Streamlit acknowledgment 与服务端持久化管理，继续保留。

## 设计系统

已读取并使用 `.agents/skills/ui-ux-pro-max/SKILL.md`，执行设计系统搜索与 `minimalism functional` 样式搜索。自动输出中的极光渐变与滚动叙事不适合本次工具型页面，因此采用 Minimalism 的网格、留白、清晰层级和无障碍原则。

| 项目 | 规范 |
| --- | --- |
| 品牌 | 深海青 `#16665f`，深色交互 `#104e49`，选中辅助底色 `#e8f3f0` |
| 基底 | 暖白 `#f8f9f6`、纯白卡片、正文 `#203633`、辅助文字 `#586b67` |
| 分类 | 景点深海青、餐饮陶土 `#9a552f`、住宿紫灰 `#73618e`；同时保留文字类别 |
| 字体 | 系统无衬线字体与 Microsoft YaHei，不增加外部字体请求 |
| 层级 | 主标题 34px / 移动端 32px，区域标题 22–24px，地点名 20px / 小屏 18px，正文 15–16px，时间地址 14px，辅助标签 12–13px |
| 间距 | 4 / 8 / 12 / 16 / 24 / 32px，卡片圆角 16px，细边框和轻阴影 |
| 状态 | 日期采用品牌色实底；地点采用双层选中边框；按钮独立 hover；键盘 3px 焦点环；遵循 reduced-motion |
| 触控 | 主要按钮最小高度 44px，操作按钮间距 8px |
| 布局 | 日期栏 184px，大屏 208px；其余时间线与地图详情约 1.12:1；1100px 收紧间距；900px 以下日期网格、时间线、地图详情依次排列 |

## 修改文件

- `streamlit_app.py`：统一外层页面、新建旅程表单、说明文字和新建入口样式。
- `travelmind/frontend/style.css`：重新组织整体视觉语言、设计变量、三栏布局、卡片、聊天、地图详情和响应式样式。
- `travelmind/frontend/index.html`：加入跳转时间线链接与焦点目标、日期区域中文标识。
- `travelmind/frontend/app.js`：删除第二栏地点详情函数与渲染入口；按钮阻止冒泡；标题按钮暴露选中状态；上移下移采用文字；地图选中滚动遵循减少动态效果设置。
- `tests/frontend_workspace.test.cjs`：验证真实前端脚本的卡片选择与独立编辑事件，不依赖第三方包。

## 保留的功能

日期选择、每天时间线、卡片及 Marker 选择、M1/M2 联动、真实 Google Places 获取、无 Place ID 的本地字段回退、聊天局部修改、手动增删替换地点、编辑时间、景点排序、费用估算、旅程历史和新建入口均保留。未修改 Agent、Coordinator、地点检索、Timeline 生成、后端 API 或数据库契约，未增加交通规划。

## 验证

- `uv run python -m pytest -q`：14 项通过。
- `node --test tests/frontend_workspace.test.cjs`：2 项通过，覆盖无重复详情、无坐标地点选中更新 M2、替换与时间表单、删除事件契约及按钮阻止冒泡。
- `node --check travelmind/frontend/app.js` 与 `node --check travelmind/frontend/place-details.js`：通过。
- `git diff --check`：通过。

这是静态前端，无需 npm 构建。直接运行 `uv run pytest` 在当前环境会出现模块导入路径问题，使用上述 Python 模块入口可正常完成全部现有测试。

自动前端测试使用轻量 DOM 适配器，不等同于真实浏览器端到端验证。尚未完成浏览器截图与真实 Google Maps/Places 网络验收；需在配置有效浏览器密钥的运行环境中检查 375 / 768 / 1024 / 1440px 下的布局、Marker 点击和官方详情加载。
