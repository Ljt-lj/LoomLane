# LoomLane V0.1 技术方案与落地记录

- 状态：V0.1 已落地，本文档作为当前实现基线与后续演进依据
- 目标版本：V0.1
- 输入材料：`LL.json`
- 更新时间：2026-11-18

## 1. 方案摘要

LoomLane 是一个本地优先的多 Agent 流水线可视化编排与编译系统。它的核心闭环是：

```text
可视化画布
  -> 生成并校验 workflow.sdl.yaml
  -> 编译为独立的 Python 工作流
  -> 子进程执行工作流
  -> 通过事件流回传执行状态
  -> 画布高亮节点和数据流
```

本方案把 SDL 定为唯一的业务语义真源。画布布局只保存界面状态，代码生成器只读取通过校验的 SDL，不直接读取 React Flow 数据。这样可以把编辑体验、编译规则和执行运行时解耦，并允许用户在 Git 中审查、比较和复用工作流。

从现有材料看，项目要实现的是完整的 LoomLane 产品，而不是一个单独的 Codex `SKILL.md` 技能包。下文按产品工程方案设计；如果最终还需要提供 Codex Skill，应另做一层调用和交付封装，不与本方案的应用程序架构混在一起。

### 1.1 V0.1 已实现能力

| 模块 | 当前实现 |
| --- | --- |
| 项目与画布 | 创建、打开、保存、导入/导出 SDL，React Flow 画布与 SDL/canvas 分离持久化 |
| Agent 与边 | Agent、端口、`PIPE`、`CONDITIONAL`、`FEEDBACK` 编辑，Agent 和边选中删除 |
| 条件 | 受限条件 AST，前端支持 `AND`、`OR`、`NOT`、多谓词添加/删除/嵌套和高级 JSON 编辑 |
| 检查器 | 右侧检查器可折叠，展开时可拖拽或键盘调整宽度，状态持久化到浏览器 |
| API Profile | 增删改、密钥状态查询、密钥写入/删除；删除被引用 Profile 时自动迁移或阻止 |
| 模型 JSON 输出 | 每个 Agent 可独立启用层次化 JSON 模板，并通过 `response_format={"type":"json_object"}` 请求 JSON |
| 编译 | 从规范化 SDL 确定性生成独立 Python 项目 |
| 运行 | 子进程执行、条件分支、有界 Feedback、Token 统计、取消和失败记录 |
| 事件 | NDJSON 落盘并通过 SSE 回传节点、边、用量和错误事件 |
| 密钥 | OS 凭据库优先，Windows DPAPI 加密回退，按项目与 Profile 隔离并绑定 origin |
| 测试 | 前端 Vitest、后端 pytest，覆盖校验、运行、密钥和关键编辑工具 |

### 1.2 阅读约定

- “已实现”表示当前仓库中存在对应代码和行为。
- “约束”表示 V0.1 的明确边界，不应被理解为前端 Bug。
- “后续预留”表示尚未纳入 V0.1 交付范围的能力。
- 示例中的 API Key 一律使用占位符，不得把真实密钥写入 SDL、Markdown、Git 或日志。

## 2. 目标与范围

### 2.1 V0.1 已实现范围

1. 支持创建、保存、导入和导出工作流。
2. 支持 Agent 角色、API Profile、全局背景 Prompt 的配置。
3. 支持 `PIPE`、`CONDITIONAL`、`FEEDBACK` 三种边。
4. 支持输入输出端口类型检查，以及反馈回路的边界检查。
5. 从 SDL 生成可独立运行的 Python 项目。
6. 通过 OpenAI 兼容接口执行 Agent。
7. 将运行事件以流式方式反馈到前端，并持久化为 `events.jsonl`。
8. 在本地项目目录中保存画布布局、SDL、生成物和运行记录。

### 2.2 v0.1 非目标

- 不做多租户、云端协作、权限系统。
- 不支持任意 Python、JavaScript 或 Jinja2 表达式执行。
- 不支持 `LOOP`、`FAN_OUT`、`AGGREGATE`、`HUMAN_GATE` 正式执行语义。
- 不做并行节点调度；v0.1 采用确定性的串行就绪队列。
- 不做 token 级模型流式输出，只回传节点、边、用量和错误等运行事件。
- 不做多 provider 完整适配；v0.1 只实现 OpenAI 兼容接口。
- 不实现 Agent 内 Python 精细规则。
- `FILE` 端口在 v0.1 只传递工作区内的文件引用，不自动把模型文本转换为文件产物。
- `max_tokens` 在 v0.1 记录并透传给模型请求，但累计超限中断放到 v0.2。

## 3. 关键设计决策

| 决策 | 采用方案 | 原因 |
| --- | --- | --- |
| 语义真源 | `workflow.sdl.yaml` | 可读、可 diff、可版本控制，代码生成器只依赖它 |
| 画布真源 | `canvas.json` 只存布局 | 避免节点配置和 SDL 各存一份后产生冲突 |
| API Profile | 非密钥元数据写入 SDL；密钥单独存入系统凭据库 | 项目可移植，且密钥不进入 Git 或 SDL |
| 条件表达式 | 自定义受限 AST | 比字符串 eval 安全，比第一版直接支持 Python 可控 |
| Feedback | 只允许回注到上游节点，必须有遍历上限 | 保留核心语义，同时避免不可控的任意环 |
| 调度模型 | 显式就绪队列和运行状态机 | 比拓扑排序更适合条件分支和有界反馈 |
| 执行隔离 | 生成项目 + 独立 Python 子进程 | 崩溃、取消和日志边界清晰 |
| 事件协议 | NDJSON 写入 stdout，由服务端转 SSE | 协议简单，方便实时消费和落盘 |
| 持久化 | 文件系统 + JSON/YAML，不引入业务数据库 | v0.1 本地单用户场景足够，项目天然可迁移 |

与初始 JSON 方案相比，V0.1 落地时已经收敛以下细节：

1. 不再维护独立的 `api_profiles.local.yaml`。SDL 保存 profile 的非敏感配置，系统凭据库保存密钥。
2. 不再同时使用 `from_port`、`to_port` 和 `data_mapping` 描述同一个映射。统一使用 `mappings` 列表。
3. 不把 Jinja2 直接用作条件执行引擎。v0.1 使用可枚举、可校验的条件 AST。
4. 生成项目不依赖 LoomLane Python 包。调度器和 Agent 调用代码由模板生成，第三方运行依赖只保留 `httpx`。
5. v0.1 不宣称支持任意图。忽略 `FEEDBACK` 边后，工作流必须是 DAG。

## 4. 总体架构

```text
┌──────────────────────────────┐
│ React + React Flow 编辑器     │
│ 画布 / 配置面板 / SDL 预览     │
└──────────────┬───────────────┘
               │ HTTP + SSE
┌──────────────▼───────────────┐
│ FastAPI 服务                  │
│ 项目 API / 校验 / 编译 / 运行  │
└───────┬───────────┬──────────┘
        │           │
        │           └──────────────┐
┌───────▼────────┐   ┌─────────────▼─────────────┐
│ SDL 校验与编译  │   │ 运行管理器                 │
│ Pydantic + Jinja│   │ 子进程 / 事件收集 / 取消    │
└───────┬────────┘   └─────────────┬─────────────┘
        │                          │
┌───────▼────────┐   ┌─────────────▼─────────────┐
│ generated/     │   │ runs/{run_id}/            │
│ 独立 Python 项目│   │ events.jsonl / result.json │
└────────────────┘   └───────────────────────────┘
```

### 4.1 技术栈

- 前端：React、TypeScript、Vite、`@xyflow/react`、Zustand、TanStack Query。
- 后端：Python 3.12、FastAPI、Pydantic v2、PyYAML、Jinja2、httpx。
- 工具链：`pnpm` 管理前端依赖，`uv` 管理 Python 环境。
- 测试：pytest、Vitest、Testing Library、Playwright。

选 React Flow 是因为它适合节点编辑器，但业务状态不能直接依赖 React Flow 的节点对象。前端应维护独立的 `WorkflowDraft`，React Flow 只负责交互和布局。

### 4.2 建议仓库结构

```text
LoomLane/
├─ apps/
│  └─ web/
│     ├─ src/
│     │  ├─ components/
│     │  ├─ features/workflow/
│     │  ├─ features/run/
│     │  └─ lib/
│     └─ package.json
├─ backend/
│  ├─ src/loomlane/
│  │  ├─ api/
│  │  ├─ compiler/
│  │  ├─ domain/
│  │  ├─ runtime/
│  │  ├─ sdl/
│  │  └─ storage/
│  ├─ templates/generated/
│  ├─ tests/
│  └─ pyproject.toml
├─ examples/
│  └─ content-review/
│     ├─ canvas.json
│     └─ workflow.sdl.yaml
├─ docs/
└─ README.md
```

## 5. 本地项目与持久化

### 5.1 项目目录

用户选择一个工作区目录，服务端在其中创建项目：

```text
projects/{project_id}/
├─ project.json
├─ canvas.json
├─ workflow.sdl.yaml
├─ generated/
│  └─ {compiler_key}/
└─ runs/
   └─ {run_id}/
      ├─ meta.json
      ├─ input.json
      ├─ events.jsonl
      ├─ result.json
      └─ workspace/
```

`project.json` 保存项目名称、创建时间、SDL revision 和最近一次成功编译的哈希。`canvas.json` 只保存视口、节点位置、尺寸和边的折点；节点名称、Prompt、端口和连线语义全部从 SDL 读取。

### 5.2 写入策略

- 项目文件必须先写临时文件，再原子替换。
- 服务端对每个项目加异步写锁，防止同一进程内并发写入。
- `PUT` 更新携带 `base_revision`；revision 不一致时返回 `409 Conflict`。
- SDL 保存前必须先通过结构校验和语义校验。
- 用户对项目目录拥有最终所有权，项目目录可以整体打包或纳入 Git。

### 5.3 密钥存储

- API Key 优先进入操作系统凭据库，例如 Windows Credential Manager、macOS Keychain 或 Linux Secret Service。
- Windows 凭据库不可用时，回退到 `workspace/credentials.dpapi`。该文件使用当前 Windows 用户的 DPAPI 加密，不保存明文密钥，也不进入 Git；其他平台不回退到明文文件。
- DPAPI 回退文件采用“读取现有记录 -> 修改目标记录 -> 临时文件写入 -> `os.replace` 原子替换”的流程，并尽最大努力将文件权限设为 `0600`。写入中断不会留下半个可读取的凭据文件。
- 凭据账户名由 `project_id/profile_id` 共同构成，因此复制 SDL 到另一个项目不会自动复用旧项目密钥。
- 凭据记录同时保存规范化后的 `base_url` origin 绑定信息。origin 改变时，旧记录会被视为未配置，用户必须重新输入 Key。
- 浏览器只在创建或更新 Profile 时提交一次密钥，之后只显示“已配置/未配置”状态，不读取明文密钥。
- 删除密钥时会同时尝试删除系统凭据库记录和本地 DPAPI 回退记录，避免旧值在后续读取时重新生效。
- 生成项目运行时通过 `LOOMLANE_SECRET_<PROFILE_ID>` 环境变量接收密钥；密钥不进入命令行参数。
- 开发环境允许通过环境变量注入密钥，但不提供明文文件回退方案。

### 5.4 API Profile 生命周期

- Profile 的名称、`base_url`、模型、超时、重试和能力标记由当前项目的 SDL 管理。
- 服务端按项目提供密钥写入、更新、删除和状态查询，不再保存第二份 Profile 元数据。
- 修改模型名不会影响已有密钥；修改 `base_url` 的 origin 会使旧密钥失效。
- Profile 删除由前端统一处理引用关系：
  - 若仍存在其他 Profile，被删除 Profile 引用的 Agent 自动切换到剩余列表中的第一个 Profile。
  - 若被删除 Profile 仍有 Agent 引用且它是唯一 Profile，则阻止删除，并提示先新增替代 Profile。
  - 若被引用 Profile 已有密钥，删除操作同时删除密钥。
  - 删除只修改前端草稿；用户保存项目后，SDL 中的 Profile 变更才正式持久化。
- 导入其他项目时不自动复用原项目密钥；即使 `profile_id` 相同，也要求用户在确认 `base_url` 后重新补充凭据。
- Profile 配置和密钥分两步保存：先保存项目使 SDL 中的 `base_url` 生效，再在 API 检查器中保存密钥。

## 6. SDL 0.1 设计

SDL 使用 YAML 保存，解析时必须使用 `safe_load`，并限制文件大小、锚点展开和嵌套深度。

### 6.1 示例

```yaml
sdl_version: "0.1"

workflow:
  id: content-review
  name: 内容审查流水线
  global_context:
    text: You are part of a content production system.
    injection: prefix
    applies_to: all
  inputs:
    - name: requirement
      type: TEXT
      required: true
  outputs:
    - name: approved_draft
      agent: drafter
      port: draft
  execution:
    feedback_limit_default: 3
    max_steps: 200

start:
  - drafter

api_profiles:
  - id: openai-main
    provider: openai_compatible
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    request:
      timeout_seconds: 60
      max_retries: 2
      temperature: 0.2
    capabilities:
      function_calling: false
      structured_output: true

agents:
  - id: drafter
    name: 内容起草
    api_profile: openai-main
    system_prompt: 根据需求撰写初稿，并根据审查反馈修订。
    inputs:
      - name: requirement
        type: TEXT
        required: true
      - name: feedback
        type: TEXT
        required: false
    outputs:
      - name: draft
        type: TEXT
      - name: outline
        type: DATA
    limits:
      max_tokens: null
    json_output:
      enabled: true
      template:
        draft: 完整的 Markdown 草稿文本
        outline:
          sections: 章节标题字符串数组
    output_dir: null

  - id: reviewer
    name: 质量审查
    api_profile: openai-main
    system_prompt: 审查初稿并给出评分。
    inputs:
      - name: draft
        type: TEXT
        required: true
    outputs:
      - name: review
        type: TEXT
      - name: score
        type: DATA
        schema_hint:
          type: number
          minimum: 0
          maximum: 1
    json_output:
      enabled: true
      template:
        review: 具体、可执行的审查意见
        score: 0 到 1 之间的数字

edges:
  - id: draft-to-review
    from: drafter
    to: reviewer
    type: PIPE
    mappings:
      - from: draft
        to: draft

  - id: review-feedback
    from: reviewer
    to: drafter
    type: FEEDBACK
    mappings:
      - from: review
        to: feedback
    condition:
      op: lt
      left: $.agents.reviewer.outputs.score
      right: 0.8
    guard:
      max_traversals: 3
```

### 6.2 标识符约束

- `workflow.id`、`agent.id`、`edge.id`、`api_profile.id` 使用 `^[a-z][a-z0-9_-]{0,63}$`。
- 端口名使用 `^[a-z][a-z0-9_]{0,63}$`。
- 同一工作流内的 Agent ID 必须唯一。
- 同一 Agent 内的输入端口名、输出端口名分别唯一。
- 所有引用必须解析到存在的对象，禁止隐式创建。

### 6.3 端口类型

| 类型 | 值约束 | 说明 |
| --- | --- | --- |
| `TEXT` | 字符串 | 自然语言、Markdown、纯文本 |
| `DATA` | 任意合法 JSON 值 | 结构化结果 |
| `FILE` | `{ "path": "...", "media_type": "..." }` | 路径必须位于本次运行工作区内 |
| `MIXED` | 不限 | 兼容型，用于渐进式建模；目标端口仍按自身类型校验 |

边连接规则：

- 相同类型可以直接连接。
- `MIXED` 可以与任意类型连接。
- 其他跨类型连接默认报错，不做隐式字符串化。
- `schema_hint` 在 v0.1 可保存任意 JSON Schema 子集；运行时可用于附加校验，不替代端口类型。

### 6.4 条件表达式

v0.1 不支持原始字符串表达式。条件使用 AST：

```json
{
  "op": "and",
  "args": [
    { "op": "lt", "left": "$.agents.reviewer.outputs.score", "right": 0.8 },
    { "op": "exists", "left": "$.agents.reviewer.outputs.review" }
  ]
}
```

允许的操作：

- 逻辑：`and`、`or`、`not`
- 比较：`eq`、`ne`、`lt`、`lte`、`gt`、`gte`
- 集合：`in`、`contains`
- 存在性：`exists`

允许的引用根：

- `$.workflow.inputs`
- `$.agents.{agent_id}.outputs`
- `$.edges.{edge_id}.traversals`
- `$.run.step`

禁止函数调用、属性写入、任意索引表达式和动态求值。前端条件编辑器支持添加、删除和嵌套多个谓词，并通过 `AND`/`OR` 条件组组合；高级 JSON 编辑仅作为复杂条件的补充入口。

### 6.5 Agent JSON 输出配置

V0.1 为每个 Agent 提供独立的 JSON 输出配置：

```yaml
json_output:
  enabled: true
  template:
    draft: 完整的 Markdown 草稿文本
    outline:
      sections: 章节标题字符串数组
```

`template` 是模型回复应遵循的 JSON 形状，不是 JSON Schema：

- 最外层键必须与 Agent 的输出端口一一对应。缺少端口、增加未知最外层键都会导致节点失败。
- 嵌套对象允许使用任意业务字段名，当前的 `outline.sections` 表示“`outline` 对象中的 `sections` 字段”，不是名为 `outline.sections` 的单层键。
- 字符串叶值用于描述模型应返回的内容或类型，模型应把说明替换为实际值。
- JSON 解析成功后，运行时仍按输出端口执行 `TEXT`、`DATA`、`FILE`、`MIXED` 类型检查。
- `schema_hint` 会在端口类型检查后继续校验，例如 `score` 的 `type/minimum/maximum`。

前端填写方式：

1. 在 Agent 检查器中启用“JSON 输出”。
2. 添加顶层字段 `draft`，填写叶值说明。
3. 添加顶层字段 `outline`，点击该字段右侧“添加子字段”。
4. 在 `outline` 下添加 `sections`，填写数组/字符串等内容说明。
5. 展开“JSON 预览”核对最终结构后保存项目。

内容审查示例的期望回复：

```json
{
  "draft": "# ...\n\n...",
  "outline": {
    "sections": ["背景", "分析", "结论"]
  }
}
```

启用后，运行时向 OpenAI 兼容接口发送：

```json
{
  "response_format": {
    "type": "json_object"
  }
}
```

同时把模板注入 System Prompt，要求模型保留所有键和嵌套层。`capabilities.structured_output` 是兼容旧配置的第二个触发条件；只要二者任一为真，请求都会携带 `response_format`。因此目标服务必须支持该参数。

模型回复先尝试整体 `json.loads`。若对象前后带有说明文字，提取第一个 `{` 并使用 `JSONDecoder.raw_decode` 解析第一个完整对象。该机制只容忍对象后的合法尾随文本，不会补全截断 JSON，也不会猜测缺失字段或修正键名。

### 6.6 API Profile 与 DeepSeek 配置示例

DeepSeek 通过 OpenAI 兼容接口接入时，Profile 可写为：

```yaml
api_profiles:
  - id: deepseek-flash
    name: DeepSeek Flash
    provider: openai_compatible
    base_url: https://api.deepseek.com/v1
    model: deepseek-flash
    request:
      timeout_seconds: 60
      max_retries: 2
      temperature: 0.2
    capabilities:
      function_calling: false
      vision: false
      long_context: false
      structured_output: true
```

注意：

- `model` 必须填写服务端实际接受的模型标识。若供应商或网关不使用 `deepseek-flash` 这个名字，应替换为对应模型 ID。
- `base_url` 应填写到 API 版本根路径，不要重复追加 `/chat/completions`；运行时会在末尾拼接该路径。
- `structured_output: true` 仅在服务接受 `response_format={"type":"json_object"}` 时启用。
- 自建网关、代理或本地兼容服务可以替换 `base_url`，但密钥会绑定到该 URL 的 scheme、host 和 port。

API Key 不属于 SDL 字段。配置步骤是：

1. 保存包含新 `base_url`、模型和 Profile ID 的项目。
2. 打开右侧检查器的 `API` 页。
3. 为该 Profile 输入 Key 并点击保存密钥。
4. 看到“已配置”后再运行。修改 origin 后状态会回到“未配置”，这是密钥安全绑定生效，而不是保存失败。

## 7. SDL 校验与编译

### 7.1 校验分两层

1. 结构校验：由 Pydantic 模型完成，检查字段、类型、枚举和版本。
2. 语义校验：检查引用、端口、图结构和安全边界。

语义校验至少覆盖：

- 所有 ID 和端口名合法且唯一。
- `start` 中的 Agent 存在。
- 每条边引用的 Agent 和端口存在。
- 边的映射源端口属于上游输出，目标端口属于下游输入。
- 条件只出现在 `CONDITIONAL` 和 `FEEDBACK` 边上。
- 条件 AST 的操作符和引用路径在允许范围内。
- 忽略 `FEEDBACK` 边后不存在普通环。
- 每条 `FEEDBACK` 边的目标必须是其源节点的上游祖先。
- 每条 `FEEDBACK` 边必须有 `max_traversals`，或在 workflow 默认值存在时可继承。
- 所有节点均可从 `start` 到达；不可达节点作为错误处理。
- 工作流声明的输出引用存在。
- SDL 中不能出现 `api_key`、`secret`、`password` 等敏感字段。

### 7.2 编译产物

编译输入只能是规范化后的 SDL：

```text
generated/{compiler_key}/
├─ workflow.py
├─ config.py
├─ manifest.json
├─ requirements.txt
├─ agents/
│  ├─ drafter.py
│  └─ reviewer.py
└─ runners/
   └─ graph_runner.py
```

`compiler_key` 由 SDL 规范 JSON 的 SHA-256 和编译器版本共同生成。SDL 或编译器版本改变时自动重新生成。

生成代码约束：

- 不硬编码 API Key。
- 不生成任意代码执行入口。
- Prompt、端口和条件都通过 JSON 安全序列化写入配置。
- `graph_runner.py` 来自受控模板，不允许用户文本直接拼接到 Python 源码中。
- `manifest.json` 记录 SDL 哈希、编译器版本和生成文件列表，不写入运行时间等非确定性字段。
- 生成项目只依赖 Python 标准库和 `httpx`。

## 8. 运行时设计

### 8.1 运行状态

```text
RunState
├─ run_id
├─ status: queued | running | succeeded | failed | cancelled
├─ step
├─ workflow_inputs
├─ agent_outputs
├─ node_inputs
├─ dirty_inputs
├─ edge_traversals
├─ usage
├─ artifacts
└─ errors
```

一次 Agent 执行的产物必须是一个 JSON 对象，以输出端口名作为键：

```json
{
  "draft": "...",
  "outline": {
    "sections": ["intro", "body"]
  }
}
```

即使 Agent 只有一个输出端口，也使用同样信封格式。执行器优先把完整回复解析为 JSON；失败时从第一个 `{` 开始使用 `JSONDecoder.raw_decode` 提取首个完整对象。解析成功后必须严格匹配输出端口，不做缺失字段补全、键名猜测或截断 JSON 修复。

### 8.2 调度语义

v0.1 使用确定性的串行就绪队列：

1. 用 workflow inputs 初始化所有 `start` Agent 的输入。
2. 从就绪队列中按 SDL 中的 Agent 顺序取出一个节点执行。
3. 节点成功后，保存输出并发送 `node_exit` 事件。
4. 按 SDL 中的边顺序计算所有出边。
5. 条件为真或无条件时，按 `mappings` 将源输出写入目标 Agent 的累计输入，并标记本轮有新输入。
6. 当目标 Agent 有新输入、没有正在执行，且累计输入已满足全部必填端口时，将其加入就绪队列。
7. 节点执行结束后清空 `dirty_inputs`，但保留 `node_inputs`。这样 Feedback 重入时，原始输入和反馈输入可以同时参与下一次调用。
8. 队列为空且没有运行中的节点时，运行结束。

关键规则：

- 多个必填输入端口是 join 关系，等待全部满足。
- 条件未触发的边不会阻塞下游。
- 可选输入不参与就绪判断。
- 同一输入端口再次收到值时，以最新值为准；累计输入不会因为节点执行而整体清空。
- 一次节点执行完成后必须有新的输入事件才能再次激活，防止节点因已满足必填端口而被无条件重复调度。
- 同一节点在一次运行中可以有多次执行，但仅有反馈触发，且受边级 guard 限制。
- `FEEDBACK` 边每命中一次，`edges.{edge_id}.traversals` 加一；达到上限后运行失败，错误码为 `feedback_limit_exceeded`。
- 全局 `max_steps` 和运行超时是第二道保护。
- 任一节点失败时默认 fail-fast，后续就绪节点不再执行。

### 8.3 Agent 调用

每个 Agent 请求包含：

- 全局背景 Prompt，按 `applies_to` 和 `injection` 配置注入。
- Agent 自身 `system_prompt`。
- 输出端口约定；启用 `json_output` 时追加 JSON 模板和保留键层级要求。
- 当前输入端口组成的 JSON。

对 OpenAI 兼容接口调用 `POST {base_url}/chat/completions`，请求体包含 `model`、`messages`、`temperature` 和可选的 `max_tokens`。当 Agent 的 `json_output.enabled` 为真，或 Profile 的 `capabilities.structured_output` 为真时，请求额外发送：

```json
{
  "response_format": {
    "type": "json_object"
  }
}
```

收到响应后按以下顺序处理：

1. 读取 OpenAI 兼容响应中的 `choices[0].message.content`。
2. 解析 JSON 对象。
3. 校验最外层键与 Agent 输出端口完全一致。
4. 校验每个端口的 `TEXT`、`DATA`、`FILE`、`MIXED` 类型。
5. 校验端口上的 `schema_hint`。
6. 记录 `token_usage` 并把输出写入运行时状态。

因此 `Port 'outline' does not satisfy schema_hint` 表示模型已经返回了 `outline`，但值不满足端口声明的 JSON Schema 子集，例如声明为 `object` 却返回了字符串，或 `score` 超出 `minimum/maximum`。

超时和重试只覆盖网络错误、429 和 5xx。4xx 参数错误、JSON 解析失败和端口校验失败不做无限重试。

### 8.4 子进程协议

服务端启动生成项目：

```text
python workflow.py --input input.json
```

约定：

- 工作目录是 `runs/{run_id}/workspace/`。
- stdout 只输出 NDJSON 事件。
- stderr 输出人类可读诊断日志，并做行数和大小限制。
- API Key 通过环境变量传入子进程，不通过命令行参数传递。
- 服务端异步读取 stdout，逐行校验事件、追加到 `events.jsonl`，并广播给 SSE 订阅者。
- 用户取消运行时，服务端终止整个子进程树。

API 侧同一项目默认只允许一个活动运行，避免生成物或工作区相互污染。

### 8.5 运行结果

Agent 流生成物的位置分为三类：

- 编译产物：`workspace/projects/{project_id}/generated/{compiler_key}/`，包含 `workflow.py`、`config.py`、Agent 配置和运行时模板。
- 运行数据：`workspace/projects/{project_id}/runs/{run_id}/`，包含 `input.json`、`events.jsonl`、`result.json` 和 `workspace/`。
- Agent 输出：节点结果写入运行状态和 `result.json` 的 `outputs`。当工作流声明 `workflow.outputs` 时，`outputs` 保存最终映射结果；所有节点的中间输出可从运行详情或事件重建。

`FILE` 端口只允许引用 `runs/{run_id}/workspace/` 内的文件；V0.1 不会自动把普通模型文本转换成文件。因此需要文件产物时，应由 Agent 返回合法文件引用，或留待后续产物落盘能力实现。

`result.json` 至少包含：

```json
{
  "run_id": "...",
  "status": "succeeded",
  "workflow_hash": "...",
  "outputs": {},
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "error": null
}
```

运行目录是排查“产物在哪里”和“为什么失败”的第一入口：先看 `events.jsonl` 定位最后成功事件和错误事件，再看 `result.json` 的 `outputs`、`usage` 和 `error`。

## 9. 事件协议与前端可视化

### 9.1 事件信封

每一行是一个独立 JSON 对象：

```json
{
  "seq": 12,
  "run_id": "019...",
  "ts": "2026-11-18T08:30:00Z",
  "type": "node_exit",
  "node_id": "reviewer",
  "edge_id": null,
  "data": {
    "status": "succeeded"
  }
}
```

`seq` 在一次运行内单调递增。事件类型在 v0.1 固定为：

- `run_started`
- `node_enter`
- `node_exit`
- `edge_traversed`
- `token_usage`
- `artifact_written`
- `error`
- `run_finished`

后续版本再增加 `human_gate_waiting` 等事件。事件只能追加，不能原地修改。

### 9.2 SSE

前端通过 `GET /api/runs/{run_id}/events` 订阅：

- 服务端把 `seq` 写入 SSE `id` 字段。
- 客户端重连时发送 `Last-Event-ID`，服务端从后续事件继续推送。
- 订阅者先从 `events.jsonl` 补齐已有事件，再接收实时事件。
- 运行完成后服务端发送终止事件并关闭流。

### 9.3 前端状态分离

编辑器中有三份不同状态：

- `WorkflowDraft`：用户正在编辑的 SDL 语义模型。
- `CanvasLayout`：节点位置和视口。
- `RunViewState`：由事件流推导的运行状态。

运行事件不能修改 `WorkflowDraft`。节点运行状态、最近走过的边和错误提示只更新 `RunViewState`，从而支持运行结束后继续编辑。

## 10. 前端编辑器

### 10.1 页面结构

v0.1 只做一个实际工作界面：

- 中央画布。
- 左侧 Agent 节点库和运行控制。
- 右侧节点、边、全局 Prompt、API Profile 配置面板。
- 底部 SDL 预览和校验问题列表。

不需要额外 landing page。首次打开直接进入最近项目或新建项目界面。

### 10.2 画布交互

- 从节点库拖入 Agent 节点。
- 节点间连线后选择边类型。
- 点击节点编辑名称、Prompt、输入输出端口、API Profile 和 JSON 输出。
- 点击边编辑映射、条件和 feedback guard。
- 选中 Agent 或边后，可用 `Delete`/`Backspace` 或检查器中的删除按钮移除。
- 删除 Agent 时，同时移除它的 `start` 引用、关联边、工作流输出和画布元素。
- 删除边时，同时移除 SDL 边和 `canvas.json` 中的布局记录。
- 支持 SDL 导入、导出和只读预览。
- 导入 SDL 时，如果没有对应 `canvas.json`，前端使用确定性自动布局生成节点位置，再要求用户确认保存。
- 校验结果显示在具体节点或边附近，同时提供问题列表。

### 10.3 右侧检查器

- 检查器可最小化为右侧竖向入口，再次点击恢复。
- 展开时支持拖拽左侧分隔条调整宽度，范围为 `300-560px`，默认 `360px`。
- 分隔条可获得键盘焦点，使用方向键微调宽度；`Home`/`End` 可移动到允许的最小/最大宽度。
- 收起状态和宽度分别持久化到 `localStorage`：
  - `loomlane.inspector.open`
  - `loomlane.inspector.width`
- 移动端不显示拖拽分隔条，检查器按单列布局展示，避免压缩画布。

### 10.4 Agent 与边删除

- 画布删除和检查器删除共用同一状态更新逻辑，避免 SDL 与 canvas 不同步。
- Agent 删除后，所有引用该 Agent 的边都一并删除，防止留下悬空引用。
- 工作流 `outputs` 中引用被删除 Agent 的条目会同步删除。
- 边删除只影响该边，不删除两端 Agent。
- 删除当前选中对象后，检查器清空选择并回到安全状态。
- 删除修改的是当前草稿，仍需保存项目才会写入 SDL。

### 10.5 条件编辑器

- `AND`/`OR` 条件组可以添加谓词或嵌套条件组。
- `NOT` 可包装谓词或其他条件组，并提供移除包装的操作。
- 条件组内至少保留一个子条件时显示删除按钮，避免生成没有语义的空组。
- 谓词支持 `eq`、`ne`、`lt`、`lte`、`gt`、`gte`、`in`、`contains`、`exists`。
- 字段下拉来自工作流输入、Agent 输出、边遍历次数和当前运行步数。
- 比较值自动尝试解析 `true`、`false`、`null`、数字、数组和对象；解析失败时保留为字符串。
- 高级 JSON 编辑用于兜底复杂条件，常规操作不需要手写 AST。

### 10.6 JSON 输出编辑器

- 每个 Agent 独立启用 JSON 输出，默认关闭。
- 顶部字段可新增、重命名和删除；“添加子字段”把叶值转换为嵌套对象。
- 普通文本按字符串处理；合法 JSON 文本按布尔、数字、数组或对象处理。
- 编辑区显示结构化预览，便于确认最终发给模型的模板。
- 行 key 使用稳定的同级索引，而不是可编辑字段名，修复了输入一个字符后输入框失焦的问题。
- 最外层键应与输出端口一致；`outline.sections` 需要在 UI 中分层填写，不能作为单层键名直接输入。

### 10.7 运行态

节点状态：

- `idle`
- `waiting`
- `ready`
- `running`
- `succeeded`
- `failed`
- `skipped`
- `cancelled`

边状态至少区分未执行、已遍历、条件和错误。运行期间高亮当前节点和最近一条已遍历边；运行结束后保留最终状态，直到用户开始下一次运行或手动清除。

底部抽屉展示 SDL、校验问题、运行事件和最终结果。运行事件不能修改当前编辑草稿，避免执行状态污染尚未保存的工作流。

## 11. HTTP API

```text
GET    /api/health

GET    /api/projects
POST   /api/projects
GET    /api/projects/{project_id}
PUT    /api/projects/{project_id}
DELETE /api/projects/{project_id}

POST   /api/projects/{project_id}/validate
POST   /api/projects/{project_id}/import-sdl
GET    /api/projects/{project_id}/sdl
POST   /api/projects/{project_id}/generate

GET    /api/projects/{project_id}/secrets/{profile_id}/status
PUT    /api/projects/{project_id}/secrets/{profile_id}
DELETE /api/projects/{project_id}/secrets/{profile_id}

POST   /api/projects/{project_id}/runs
GET    /api/runs/{run_id}
GET    /api/runs/{run_id}/events
POST   /api/runs/{run_id}/cancel
```

后端通过 Pydantic 自动生成 OpenAPI，前端可以从 schema 生成类型。项目更新响应必须返回新 revision；前端保存失败时提示重新加载 SDL 或重新应用本地更改。

关键响应语义：

- 项目 `DELETE` 删除整个本地项目目录，属于不可撤销操作，前端必须二次确认。
- 密钥 `DELETE` 只删除系统凭据库和 DPAPI 回退中的对应记录，不修改 SDL。
- Profile 删除没有独立服务端资源；它是在 SDL 更新中删除 `api_profiles` 项，并同步重写引用它的 Agent。
- 请求体被 Pydantic 拒绝时返回 `422`。响应中的 `loc` 会指出具体字段，例如 `["body","sdl","agents",0,"json_output"]`。
- Pydantic 模型使用 `extra="forbid"`。前端提交了后端模型不存在的字段时，错误类型为 `extra_forbidden`；这通常表示后端进程仍在运行旧代码。

## 12. 安全与可靠性

- 不执行用户提供的 Python、Jinja2 或系统命令。
- 条件求值器使用白名单 AST，不调用 `eval`。
- YAML 使用 `safe_load` 并设置输入大小上限。
- 文件路径统一经过工作区根目录校验，拒绝 `..` 和绝对路径逃逸。
- 子进程只继承必要环境变量。
- stdout 事件和 stderr 均设大小上限，避免日志耗尽磁盘。
- 单次运行设置超时、最大步数和子进程输出上限。
- API Key、Authorization 头和完整请求体不进入日志。
- 生成代码、运行记录和项目文件中都不能包含密钥。
- 本地开发默认只监听 `127.0.0.1`，并限制前端 origin。

## 13. 测试与验证

### 13.1 验证命令

```powershell
.\.venv\Scripts\python -m compileall -q backend\src
.\.venv\Scripts\python -m pytest backend\tests -q
pnpm --dir apps/web test
pnpm --dir apps/web build
```

本轮验证结果：

| 项目 | 结果 |
| --- | --- |
| 后端 Python 编译检查 | 通过 |
| 后端 pytest | 19 passed |
| 前端 Vitest | 13 passed |
| 前端生产构建 | 通过 |
| 浏览器检查 | 检查器折叠/展开、JSON 字段连续输入焦点、Profile 删除交互正常 |

### 13.2 后端已覆盖

- SDL 结构校验、错误路径和 `extra="forbid"` 行为。
- 端口类型兼容、普通环和 Feedback 祖先规则。
- 条件 AST 安全求值、非法路径和非法操作符拒绝。
- 输入 join、条件分支、Feedback 重入与 guard 超限。
- Agent JSON 输出的 `response_format` 开关、端口校验和尾随文本提取。
- 系统凭据库、DPAPI 回退、原子写入、origin 绑定和删除行为。
- 项目创建、保存、revision、导入、生成、运行和删除 API。
- 生成项目哈希稳定性和密钥不进入生成物。

### 13.3 前端已覆盖

- SDL 与画布状态互转。
- API Profile 删除、Agent 自动迁移和唯一 Profile 阻止删除。
- 检查器宽度边界和 UI 状态持久化。
- 条件谓词追加和多层组合。
- 关键类型转换和安全默认值。
- JSON 模板编辑使用稳定行 key，避免字段重命名导致失焦。

### 13.4 手工与后续测试

- 已手工创建临时项目，验证包含 `json_output` 的 SDL 可以保存、重新读取并删除。
- 已通过浏览器连续输入 JSON 字段，确认输入框不再因行 key 变化而失焦。
- 后续应将“创建项目 -> 配置 Profile -> 连线 -> 校验 -> 生成 -> 运行 -> 查看结果”固化为 CI 中的 Playwright 全流程测试。

## 14. 实施状态

| 阶段 | 状态 | 已落地内容 |
| --- | --- | --- |
| A：契约和基础工程 | 已完成 | 前后端工程、SDL 模型、示例项目、校验器、测试基础 |
| B：编译与无界面运行 | 已完成 | Jinja2 生成、OpenAI 兼容调用、串行调度、Feedback guard、事件输出 |
| C：API 与本地持久化 | 已完成 | 项目 CRUD、revision、原子写入、密钥存储、编译缓存、SSE、取消运行 |
| D：可视化编辑器 | 已完成 | React Flow、节点/边配置、检查器、删除、条件树、JSON 编辑器、运行高亮 |
| E：加固与文档 | 基本完成 | 路径安全、密钥隔离、测试、示例和故障排查；全流程 Playwright CI 留作后续 |

## 15. 常见故障排查

### 15.1 保存时出现 `Request failed with status 422`

`422` 表示请求已经到达 FastAPI，但 Pydantic 拒绝请求体。不要先怀疑网络；应查看响应体中的 `type`、`loc` 和 `msg`。

典型响应：

```json
{
  "type": "extra_forbidden",
  "loc": ["body", "sdl", "agents", 0, "json_output"],
  "msg": "Extra inputs are not permitted"
}
```

这表示前端发送了 `json_output`，但正在运行的后端模型不认识该字段，通常是后端进程仍在运行修改前的代码。处理方法：

1. 停止旧后端进程。
2. 使用 `--reload` 重新启动：

   ```powershell
   .\.venv\Scripts\python -m uvicorn loomlane.main:app --app-dir backend/src --reload --port 8000
   ```

3. 刷新前端并重试保存。
4. 必要时检查 OpenAPI 是否已包含 `AgentJsonOutput`。

修改 Pydantic 模型、SDL 字段或生成模板后，重启后端是必须步骤。

### 15.2 `Failed to fetch` 或 `Failed to fetchsvg`

“Failed to fetch”是浏览器无法完成 HTTP 请求，不包含服务端业务错误。常见原因：

- 后端未启动、端口不是 `8000` 或进程已退出。
- 前端请求的 API 地址错误。
- 浏览器页面使用 `localhost`，API 使用 `127.0.0.1`，而服务端 CORS 没允许该 origin。
- 请求触发了浏览器超时或网络代理拦截。

先检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

如果健康检查失败，先恢复后端；如果健康检查正常，再检查浏览器 Network 面板中的请求 URL、status 和 CORS 响应。`Failed to fetchsvg` 中的 `svg` 通常只是错误旁图标或拼接文本，不代表 SVG 请求本身是根因。

### 15.3 模型 JSON 解析失败

报错示例：

```text
Agent response contained invalid JSON: Extra data: line 1 column 1629
```

含义是模型返回内容不是单个合法 JSON。当前运行时会在直接解析失败后从第一个 `{` 开始 `raw_decode`，可容忍首个完整 JSON 对象后的解释文本；如果仍失败，通常是截断、多个 JSON 对象或多个根值。

处理顺序：

1. 启用 Agent 的 `json_output`。
2. 填写完整模板，并保证服务支持 `response_format={"type":"json_object"}`。
3. 降低输出长度或提高 `max_tokens`，排除截断。
4. 查看模型原始响应，确认没有 Markdown 代码块外的额外对象。

系统不会猜测缺失字段或自动修补 JSON。

### 15.4 `Port 'outline' does not satisfy schema_hint`

该错误说明模型返回了 `outline`，但值的类型或范围不符合端口声明。例如：

```yaml
outputs:
  - name: outline
    type: DATA
    schema_hint:
      type: object
```

此时模型返回字符串 `"..."` 会失败，必须返回类似：

```json
{
  "outline": {
    "sections": ["背景", "分析", "结论"]
  }
}
```

处理时要同时检查三层：

- `json_output.template` 是否把 `outline` 表示为对象。
- Agent 输出端口 `outline` 的 `type` 和 `schema_hint` 是否匹配。
- 模型实际返回的 JSON 是否把对象错误地编码成了字符串。

### 15.5 `Feedback edge ... exceeded max_traversals=3`

Feedback 边已允许成功遍历 3 次，第 4 次仍满足条件时运行失败。常见原因是审查分数一直没有达到阈值，或条件引用了错误字段。处理后可以：

- 修正审查和修订 Prompt，使反馈循环能够收敛。
- 检查 `condition` 的字段路径和阈值。
- 在确有需要时提高 `guard.max_traversals`，但应保留明确上限。

该错误是预期保护，不应通过无限循环规避。

### 15.6 API Key 显示未配置

- 确认先保存项目，再为同一个 `project_id + profile_id` 保存密钥。
- 确认修改 `base_url` 的 origin 后重新输入密钥。
- Windows 上检查 `workspace/credentials.dpapi` 是否存在；不要尝试将其中内容粘贴到配置中。
- 用 API 检查器的状态接口确认 `configured` 和 `origin`。
- 本地 `localhost`、`127.0.0.1`、`::1` 服务允许无 Key 调用；其他地址缺少 Key 会直接报错。

### 15.7 400/404 from `/chat/completions`

- `404` 通常是 `base_url` 已包含 `/chat/completions`，导致运行时重复拼接。
- `400` 可能表示模型名不存在、`response_format` 不受支持或请求参数超出供应商限制。
- 若服务不支持 JSON mode，先修正供应商兼容性，不要依赖前端删除端口来绕过错误。

## 16. V0.1 验收结果

- [x] 用户可以在画布上创建至少两个 Agent，并配置 Prompt、端口和 OpenAI 兼容 API Profile。
- [x] 工作流能保存为可读 SDL，重新打开后语义和布局正确恢复。
- [x] 非法端口、缺失引用、普通环、非法条件和缺少 Feedback guard 会被明确拒绝。
- [x] 同一 SDL 在相同编译器版本下生成确定性文件内容。
- [x] 生成项目无需安装 LoomLane 包即可在独立 Python 环境运行。
- [x] Pipe、条件分支和有界 Feedback 的行为通过后端测试。
- [x] 前端能实时看到节点进入、节点退出、边遍历、Token 用量和最终结果。
- [x] 运行事件完整写入 `events.jsonl`，可用于刷新后重建运行状态。
- [x] API Key 不出现在项目文件、SDL、日志、生成代码或 Git 中。
- [x] 运行失败时能看到节点级错误和可定位的错误消息。
- [x] 右侧检查器支持折叠、宽度调整和 UI 偏好持久化。
- [x] Agent、边和 API Profile 具有明确的删除及引用清理语义。
- [x] 条件编辑器支持多谓词、嵌套、`AND`/`OR`/`NOT` 和高级 JSON。
- [x] Agent 可独立启用层次化 JSON 输出，并发送 `json_object` 响应格式。

## 17. 后续版本预留

- v0.2：`LOOP`、`FAN_OUT`、`AGGREGATE`、`HUMAN_GATE`、并行执行、运行回放、token 累计上限、产物自动落盘。
- v0.3：Agent 内 Python 精细规则、沙箱、多 provider、能力路由和更强条件语言。

SDL 版本升级遵循以下规则：

- 新字段优先增加为可选字段，并给默认值。
- 删除或改变语义必须提升 `sdl_version`。
- 编译器按 SDL 版本选择迁移器。
- 生成项目 manifest 记录编译器版本，保证历史运行可追溯。

