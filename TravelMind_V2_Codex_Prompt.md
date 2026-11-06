# TravelMind V2 — Codex 开发任务说明

> **任务性质**：在现有 TravelMind 原始版本上进行增量改造，不是重写项目。  
> **原始版本特征**：仍包含 Flight Agent；推荐主要是非实时信息；最终结果以自由文本为主。  
> **目标**：保留原有多 Agent 基本架构，将航班 Agent 替换为美食 Agent；统一产出可编辑的结构化旅行计划，并在前端以**纵向、按日期排列、可展开的每日卡片**展示；生成完成后再展示**整段旅程的预估总费用区间**。

## 0. 首先检查仓库，不要直接大规模重构

1. 阅读现有前后端代码，确认 Agent、Coordinator、Service、Prompt、API、状态管理、页面组件、数据源和测试的实际实现与调用链。
2. 简要列出需要改动的文件以及修改理由；优先复用原有模型调用、Agent 调度和错误处理，不要无必要引入 LangGraph、新数据库或新的大型依赖。
3. **正式接入 Google Maps API，替换现有模拟地点数据：**
   - 项目 `.env` 文件中已配置 `GOOGLE_MAPS_API_KEY`，应通过现有环境变量加载机制读取并使用该密钥，不要要求用户重新配置。
   - 检查当前项目的地点检索实现，识别并逐步替换 Mock 数据、硬编码地点列表及模拟搜索结果。
   - 使用 Google Places API 检索真实的景点、餐厅和酒店信息，并根据实际需求获取名称、地址、地理位置、评分、价格等级、营业时间等可用字段。
   - Agent 应基于 API 返回的真实候选地点进行筛选与规划，而不是依赖 LLM 自行编造地点信息。
   - 对 API 未返回的字段保留 `null`，不要通过 LLM 猜测或填充。
   - 检查 API Key 的实际可用性及所需 API 的启用情况。如果调用失败，应提供明确的错误信息，不要静默回退到 Mock 数据。
   - 避免重复 API 请求，尽量复用已经获取的地点数据，控制不必要的调用成本。
4. 保持现有项目可运行，优先采取最小必要修改。必要时兼容旧接口，避免无故破坏现有功能。
5. 按功能模块逐步实现和验证，不要一次性重写整个项目。除非明确要求，不要自行执行 Git Commit。

## 1. 业务需求与边界

### 1.1 Agent 架构

- 保留现有 Multi-Agent + Coordinator / Orchestrator 主体结构。
- 将 **Flight Agent 替换为 Food Agent**，清理不再使用的航班相关流程、Prompt、接口和 UI（仅清理由本次功能替换确认为废弃的部分，不误删通用能力）。
- V2 包含三个专业 Agent：
  - `Activity Agent`：规划每日景点、建议游览顺序与时段。
  - `Food Agent`：推荐当天早、中、晚餐；参考景点区域和用户餐饮偏好，但**不需要用营业时间进行硬性过滤**。
  - `Hotel Agent`：推荐每日当晚住宿；最后一天允许无住宿。
- `Coordinator` 负责分配、汇总、映射日期与槽位、校验结构、返回统一的 `TripPlan`。不要让三个 Agent 都输出完整 `TripPlan`，以免互相覆盖。
- 推荐的基础协作顺序：输入解析/日期初始化 → Activity Agent → Food Agent 与 Hotel Agent（可并行）→ Coordinator 汇总 → 费用估算 → 返回结构化结果。若现有架构更适合其他小幅调整，以**最小改造与一致性**为优先。

### 1.2 旅行天数

- 用户提供目的地、起止日期和其他非预算偏好。
- 后端使用确定性日期计算：`total_days = (end_date - start_date).days + 1`（含起止日期）。
- 必须校验 `end_date >= start_date`；生成恰好 `total_days` 个 `DailyPlan`，按日期升序排列。
- 不要交给 LLM 计算日期数量；不要假定用户每天都有完整的游玩时段。

### 1.3 餐饮真实性与空槽

- **自动推荐餐厅必须能依据可信地点数据源核实真实存在**；验证应当基于地点来源结果/可追溯标识，不只是相信模型给出的名字。查不到或无法验证时，该餐次保持 `null`，不能编造名称、地址、营业时间或价格。
- **仅要求存在性核实**，不要求营业时间与推荐餐次一致。营业时间用于餐厅详情展示；查不到可以留空。
- 用户手动输入的餐厅或其他项目**无需外部核实、无需自动补全属性**；名称可直接保存，其余可选属性保持 `null` 或空集合。人工输入不应被冒充为已通过地点数据源核实。V2 不需要保留 `verification_status`、`source_type`、`is_user_modified` 三个字段。
- 自动推荐未完成不等于整个规划失败：缺少某一餐厅时，保留对应空槽并继续展示其他内容。

### 1.4 去除预算输入；输出预估总价区间

- **删除原有所有“用户预算”输入与依赖**：包括前端预算控件、请求字段、偏好 Schema、Prompt 限额、预算筛选逻辑、价格上限验证等。不要仅隐藏前端而保留后端强制预算约束。
- 用户不需要提供预算；Agent 不能因缺失预算拒绝规划。
- **在三个 Agent 完成初始推荐后**，为整个旅程给出 `estimated_total_cost` 价格区间，展示在行程概览区，而不是要求用户先填预算。
- 价格区间必须注明：币种、包含哪些成本类别、缺失哪些成本类别、估算依据/假设、是否是完整总价。至少区分餐饮、景点门票、住宿；现阶段已去掉航班 Agent，**不得将航班费用计入默认总价**。可考虑本地交通作为可选估算项，但没有依据时不应捏造。
- **不要把缺失价格当作 0**；不能因为价格缺失就给出貌似精确的“全部费用”。如果现有可信数据不足以形成可支持的总价区间，返回 `low: null, high: null` 和明确的“不足以估算”说明。可以提供部分费用合计，但必须标注为“已知项目小计”，而不是完整旅程总价。
- 费用的数值运算由后端执行，不让 LLM 直接编造总价。如果采用经验性区间/静态参考价，必须明确其估算假设和适用范围；不可以表述为实时报价。
- 当前行程价格可能有不同货币：只有具备可靠汇率及对应时间信息时才进行转换；否则不得跨币种直接加总。
- 生成之后用户修改行程**不调用任何 Agent**。如果需要同步价格展示，只允许用确定性函数基于当前条目价格重新计算，不能为了重估价格重新调用模型或地点检索。

## 2. 标准数据模型（Pydantic）

优先在现有 Schema 基础上调整，尽量沿用仓库已采用的 Pydantic 版本与代码风格。以下是**目标字段契约**，不是要求机械照抄类名或目录。如果现有字段表达了同等语义，可复用并清晰映射。

### 2.1 TripPreferences（无预算）

```python
class TripPreferences(BaseModel):
    interests: list[str] = Field(default_factory=list)
    food_preferences: list[str] = Field(default_factory=list)
    travel_pace: Literal["relaxed", "moderate", "intensive"] = "moderate"
    additional_notes: str | None = None
```

不得出现 `budget`、`budget_level`、`max_budget` 或等价的用户预算输入。

### 2.2 OpeningHours

```python
class OpeningHours(BaseModel):
    day_of_week: int = Field(ge=1, le=7)  # 1=周一, 7=周日
    open_time: time
    close_time: time
```

- `opening_hours: list[OpeningHours] = Field(default_factory=list)`；查不到就用 `[]`。
- 同一天可有多个时段；允许跨午夜；接口不提供时不推断营业时间。

### 2.3 PlaceBase（通用地点属性）

```python
class PlaceBase(BaseModel):
    id: str                   # TravelMind 内部的项目实例 ID，供前端局部编辑
    name: str
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = None
    source_url: str | None = None
```

`id`（本地项目 ID）与外部平台的 `place_id` 是不同概念。用户仅输入名字时，仍由后端生成本地 `id`，其余属性可空。

### 2.4 Restaurant（餐厅）

```python
class Restaurant(PlaceBase):
    place_id: str | None = None
    cuisine: list[str] = Field(default_factory=list)
    price_level: str | None = None
    opening_hours: list[OpeningHours] = Field(default_factory=list)
```

- `price_level` 和 `opening_hours` 查不到可以为空（分别 `None` / `[]`）。
- 不得为了价格估算偷偷向 `Restaurant` 强塞已删除的消费金额字段；若需估算，采用**独立费用估算层**保存有依据的区间与说明，或使用可说明来源的类别级估算，并明确不确定性。

### 2.5 Activity（景点）

```python
class Activity(PlaceBase):
    place_id: str | None = None
    category: str | None = None
    planned_start_time: time | None = None
    planned_end_time: time | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=0)
    ticket_price: float | None = Field(default=None, ge=0)
    currency: str | None = None
    opening_hours: list[OpeningHours] = Field(default_factory=list)
```

- `opening_hours` 查不到就为空数组。
- `planned_*` 是建议游览时间，不等于景点开放时间。
- `activities` 数量可变，也允许 `[]`；不得强制每一天必须 2–4 个景点。

### 2.6 Hotel（酒店）

```python
class Hotel(PlaceBase):
    place_id: str | None = None
    price_per_night: float | None = Field(default=None, ge=0)
    currency: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
```

- `rating` 查不到可以为 `None`；若原有数据源评分刻度不是 0–5，先规范化或调整约束，不能悄悄截断。
- 每个 `DailyPlan.hotel` 表示**当天晚上的住宿**，离开日可为 `null`；连续多晚可以引用相同酒店的地点信息，但每晚仍有自己的槽位。

### 2.7 DailyPlan（单日卡片）

```python
class DailyPlan(BaseModel):
    day_id: str
    day_index: int = Field(ge=1)  # 1-based
    date: date
    title: str = ""

    breakfast: Restaurant | None = None
    lunch: Restaurant | None = None
    dinner: Restaurant | None = None

    activities: list[Activity] = Field(default_factory=list)
    hotel: Hotel | None = None
    notes: list[str] = Field(default_factory=list)
```

- 早餐、午餐、晚餐**字段永远存在**，内容允许 `null`；用户删除只清空值，不删除字段。
- 景点是数组，可逐条添加、编辑、删除和调整顺序。
- 酒店允许为空。

### 2.8 TripPlan（整段旅程）

```python
class TripPlan(BaseModel):
    trip_id: str
    destination: str
    start_date: date
    end_date: date
    total_days: int = Field(ge=1)
    preferences: TripPreferences = Field(default_factory=TripPreferences)
    days: list[DailyPlan] = Field(default_factory=list)
    estimated_total_cost: "CostEstimate | None" = None
```

`days` 是 `list[DailyPlan]`，即每一天卡片的数据项。`TripPlan` 最外层承载整个行程与费用摘要。

### 2.9 独立的费用估算结果

可使用以下模型；字段命名允许按仓库风格调整，但语义与缺失值处理必须完整：

```python
class CostCategoryEstimate(BaseModel):
    category: Literal["food", "activities", "hotel", "local_transport"]
    low: float | None = Field(default=None, ge=0)
    high: float | None = Field(default=None, ge=0)
    currency: str | None = None
    basis: str | None = None     # 来源或估算假设
    coverage_note: str | None = None

class CostEstimate(BaseModel):
    low: float | None = Field(default=None, ge=0)
    high: float | None = Field(default=None, ge=0)
    currency: str | None = None
    is_complete: bool = False
    categories: list[CostCategoryEstimate] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=lambda: ["flights"])
    missing_information: list[str] = Field(default_factory=list)
    note: str | None = None
```

- 总价只能在范围有可靠依据、覆盖范围已界定且币种统一时计算；单项 `low <= high`，总价亦然。
- 餐厅对象删去了单餐消费金额，`price_level` 也不等于可精确相加的价格：如无可靠估算依据，餐饮费用保持未知。
- 允许前端显示“总费用暂无法估算（价格信息不足）”，**不允许硬编码假金额**。
- 若仅计算出已知部分，用类别区间和文案展示“已知项目小计”，不要写成完整总价。

## 3. 前端 UI 与编辑逻辑

### 3.1 页面结构

- 顶部行程概览：目的地、起止日期、天数、**生成完成后**的费用估算摘要/不足提示。
- 下方为**纵向**排列的 `DailyPlanCard`：Day 1、Day 2、...，按日期顺序。
- 每个卡片可展开/折叠。展开后展示：早餐、午餐、晚餐、景点列表、当晚住宿、备注。
- 视觉风格参考苹果/微软的页面设计，时尚简洁优雅。

### 3.2 所有推荐支持手动编辑

- 餐饮：三个固定槽位支持**添加 / 更换 / 删除 / 查看详情**。
- 景点：支持**添加 / 编辑 / 删除 / 排序**；不限制数量。手动输入名称即可，额外属性可空。
- 酒店：支持**添加 / 更换 / 删除 / 查看详情**；手动输入名称即可，额外属性可空。
- 对于餐厅，用户可通过**直接输入名称**完成添加/更换；无需自动搜索或核实，也不自动填充营业时间、位置等属性。已有详细属性的系统推荐可以在详情中按已知字段展示。
- 当餐厅槽位被删除，置为 `null`，但继续显示“尚未安排”和“添加餐厅”按钮。
- 更换时，用户取消编辑必须保留原项目；保存成功之后才替换。
- 编辑只影响目标日期与目标槽位/项目，**绝不能触发任何 Agent、LLM 或自动地点检索**，也不能悄悄重算/覆盖其他日期。
- 展开/折叠状态属于前端 UI，不属于行程生成结果。
- 保存过程提供 loading、错误提示；失败时回滚或保留未提交的编辑值；删除可提供撤销。
- **保存需要持久化**：至少复用现有持久化机制；若没有，设计最小可用保存方案，保证刷新页面后的内容不丢失，并说明其适用边界。不要将“只在 React state 里更新”视为完成。

### 3.3 详情与空值

- 餐厅详情展示：名称、地址、菜系、价格等级、营业时间、描述和来源链接（仅展示有值字段）；`opening_hours=[]` 时显示“暂无营业时间”。
- 景点详情展示已知时段、时长、门票、开放时间等；酒店详情展示已知每晚参考价、评分等。
- 不能把 `null`、`[]` 或数据库缺失误显示为 0 元、0 分、24 小时营业。
- 手动输入只有名称时，详情页正常工作，不出现空属性造成的报错。

## 4. Agent 输出、API 与状态流转

1. 将用户自然语言转成受约束的行程参数；如果日期缺失，沿用已有合理的输入收集或错误提示机制，不虚构日期。
2. 后端初始化 `TripPlan` 和所有空白 `DailyPlan`（早餐/午餐/晚餐是固定字段）。
3. Agent 只负责本职推荐，返回结构化对象/可验证候选；Coordinator 负责按日期填槽。
4. 自动餐厅推荐必须检查存在性；失败则 `null`，不能使整段规划异常退出。不能假定 Food Agent 文字回答本身就是地点核实。
5. 前端通过稳定的 `trip_id`、`day_id`、项目 `id` 实施局部编辑。建议接口：
   - `GET /api/trips/{trip_id}` 获取当前行程；
   - `PUT /api/trips/{trip_id}/days/{day_id}/meals/{meal_type}` 添加或替换单餐（可以只提交名称）；
   - `DELETE /api/trips/{trip_id}/days/{day_id}/meals/{meal_type}` 清空单餐；
   - 类似的景点单项 CRUD/排序接口与酒店替换/清空接口。
6. 如现有 API 路由风格不同，遵循现有风格实现等效能力，避免重复建设。对非法 `meal_type`、不存在的行程/日期、空名称等返回明确错误。
7. 费用估算在首次完整规划后生成；用户修改后仅允许通过后端**确定性计算**更新费用摘要。缺少可靠金额时，应更新“不完整/无法估算”状态，而非静默保留过期且看似准确的金额。

## 5. 测试与验收要求

以核心功能正常运行为目标，避免过度测试和冗余代码。

### 核心验收

- Food Agent 成功替换 Flight Agent，完整行程生成流程正常。
- 不再要求输入预算；跨日期行程天数及日期正确。
- 每天保留早餐、午餐、晚餐三个槽位（允许为空）；景点数量不再强制限制。
- Food Agent 无可靠推荐时保留空槽，不影响行程生成。
- 用户可以添加、编辑、删除餐厅、景点和酒店；操作不触发 Agent，刷新后数据仍保留。
- 手动添加的内容无需核实，仅填写名称即可保存；缺失字段正常展示。
- 行程生成完成后展示费用区间；缺失价格不按 0 计算，不混加不同货币，不包含机票。

### 测试执行原则

1. **优先复用现有测试**，仅对本次修改涉及的核心功能进行必要验证，不追求全面测试覆盖率。
2. 不要为每个功能或边界条件创建独立测试文件，避免大量测试脚本和复杂测试框架。
3. 如有必要，可以创建少量临时测试脚本，**测试通过后删除所有本次新增的临时测试文件**，不保留一次性测试代码。
4. 保留项目原有测试文件，不得随意删除或破坏现有测试体系。
5. 最后执行必要的后端测试及前端构建检查，确保本次修改未引入明显回归。
6. 完成后简要汇报测试结果、已验证的核心功能和仍存在的问题，无需生成冗长的测试报告。

## 6. 交付要求

完成后请输出：

1. 实际修改的文件与主要变化（按后端模型、Agent/Coordinator、API/存储、前端组件、测试划分）。
2. 新的主要数据流与 Agent 调用顺序。
3. 关键数据对象 JSON 示例，含：自动推荐、餐厅空槽、用户手动输入只有名称、费用信息不足的情况。
4. 已运行的测试/构建命令及结果；未运行的说明原因。
5. 尚需配置的地点数据源、API key 或第三方服务；不可声称未配置的服务已经实现真实性核实。

**原则：实现可运行的 V2，而不是只交付概念设计；拒绝编造真实地点和价格数据；仅执行必要重构，不为了架构“先进”而增加不必要的复杂度。**
