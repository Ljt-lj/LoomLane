# LoomLane 初步技术方案

- 状态：初稿，供后续实现与评审使用
- 目标版本：v0.1
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

## 2. 目标与范围

### 2.1 v0.1 目标

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

与初始 JSON 方案相比，建议收敛以下细节：

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

- API Key 进入操作系统凭据库，例如 Windows Credential Manager、macOS Keychain 或 Linux Secret Service。
- 凭据键由 `project_id` 与 `profile_id` 共同推导，不把密钥写入 SDL、日志、运行记录或生成代码。
- 凭据记录同时保存规范化后的 `base_url` origin 绑定信息；origin 改变时旧密钥立即失效并要求重新输入。
- 浏览器只在创建或更新 profile 时提交一次密钥，之后只显示脱敏值。
- 开发环境允许通过环境变量注入密钥，但不能把明文回退方案作为默认行为。

### 5.4 API Profile 生命周期

- Profile 的名称、`base_url`、模型、超时、重试和能力标记由当前项目的 SDL 管理。
- 服务端按项目提供密钥写入、更新、删除和状态查询，不再保存第二份 Profile 元数据。
- 修改模型名不会影响已有密钥；修改 `base_url` 的 origin 会使旧密钥失效。
- 删除 Profile 时，前端先移除相关 Agent 引用或改为其他 Profile，再单独删除密钥。
- 导入其他项目时不自动复用原项目密钥；即使 `profile_id` 相同，也要求用户在确认 `base_url` 后重新补充凭据。

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

禁止函数调用、属性写入、任意索引表达式和动态求值。前端提供字段选择器和比较器，不要求用户手写表达式。

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

即使 Agent 只有一个输出端口，也使用同样信封格式。执行器从模型回复中提取一个完整 JSON 对象，并校验所需端口。解析失败直接标记节点失败，不做猜测式修复。

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
- 当前输入端口组成的 JSON。
- 输出端口名和严格 JSON 输出要求。

对 OpenAI 兼容接口调用 `POST {base_url}/chat/completions`。如果 profile 标记支持 structured output，可以发送相应参数；否则仍依赖 Prompt 约束和本地 JSON 校验。

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
- 点击节点编辑名称、Prompt、输入输出端口、API Profile。
- 点击边编辑映射、条件和 feedback guard。
- 支持删除、复制和撤销/重做。
- 支持 SDL 导入、导出和只读预览。
- 导入 SDL 时，如果没有对应 `canvas.json`，前端使用确定性自动布局生成节点位置，再要求用户确认保存。
- 校验结果显示在具体节点或边附近，同时提供问题列表。

### 10.3 运行态

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

## 11. HTTP API 草案

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

## 13. 测试策略

### 13.1 后端单元测试

- SDL 结构校验和错误定位。
- 端口类型兼容矩阵。
- 普通环检测和 Feedback 祖先校验。
- 条件 AST 的安全求值和非法路径拒绝。
- 输入 join、条件分支和 feedback guard。
- 模型 JSON 信封解析和端口校验。
- 生成项目哈希稳定性和 Prompt 安全转义。

### 13.2 编译与运行集成测试

准备以下 fixture：

- 最小单节点工作流。
- 两节点 Pipe。
- 条件为假时不执行下游。
- 条件为真时执行下游。
- Feedback 修订两轮后通过。
- Feedback 超限失败。
- 非法普通环拒绝编译。
- 缺失 API Key 失败且不泄露密钥。

使用本地假 OpenAI 兼容 HTTP 服务或 `httpx.MockTransport` 执行生成项目，验证事件顺序、运行结果和 `result.json`。

### 13.3 前端测试

- SDL 与 React Flow 状态互转。
- 节点和边配置编辑。
- 校验问题映射到画布元素。
- SSE 事件驱动的节点和边状态变化。
- 导入失败、保存冲突和运行失败提示。

### 13.4 端到端验收

Playwright 覆盖完整路径：

```text
新建项目
  -> 添加两个 Agent
  -> 配置 API Profile（使用测试服务）
  -> 连线
  -> SDL 校验通过
  -> 生成代码
  -> 启动运行
  -> 看到节点高亮事件
  -> 查看成功结果和运行记录
```

## 14. 实施顺序

### 阶段 A：契约和基础工程

- 建立前后端工程、开发命令和 CI。
- 完成 SDL Pydantic 模型和 JSON Schema。
- 固化示例 SDL、项目目录和事件协议。
- 完成第一版校验器和测试 fixture。

### 阶段 B：编译与无界面运行

- 完成 Jinja2 代码生成模板。
- 完成生成项目的 Agent HTTP 调用。
- 完成显式调度器、Feedback guard 和事件输出。
- 使用假模型服务跑通 CLI 集成测试。

### 阶段 C：API 与本地持久化

- 项目 CRUD、revision 和原子写入。
- API Profile 与系统凭据库。
- 编译缓存和运行管理器。
- SSE 事件补发和取消运行。

### 阶段 D：可视化编辑器

- React Flow 画布和节点/边配置。
- SDL 导入导出、校验问题展示。
- 运行控制和事件高亮。
- 保存冲突和错误恢复。

### 阶段 E：加固

- 路径安全、日志脱敏和资源上限。
- 运行记录与示例项目。
- Playwright 全流程测试。
- 文档化生成代码和 SDL 版本兼容规则。

## 15. v0.1 验收标准

满足以下条件才算完成 v0.1：

1. 用户可以在画布上创建至少两个 Agent，并配置 Prompt、端口和 OpenAI 兼容 API Profile。
2. 工作流能保存为可读的 SDL，重新打开后语义和布局都正确恢复。
3. 非法端口、缺失引用、普通环、非法条件和缺少 Feedback guard 会被明确拒绝。
4. 同一 SDL 在相同编译器版本下生成确定性文件内容。
5. 生成项目无需安装 LoomLane 包即可在独立 Python 环境运行。
6. Pipe、条件分支和有界 Feedback 的行为通过集成测试。
7. 前端能实时看到节点进入、节点退出、边遍历、token 用量和最终结果。
8. 运行事件完整写入 `events.jsonl`，刷新页面后可以通过事件重建本次运行的最终状态。
9. API Key 不出现在项目文件、SDL、日志、生成代码或 Git 中。
10. 运行失败时用户能看到节点级错误，而不是只有一个无上下文的 500。

## 16. 后续版本预留

- v0.2：`LOOP`、`FAN_OUT`、`AGGREGATE`、`HUMAN_GATE`、并行执行、运行回放、token 累计上限、产物自动落盘。
- v0.3：Agent 内 Python 精细规则、沙箱、多 provider、能力路由和更强条件语言。

SDL 版本升级遵循以下规则：

- 新字段优先增加为可选字段，并给默认值。
- 删除或改变语义必须提升 `sdl_version`。
- 编译器按 SDL 版本选择迁移器。
- 生成项目 manifest 记录编译器版本，保证历史运行可追溯。

