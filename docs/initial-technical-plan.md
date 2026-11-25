# LoomLane V0.3 技术方案与落地记录

- 状态：V0.3 已落地，本文档作为当前实现基线与后续演进依据
- 目标版本：V0.3
- 输入材料：`LL.json`
- 更新时间：2026-11-25

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

### 1.1 V0.3 已实现能力

| 模块 | 当前实现 |
| --- | --- |
| 项目与画布 | 创建、打开、保存、导入/导出 SDL，React Flow 画布与 SDL/canvas 分离持久化 |
| Agent 与边 | Agent、端口及 `PIPE`、`CONDITIONAL`、`FEEDBACK`、`LOOP`、`FAN_OUT`、`AGGREGATE`、`HUMAN_GATE` 编辑 |
| 条件 | 受限条件 AST，支持范围、前后缀、正则、空值、长度和集合谓词；前端支持 `AND`、`OR`、`NOT`、多谓词添加/删除/嵌套和高级 JSON 编辑 |
| Agent Python 规则 | 每个 Agent 可配置输入前/输出后布尔规则；使用白名单 AST 解释器和独立 Python 进程沙箱执行 |
| 数据变换 | Agent 输入前后与边映射支持受限 Python 表达式；表达式返回严格 JSON，可在模型前后清理、重组和跨端口类型转换数据 |
| 面板 | 左、右、底部面板均可收纳；左右面板可调宽度，底部面板可调高度，偏好持久化到浏览器 |
| API Profile | 增删改、密钥状态查询、密钥写入/删除；删除被引用 Profile 时自动迁移或阻止 |
| 模型 JSON 输出 | 每个 Agent 可独立启用层次化 JSON 模板，并通过 `response_format={"type":"json_object"}` 请求 JSON |
| 图片生成 | Chat/Image Agent 双模式、图片供应商适配器、OpenAI Images/SiliconFlow/AUTOMATIC1111 协议、图片校验与自动落盘 |
| 编译 | 从规范化 SDL 确定性生成独立 Python 项目 |
| 运行 | 子进程执行、条件分支、有界 Feedback/Loop、Fan-out、Aggregate、人工确认、并行度和取消 |
| 限额 | 单次请求 `max_tokens`、Agent 累计 `max_total_tokens`、Workflow 累计 `max_total_tokens`、`max_steps` |
| 产物 | `TEXT`、`DATA`、`FILE` 输出自动写入运行工作区，生成 `artifact_written` 事件并记录字节数 |
| 事件与回放 | NDJSON 落盘、SSE 实时推送、序号回放、播放/暂停、单步拖动、倍速和实时跳转 |
| 密钥 | OS 凭据库优先，Windows DPAPI 加密回退，按项目与 Profile 隔离并绑定 origin |
| 测试 | 前端 Vitest、后端 pytest，覆盖校验、运行、密钥和关键编辑工具 |

### 1.2 阅读约定

- “已实现”表示当前仓库中存在对应代码和行为。
- “约束”表示 V0.3 的明确边界，不应被理解为前端 Bug。
- “后续预留”表示尚未纳入 V0.3 交付范围的能力。
- 示例中的 API Key 一律使用占位符，不得把真实密钥写入 SDL、Markdown、Git 或日志。

## 2. 目标与范围

### 2.1 V0.3 已实现范围

1. 支持创建、保存、导入和导出工作流。
2. 支持 Agent 角色、API Profile、全局背景 Prompt 的配置。
3. 支持 `PIPE`、`CONDITIONAL`、`FEEDBACK`、`LOOP`、`FAN_OUT`、`AGGREGATE`、`HUMAN_GATE` 七种边。
4. 支持输入输出端口类型检查、反馈/循环回路边界，以及聚合入边一致性检查。
5. 从 SDL 生成可独立运行的 Python 项目。
6. 通过 OpenAI 兼容接口执行 Agent。
7. 支持批次感知的并行调度、Fan-out、全量/任一聚合和人工确认暂停。
8. 将运行事件流式反馈到前端、持久化为 `events.jsonl`，并按事件序号进行运行回放。
9. 在本地项目目录中保存画布布局、SDL、生成物、运行记录和自动落盘产物。
10. 对单次模型调用、Agent 累计、Workflow 累计和运行步数实施分层限额。
11. 支持图片生成 Agent，将图片服务的 Base64 或 URL 结果保存为运行工作区文件，并继续走统一 artifacts 流程。
12. 支持 Agent 输入前/输出后 Python 精细规则，并对规则表达式实施语法白名单、进程隔离、资源上限和事件记录。
13. 支持更强的声明式边条件，包括范围、前后缀、正则、空值、长度和多值集合判断。
14. 支持 Agent 输入/输出变换与边映射变换，使用同一受限表达式语言完成数据清理、重组和跨端口类型转换。

### 2.2 V0.3 非目标

- 不做多租户、云端协作、权限系统。
- 不支持任意 Python、JavaScript 或 Jinja2 表达式执行。
- 不做 token 级模型流式输出，只回传节点、边、用量和错误等运行事件。
- 不提供第三方供应商插件热加载；V0.3 已内置 OpenAI Images、SiliconFlow 和 AUTOMATIC1111 / Forge 图片适配器，并默认使用本地 AUTOMATIC1111 / Forge。
- 不执行任意 Python。Agent 规则和数据变换只接受可枚举、可审计的表达式子集，不提供 import、函数定义、循环、推导式、赋值、文件/网络访问或任意方法调用。
- 不提供分布式执行或跨主机调度；并行仅限单个生成项目的本地线程池。
- 不提供产物的在线编辑、版本管理或对象存储；产物文件由本地运行工作区管理。
- 图片能力只做生成，不做图生图、局部重绘、蒙版编辑或视频生成；这些能力需要独立输入协议。
- 不校验 `AGGREGATE` 映射是否覆盖目标 Agent 的全部必要输入，仍需用户在保存前补齐。

## 3. 关键设计决策

| 决策 | 采用方案 | 原因 |
| --- | --- | --- |
| 语义真源 | `workflow.sdl.yaml` | 可读、可 diff、可版本控制，代码生成器只依赖它 |
| 画布真源 | `canvas.json` 只存布局 | 避免节点配置和 SDL 各存一份后产生冲突 |
| API Profile | 非密钥元数据写入 SDL；密钥单独存入系统凭据库 | 项目可移植，且密钥不进入 Git 或 SDL |
| 条件表达式 | 自定义声明式 AST | 可校验、可序列化，比字符串 eval 安全，并支持更强的常用谓词 |
| Agent Python 规则 | 白名单 AST 解释 + 独立子进程 | 允许在不开放任意 Python 的前提下表达精细业务规则，并通过进程和资源边界隔离失败 |
| 数据变换 | 受限表达式返回严格 JSON，由运行时按固定阶段应用 | 让输入清理、输出重组和边级转换共享同一套可校验语义，避免引入脚本节点或隐式类型转换 |
| Feedback | 只允许回注到上游节点，必须有遍历上限 | 保留核心语义，同时避免不可控的任意环 |
| 调度模型 | 显式就绪队列和运行状态机 | 比拓扑排序更适合条件分支和有界反馈 |
| 执行隔离 | 生成项目 + 独立 Python 子进程 | 崩溃、取消和日志边界清晰 |
| 事件协议 | NDJSON 写入 stdout，由服务端转 SSE | 协议简单，方便实时消费和落盘 |
| 持久化 | 文件系统 + JSON/YAML，不引入业务数据库 | V0.3 本地单用户场景足够，项目天然可迁移 |
| 批次模型 | 每次 Agent 执行都绑定 `batch_id` | 同时表达普通流程、Fan-out 分支、Loop/Feedback 重入和聚合等待 |
| 并发调度 | 有界线程池 + 显式就绪队列 | 在单机本地执行中获得可控并行，不引入分布式运行时 |
| 人工暂停 | Gate 文件 + 进程内等待线程 | 子进程可暂停、状态可落盘，API 可在独立请求中批准或拒绝 |
| 运行回放 | 原始事件流 + 纯函数推导视图 | 回放与实时消费共用一套状态机，支持任意事件序号重建 |
| 图片生成 | Agent 增加 `chat`/`image` 模式，图片参数和协议放在 API Profile | 同一工作流可以混用文本与图片服务，同时避免把模型能力和请求参数散落到 Agent Prompt |
| 图片供应商 | `image_generation.protocol` 选择适配器，公共运行层负责调用与落盘 | 将供应商差异限制在请求构造和响应提取，密钥、重试、安全校验及 artifacts 逻辑保持一致 |

与初始 JSON 方案相比，V0.3 落地时已经收敛以下细节：

1. 不再维护独立的 `api_profiles.local.yaml`。SDL 保存 profile 的非敏感配置，系统凭据库保存密钥。
2. 不再同时使用 `from_port`、`to_port` 和 `data_mapping` 描述同一个映射。统一使用 `mappings` 列表。
3. 不把 Jinja2 或字符串 `eval` 用作条件执行引擎。V0.3 使用可枚举、可校验的条件 AST；Agent 规则额外使用白名单 Python 表达式解释器。
4. 生成项目不依赖 LoomLane Python 包。调度器和 Agent 调用代码由模板生成，第三方运行依赖只保留 `httpx`。
5. V0.3 仍不宣称支持任意图。忽略 `FEEDBACK` 和 `LOOP` 边后，工作流必须是 DAG。
6. 并行、人工确认和聚合只在本机单个生成项目进程内实现，不引入消息队列或分布式协调。
7. 运行回放只重放已记录事件，不重新请求模型，也不重新执行副作用。

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

## 6. SDL 0.3 设计

SDL 使用 YAML 保存，解析时必须使用 `safe_load`，并限制文件大小、锚点展开和嵌套深度。

### 6.1 示例

```yaml
sdl_version: "0.3"

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
    loop_limit_default: 10
    max_steps: 200
    max_total_tokens: 50000
    max_parallel: 4

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
      max_total_tokens: 12000
    json_output:
      enabled: true
      template:
        draft: 完整的 Markdown 草稿文本
        outline:
          sections: 章节标题字符串数组
    rules:
      - phase: input
        expression: "len(inputs.requirement) <= 5000"
        message: 需求说明不能超过 5000 字
        enabled: true
      - phase: output
        expression: "not_empty(outputs.draft) and len(outputs.draft) >= 100"
        message: 草稿内容不完整
        enabled: true
    input_transform:
      mode: fields
      fields:
        - path: requirement
          value: inputs.requirement
        - path: feedback
          value: 'inputs.feedback if "feedback" in inputs else ""'
    output_transform:
      mode: fields
      fields:
        - path: draft
          value: raw_outputs.draft
        - path: outline
          value: 'raw_outputs.outline if not_empty(raw_outputs.outline) else {"sections": []}'
    output_dir: artifacts/drafter

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
    limits:
      max_tokens: null
      max_total_tokens: 8000
    output_dir: null

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

`sdl_version` 目前接受 `"0.1"`、`"0.2"` 和 `"0.3"`，新建项目默认写入 `"0.3"`。
读取旧版 SDL 时仍由同一套运行时按兼容字段执行；新增 Agent 规则建议使用 `"0.3"`。

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
- `schema_hint` 在 V0.3 可保存任意 JSON Schema 子集；运行时可用于附加校验，不替代端口类型。

图片 Agent 额外要求：

- `agents[].mode` 必须是 `image`；旧配置缺省时按 `chat` 处理。
- 引用的 API Profile 必须设置 `capabilities.image_generation: true`。
- 必须有且只有一个 `FILE` 输出端口，图片运行时会把生成文件路径写入该端口。
- 不允许启用 `json_output`，图片服务响应与普通 Chat JSON 输出是两套协议。

### 6.4 条件表达式

V0.3 的边条件不使用原始字符串或 `eval`，而是使用可序列化 AST：

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

| 类别 | 运算符 | 运行语义与右侧值 |
| --- | --- | --- |
| 逻辑 | `and`、`or`、`not` | 使用 `args` 组合子条件 |
| 比较 | `eq`、`ne`、`lt`、`lte`、`gt`、`gte` | 数值比较允许整数与浮点混用；普通比较要求类型一致 |
| 范围 | `between` | 右侧必须是两个元素的数组，闭区间判断 |
| 成员 | `in`、`contains` | `in` 判断左值是否属于右侧容器；`contains` 判断右侧值是否属于左侧容器 |
| 字符串 | `starts_with`、`ends_with` | 左右值都必须是字符串 |
| 正则 | `matches` | 右侧是正则字符串，使用 `re.search` 语义 |
| 空值 | `empty`、`not_empty` | 单目运算符，仅提供 `left` |
| 长度 | `length_eq` | 右侧必须是非负整数；支持字符串、列表、元组、对象和集合 |
| 集合 | `contains_all`、`contains_any` | 右侧是候选值数组，分别要求全部或任一成员命中 |
| 存在性 | `exists` | 单目运算符，仅检查引用是否存在 |

正则约束：

- pattern 最大 `256` 字符，被匹配文本最大 `4096` 字符。
- 不允许分组、lookaround、内联 flag 和反向引用。
- 量词上限为 `1000`。
- 编译失败或类型不匹配时条件返回 `false`，不会执行动态代码。

允许的引用根：

- `$.workflow.inputs`
- `$.agents.{agent_id}.outputs`
- `$.edges.{edge_id}.traversals`
- `$.run.step`

禁止函数调用、属性写入、任意索引表达式和动态求值。前端条件编辑器支持添加、删除和嵌套多个谓词，并通过 `AND`/`OR` 条件组组合；高级 JSON 编辑仅作为复杂条件的补充入口。

边与条件的关系：

- `PIPE` 不允许配置条件。
- `CONDITIONAL` 必须配置条件。
- `FEEDBACK` 可以有条件；不配置时首次成功传播即触发反馈。
- `LOOP` 必须配置条件，条件为真时继续回注，为假时退出回路。
- `FAN_OUT`、`AGGREGATE`、`HUMAN_GATE` 可以通过通用 `condition` 决定这条边是否参与本轮传播。

### 6.5 Agent Python 精细规则

Agent 规则用于在模型调用前后执行可审计的布尔校验。规则保存在 Agent 的
`rules` 数组中：

```yaml
rules:
  - phase: input
    expression: "len(inputs.brief) <= 5000"
    message: Brief is too long
    enabled: true
  - phase: output
    expression: "not_empty(outputs.draft) and len(outputs.draft) >= 100"
    message: Draft is incomplete
    enabled: true
```

字段语义：

- `phase: input` 在模型请求发出前执行；全部通过后才允许调用模型。
- `phase: output` 在模型响应解析、端口类型和 `schema_hint` 校验通过后、产物落盘前执行。
- `expression` 必须是返回严格布尔值（`bool`）的 Python 表达式；返回整数、字符串或其他真值会被拒绝。
- `message` 可选，规则失败时作为用户可读原因。
- `enabled: false` 的规则保留在 SDL 中但不执行。
- 同一阶段的所有启用规则按声明顺序全部执行，任一规则失败都会终止当前 Agent 执行。

可访问根：

| 阶段 | 可访问根 |
| --- | --- |
| `input` | `inputs`、`workflow`、`edges`、`run` |
| `output` | `inputs`、`outputs`、`workflow`、`edges`、`run` |

`output` 阶段不允许读取尚未产生的其他 Agent 输出；输入阶段引用 `outputs` 会在保存/校验时被拒绝。

允许的安全 helper：

- 长度与空值：`len`、`length`、`empty`、`not_empty`
- 类型与数值：`str`、`int`、`float`、`bool`、`min`、`max`
- 集合聚合：`any`、`all`、`contains`
- 字符串：`startswith`、`endswith`、`lower`、`upper`

允许的 Python 子集包括常量、字段/下标读取、列表/元组/字典/集合字面量、单双目算术、布尔运算、比较、条件表达式和上述 helper 调用。禁止 import、赋值、函数定义、lambda、推导式、生成器、循环、异常处理、类、dunder 属性、动态调用、关键字参数、星号参数和任意其他方法调用。

沙箱与资源边界：

- 规则先由独立 AST 校验器检查，再由不调用 `eval`/`exec` 的白名单解释器执行。
- 该解释器运行在独立的 `python -I -S` 子进程中，使用临时工作目录、最小化环境变量和 JSON stdin/stdout 协议。
- 主运行器只接收结构化布尔结果或结构化错误，不共享解释器全局状态。
- 表达式最大 `2000` 字符、最多 `200` 个 AST 节点、最大深度 `24`。
- 上下文序列化后最大 `1 MiB`，单次执行最多 `1000` 个解释器操作。
- 字符串最大 `100000` 字符、集合最大 `10000` 项、整数最大 `512` bit。
- 单次规则默认超时 `2` 秒（包含隔离子进程启动时间）；超时、进程异常、非布尔结果和非法上下文都会转换为规则执行错误。

运行时为每条实际执行的规则按顺序发出：

- `rule_started`：包含 `phase`、`rule_index`、`expression`，有提示时包含 `message`。
- `rule_passed`：规则返回 `true`。
- `rule_failed`：包含 `phase`、`rule_index`、`expression` 和 `error`。

规则失败发生在产物落盘前；输出规则失败时可能已经产生模型 Token 用量，但不会写入该 Agent 的 artifacts。

### 6.6 七种边与批次语义

一次 Agent 执行绑定一个不可变的 `batch_id`。普通边沿用当前批次；Fan-out 和循环边创建新批次：

| 边类型 | 触发与路由 | 批次 | 关键约束 |
| --- | --- | --- | --- |
| `PIPE` | 源 Agent 成功后直接传播 | 沿用当前批次 | 必须至少有一条 mapping |
| `CONDITIONAL` | 条件为真时传播 | 沿用当前批次 | 必须配置条件 |
| `FEEDBACK` | 条件满足时回注上游 | 新建 `loop-*` 批次 | 目标必须是源节点的上游祖先 |
| `LOOP` | 条件为真时继续回注，条件为假时退出 | 新建 `loop-*` 批次 | 必须有条件、目标必须是上游祖先 |
| `FAN_OUT` | 把同一源节点的多个并行出口路由到新批次 | 新建一次 `fanout-*` 批次，同一源节点的所有 Fan-out 出口共享 | 适合后续并行分支 |
| `AGGREGATE` | 按目标 Agent 和批次等待所有/任一聚合入边 | 沿用 fan-out 批次 | 目标不能同时混用普通入边；同一目标的模式必须一致 |
| `HUMAN_GATE` | 创建待办 Gate 并暂停该路径 | 批准后沿用原批次 | 拒绝和超时策略必须显式定义 |

`FEEDBACK` 与 `LOOP` 在运行时都使用新批次，并受边级 `guard.max_traversals` 限制。区别是 `LOOP` 强制要求继续条件，且未显式设置 guard 时继承 `loop_limit_default`；`FEEDBACK` 继承 `feedback_limit_default`。

`FAN_OUT` 示例：

```yaml
edges:
  - id: split-research
    from: planner
    to: researcher
    type: FAN_OUT
    mappings:
      - from: brief
        to: brief

  - id: split-review
    from: planner
    to: reviewer
    type: FAN_OUT
    mappings:
      - from: brief
        to: brief
```

两条 Fan-out 边由同一次 `planner` 执行传播时共享同一个 fan-out 批次。后端最多同时运行 `workflow.execution.max_parallel` 个 Agent。

`AGGREGATE` 示例：

```yaml
edges:
  - id: research-join
    from: researcher
    to: synthesizer
    type: AGGREGATE
    aggregate:
      mode: all
    mappings:
      - from: findings
        to: research

  - id: review-join
    from: reviewer
    to: synthesizer
    type: AGGREGATE
    aggregate:
      mode: all
    mappings:
      - from: review
        to: review
```

`all` 等待同一批次内所有 AGGREGATE 入边到达；`any` 在第一条入边到达后即调度目标 Agent。由于 `any` 不会等待其余分支，后到映射若复用同一端口，会以最后写入值为准。当前校验器尚未检查 Aggregate 映射是否覆盖目标 Agent 的全部必填输入，用户仍需补齐映射。

`HUMAN_GATE` 示例：

```yaml
edges:
  - id: production-approval
    from: reviewer
    to: publisher
    type: HUMAN_GATE
    mappings:
      - from: review
        to: approval_context
    human_gate:
      prompt: 请确认内容是否可以发布
      timeout_seconds: 300
      on_reject: fail
      on_timeout: fail
```

Gate 支持的策略：

- `on_reject: fail | skip`
- `on_timeout: fail | approve | reject`

批准后该边继续传播；`skip` 只停止该路径。若后续工作流输出依赖被跳过的路径，最终仍会因输出未生成而失败。

### 6.7 运行限额

限额分为三层：

| 配置 | 作用域 | 行为 |
| --- | --- | --- |
| `agents[].limits.max_tokens` | 单次模型请求 | 作为请求体的 `max_tokens` |
| `agents[].limits.max_total_tokens` | 单个 Agent 累计 | 每次返回用量后累计检查，超限使运行失败 |
| `workflow.execution.max_total_tokens` | 整次运行累计 | 所有 Agent 用量求和后检查，超限使运行失败 |
| `workflow.execution.max_steps` | 整次运行 | 每调度一次 Agent 执行计一步，达到上限后失败 |
| `workflow.execution.max_parallel` | 整次运行 | 同时运行的 Agent 任务上限，范围 `1-64` |

累计限额依赖供应商返回的 `usage`。运行时在调用完成后检查累计值，因此最后一次已发出的请求仍可能让总量略微超过配置值；这不是前端显示误差，而是“事后计量”的边界。

### 6.8 Agent JSON 输出配置

V0.3 为每个 Agent 提供独立的 JSON 输出配置：

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
- `TEXT` 端口的模板值必须是标量说明，不能写成对象或数组；需要保留层次化结果时，应把端口类型改为 `DATA`。
- `FILE` 端口的保存校验仍要求模板是带字符串 `path` 字段的对象，用于指导模型生成标准引用。
- Chat Agent 实际返回合法相对路径字符串时，运行时会先规范化为 `{"path": "..."}`，再执行工作区路径和文件引用安全检查；不会放宽越界路径限制。
- JSON 解析成功后，运行时仍按输出端口执行 `TEXT`、`DATA`、`FILE`、`MIXED` 类型检查。
- `schema_hint` 会在端口类型检查后继续校验，例如 `score` 的 `type/minimum/maximum`。

例如 `suggestion` 是 `TEXT` 端口时，错误模板与正确模板分别为：

```yaml
# 错误：对象输出会在运行时触发 Port 'suggestion' requires TEXT
suggestion:
  风格: 画风修改建议
  背景: 背景修改建议

# 正确：把综合建议作为一段文本返回
suggestion: 综合风格、背景、角色和质量维度的完整修改建议文本
```

保存或显式校验时，此类冲突会返回 `json_output_port_type_mismatch`，不需要等到运行阶段才发现。

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

### 6.9 API Profile 与 DeepSeek 配置示例

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

### 6.10 图片生成 Profile 与 Agent 配置

V0.3 通过图片供应商适配器接入不同图片服务。API Profile 顶层的
`provider: openai_compatible` 表示该 Profile 属于 OpenAI 风格配置族；
`image_generation.protocol` 进一步选择实际图片协议。未填写 `protocol` 时默认
使用 `automatic1111`，调用本机或内网 WebUI；需要使用 OpenAI Images 或
SiliconFlow 的配置应显式填写对应协议。

OpenAI Images Profile 示例：

```yaml
api_profiles:
  - id: openai-images
    name: OpenAI Images
    provider: openai_compatible
    base_url: https://api.openai.com/v1
    model: gpt-image-1
    request:
      timeout_seconds: 120
      max_retries: 2
      temperature: 0.2
    capabilities:
      function_calling: false
      vision: false
      long_context: false
      structured_output: false
      image_generation: true
    image_generation:
      protocol: openai_images
      size: 1024x1024
      quality: high
      output_format: png
      response_format: b64_json
```

SiliconFlow Profile 示例：

```yaml
api_profiles:
  - id: siliconflow-kolors
    name: SiliconFlow Kolors
    provider: openai_compatible
    base_url: https://api.siliconflow.cn/v1
    model: Kwai-Kolors/Kolors
    request:
      timeout_seconds: 120
      max_retries: 2
      temperature: 0.2
    capabilities:
      function_calling: false
      vision: false
      long_context: false
      structured_output: false
      image_generation: true
    image_generation:
      protocol: siliconflow
      size: 1024x1024
      quality: null
      output_format: null
      response_format: null
```

本地 AUTOMATIC1111 / Forge Profile 示例：

```yaml
api_profiles:
  - id: local-sd
    name: Local Stable Diffusion
    provider: openai_compatible
    base_url: http://127.0.0.1:7860
    model: local
    request:
      timeout_seconds: 300
      max_retries: 0
      temperature: 0.2
    capabilities:
      function_calling: false
      vision: false
      long_context: false
      structured_output: false
      image_generation: true
    image_generation:
      protocol: automatic1111
      size: 1024x1024
      quality: null
      output_format: null
      response_format: null
```

字段说明：

| 字段 | 作用 |
| --- | --- |
| `capabilities.image_generation` | 标记该 Profile 可供 Image Agent 使用 |
| `image_generation.protocol` | `openai_images`、`siliconflow` 或 `automatic1111`；缺省值为 `automatic1111` |
| `image_generation.size` | 请求图片尺寸，默认 `1024x1024`，最终以服务端支持范围为准 |
| `image_generation.quality` | OpenAI Images 可选质量值，留空时不向服务端发送 |
| `image_generation.output_format` | OpenAI Images 输出格式，可选 `png`、`jpeg` 或 `webp` |
| `image_generation.response_format` | OpenAI Images 偏好 `url` 或 `b64_json`，留空时使用服务端默认行为 |

`quality`、`output_format` 和 `response_format` 仅对 OpenAI Images 生效，
不会泄漏到 SiliconFlow 或 AUTOMATIC1111 请求中。

对应 Agent：

```yaml
- id: illustrator
  name: 封面绘制
  api_profile: openai-images
  mode: image
  system_prompt: 根据需求生成一张构图清晰、文字留白充足的编辑插画。
  inputs:
    - name: brief
      type: TEXT
      required: true
  outputs:
    - name: picture
      type: FILE
  json_output:
    enabled: false
    template: {}
```

OpenAI Images 适配器向 `POST {base_url}/images/generations` 发送：

```json
{
  "model": "gpt-image-1",
  "prompt": "由全局背景 Prompt、Agent System Prompt 和当前输入拼装",
  "size": "1024x1024",
  "quality": "high",
  "output_format": "png",
  "response_format": "b64_json"
}
```

SiliconFlow 适配器调用相同路径，但发送该供应商接受的字段：

```json
{
  "model": "Kwai-Kolors/Kolors",
  "prompt": "由全局背景 Prompt、Agent System Prompt 和当前输入拼装",
  "image_size": "1024x1024",
  "batch_size": 1
}
```

AUTOMATIC1111 / Forge 适配器调用本地 WebUI API：

```text
POST {base_url}/sdapi/v1/txt2img
```

```json
{
  "prompt": "由全局背景 Prompt、Agent System Prompt 和当前输入拼装",
  "negative_prompt": "",
  "width": 1024,
  "height": 1024,
  "steps": 28,
  "cfg_scale": 7,
  "sampler_name": "DPM++ 2M Karras",
  "seed": -1,
  "batch_size": 1,
  "n_iter": 1
}
```

`size` 会拆分为 `width` 和 `height`。实际 checkpoint、VAE 和采样器实现由
WebUI 当前加载的模型决定，Profile 的 `model` 字段不会切换 WebUI 模型。

本地部署要求与硬件建议：

1. AUTOMATIC1111 或 Forge WebUI 必须使用 `--api` 启动，例如
   `webui.bat --api --port 7860`。
2. `base_url` 填 WebUI 根地址，例如 `http://127.0.0.1:7860`，不要追加
   `/v1`、`/sdapi/v1` 或 `/sdapi/v1/txt2img`。
3. 本机回环地址和 `host.docker.internal` 被视为本地服务，因此不需要 API Key；
   仍必须创建 Profile 并启用 `capabilities.image_generation`。
4. 不要把启用 `--api` 且无认证的 WebUI 直接暴露到公网；远程部署应在反向代理层
   增加认证和访问控制。
5. 8 GB 显存适合常见的 1024 级生成；6 GB 可使用较小 checkpoint 或较低分辨率；
   4 GB 通常需要更低分辨率、优化参数或接受较慢速度。CPU 可以运行，但生成时间
   会显著增加。
6. 生成耗时可能远高于文本请求，建议把 Profile 的 `timeout_seconds` 设置为
   `120` 到 `300` 秒，并按本地服务稳定性配置重试次数。

三个适配器共享同一个运行流程：

1. 从环境变量读取与 Profile 绑定的 API Key。
2. 由适配器构造供应商请求。
3. 公共运行层处理超时、重试、429 和 5xx。
4. 由适配器从响应中提取 Base64、HTTP URL 或 data URL 图片；
   AUTOMATIC1111 的裸 Base64 图片也走同一套解码与校验。
5. 公共运行层执行大小限制、文件头校验、安全下载和 artifacts 落盘。

配置顺序：

1. 在 API 常用库或项目 Profile 中启用 `Image generation`，选择供应商协议并填写该协议支持的参数。
2. 保存项目和 Profile，再按普通 Profile 的方式配置 API Key。
3. 在 Agent 检查器把模式切换为 `Image`；界面会自动选择兼容 Profile、关闭 JSON 输出并整理为单个 `FILE` 输出。
4. 校验、生成并运行工作流，在结果与产物区域查看 `artifact_written`。

DeepSeek 文本模型不能用于图片生成。即使接口形态兼容 OpenAI，也必须确认目标模型实际支持对应图片接口，否则会返回 404 或参数错误。

新增图片供应商时，在生成运行时的
`backend/templates/generated/runners/image_providers.py.j2` 中增加适配器。适配器需要：

- 声明唯一的 `protocol` 标识。
- 实现 `build_request(agent, profile, inputs) -> ImageRequest`。
- 实现 `extract_image(payload, timeout) -> bytes`。
- 注册到 `_IMAGE_ADAPTERS`，并同步 Pydantic、前端联合类型和测试。

文件下载、安全边界、Token 记录和 artifacts 写入不需要在供应商适配器中重复实现。

### 6.11 数据变换配置

数据变换用于在模型调用前后，以及一条边把值送达目标端口前，对 JSON 数据做确定性清理和重组。它复用 Agent 规则的白名单 AST、独立子进程和资源边界，但返回值不是布尔值，而是严格 JSON。

项目 SDL 默认使用可读的 `fields` 模式。用户只填写输出路径和值表达式，不手写完整 JSON；后端在校验和编译时把字段表组装成安全沙箱可执行的 JSON 对象表达式。旧的 `expression` 写法继续支持，并作为需要任意 JSON 返回值时的兼容模式。

支持三个位置。Agent 输入和输出推荐使用 `fields`：

```yaml
agents:
  - id: writer
    input_transform:
      mode: fields
      fields:
        - path: brief
          value: str(inputs.brief)
        - path: feedback
          value: 'inputs.feedback if "feedback" in inputs else ""'
    output_transform:
      mode: fields
      fields:
        - path: article
          value: 'str(raw_outputs.title) + "\n\n" + str(raw_outputs.body)'
        - path: outline.sections
          value: raw_outputs.sections
    inputs:
      - name: brief
        type: TEXT
        required: true
      - name: feedback
        type: TEXT
        required: false
    outputs:
      - name: article
        type: TEXT

edges:
  - id: text-to-data
    from: collector
    to: analyzer
    type: PIPE
    mappings:
      - from: payload
        to: payload
        transform:
          expression: '{"text": value, "length": length(value)}'
```

上面的 Agent 字段表会在编译产物中分别组装为等价表达式：

```python
input_transform = '{"brief": str(inputs.brief), "feedback": inputs.feedback if "feedback" in inputs else ""}'
output_transform = '{"article": str(raw_outputs.title) + "\n\n" + str(raw_outputs.body), "outline": {"sections": raw_outputs.sections}}'
```

项目文件、项目 GET/PUT API 和 SDL 仍保留 `mode: fields` 与字段表，便于阅读和编辑；只有转发给运行时的 `runtime_dict` 会替换为 `expression`。因此 API 数据可读，旧运行模板也不需要理解前端字段结构。

字段表规则：

- `path` 必须是小写标识符组成的点分隔路径，最多 8 段，例如 `brief`、`outline.sections`。
- 单个变换最多 32 个字段；重复路径以及父字段与子字段同时出现会被拒绝，例如不能同时配置 `outline` 和 `outline.sections`。
- `value` 是当前位置上下文中的安全表达式，不是 JSON 文本。写 `raw_outputs.sections`，不要写成 `"raw_outputs.sections"`。
- `fields` 结果始终是 JSON 对象，符合 Agent 输入和输出变换的返回值要求。
- Edge mapping 界面保留高级 `expression`，因为它允许返回对象、数组、字符串或其他严格 JSON 值；API 仍可对 mapping 使用 `fields` 模式。
- 删除最后一个字段会清除整个变换项，不会提交空的 `fields` 配置。

需要完整控制表达式、返回非对象 JSON 或迁移已有项目时，继续使用高级模式：

```yaml
input_transform:
  expression: '{"brief": inputs.brief}'
```

未提供 `mode` 时，包含 `expression` 的旧配置自动按 `expression` 模式解释；新前端创建变换时默认使用 `fields` 模式。

字段语义：

- `agents[].input_transform`：在 Agent 已就绪并进入执行后，整体替换该次 Agent 的 `inputs`。返回值必须是 JSON 对象。
- `agents[].output_transform`：在收到并解析模型 JSON 后、端口与 `schema_hint` 校验及输出规则之前，整体替换 `outputs`。返回值必须是 JSON 对象。
- `edges[].mappings[].transform`：只变换当前 mapping 的来源端口值，再把结果写入该 mapping 的目标端口。返回值可以是任意严格 JSON 值，但随后必须满足目标端口类型和 `schema_hint`。
- 未配置变换时保持原有行为；三个字段均为可选，不要求旧项目迁移。
- Agent 变换不是“补字段”语法。调度器会先按实际已映射端口判断 Agent/批次是否就绪，然后才执行 `input_transform`；因此输入变换不能补齐从未送达的必填端口，但可以删除已有端口，随后由常规输入校验报错。

上下文根按执行位置隔离：

| 位置 | 可访问根 | 说明 |
| --- | --- | --- |
| Agent `input_transform` | `inputs`、`workflow`、`edges`、`run` | `inputs` 是进入 Agent 的当前端口对象 |
| Agent `output_transform` | `raw_outputs`、`inputs`、`workflow`、`edges`、`run` | `raw_outputs` 是模型刚返回并解析出的原始端口对象 |
| Edge mapping `transform` | `value`、`outputs`、`workflow`、`edges`、`run` | `value` 是当前来源端口值，`outputs` 是源 Agent 的完整输出 |

固定执行顺序：

1. 源 Agent 成功并满足出边条件后，逐条 mapping 执行 `transform`；结果写入目标批次端口。
2. 目标 Agent/批次满足实际端口就绪条件并进入执行后，先应用 `input_transform`。
3. 对变换后的输入执行必填、额外端口、类型和 `schema_hint` 校验，再执行 `input` 规则，最后调用模型。
4. 模型响应解析为对象后应用 `output_transform`。
5. 对变换后的输出执行端口、类型和 `schema_hint` 校验，再执行 `output` 规则，最后写入 artifacts 并传播出边。

严格 JSON 规则：

- 允许 `null`、布尔、数字、字符串、数组和字符串键对象；拒绝 tuple、set、非字符串键、`NaN`、`Infinity`、缺失值和自定义对象。
- 结果必须可由标准 JSON 表示；运行时拒绝循环引用、超过 64 层的嵌套、超过 20000 个值节点、超过 10000 项的集合和超过 1 MiB 的序列化结果。
- 字符串、整数、表达式长度、上下文大小、操作次数和超时继续使用 Agent 规则相同的资源上限。
- Edge transform 只跳过保存时的“源端口类型必须等于目标端口类型”检查；目标端口仍会在运行时校验最终值，不能用变换绕过 `TEXT`、`DATA`、`FILE` 的契约。
- 表达式不能执行任意方法。应使用 `str`、`int`、`float`、`length`、`upper`、`lower`、`contains` 等安全 helper；例如使用 `str(inputs.brief)`，不要写 `inputs.brief.strip()`。

常见用途：

- 输入清理：把可选字段补为空字符串，统一组合多个上游端口，或只保留模型需要的字段。
- 输出重组：把模型偏好的扁平 JSON 字段组合成目标端口要求的结构。
- 跨类型映射：把 `TEXT` 转换为结构化 `DATA`，或从 `DATA` 中提取一个字符串写入 `TEXT` 端口。
- 可追溯元数据：在目标 DATA 中加入 `workflow.inputs` 中的业务标识、当前 `run.step` 或边遍历次数。

每个实际执行的变换会发出 `transform_started`，成功时发出 `transform_passed`，失败时发出 `transform_failed`。事件数据包含 `scope`（`agent` 或 `edge`）、`phase`（`input`、`output` 或 `mapping`）、`expression`，边映射还包含从零开始的 `mapping_index`。

## 7. SDL 校验与编译

### 7.1 校验分两层

1. 结构校验：由 Pydantic 模型完成，检查字段、类型、枚举和版本。
2. 语义校验：检查引用、端口、图结构和安全边界。

语义校验至少覆盖：

- 所有 ID 和端口名合法且唯一。
- `start` 中的 Agent 存在。
- 每条边引用的 Agent 和端口存在。
- 边的映射源端口属于上游输出，目标端口属于下游输入。
- `PIPE` 不允许条件，`CONDITIONAL` 和 `LOOP` 必须配置条件。
- 条件 AST 的操作符和引用路径在允许范围内。
- 忽略 `FEEDBACK` 和 `LOOP` 边后不存在普通环。
- 每条 `FEEDBACK` 和 `LOOP` 边的目标必须是其源节点的上游祖先。
- 每条 `FEEDBACK`/`LOOP` 边必须有 `max_traversals`，或在 workflow 默认值存在时继承。
- `AGGREGATE` 配置只能出现在 `AGGREGATE` 边上，`HUMAN_GATE` 配置只能出现在 `HUMAN_GATE` 边上。
- 同一目标 Agent 的所有 `AGGREGATE` 入边必须使用相同的 `all` 或 `any` 模式。
- `AGGREGATE` 目标不能与其他类型的入边混用。
- `image` 模式 Agent 必须引用启用了图片能力的 Profile，并且只能有一个 `FILE` 输出。
- `image` 模式 Agent 不能同时启用 `json_output`。
- Agent 规则阶段合法，表达式符合白名单 AST，且 `input` 规则不能引用 `outputs.*`。
- Agent `input_transform`、`output_transform` 和边 mapping `transform` 必须符合各自的白名单上下文根；Agent 变换在运行时必须返回对象。
- Edge mapping `transform` 只放宽保存阶段的源/目标端口同类型检查，变换结果仍必须在运行时满足目标端口类型和 `schema_hint`。
- 正则类边条件通过长度、复杂度和危险语法检查。
- 所有节点均可从 `start` 到达；不可达节点作为错误处理。
- 工作流声明的输出引用存在。
- SDL 中不能出现 `api_key`、`secret`、`password` 等敏感字段。

校验器当前不检查 AGGREGATE 映射是否覆盖目标 Agent 的全部必填输入。该问题会在运行时就绪判断阶段表现为节点不执行，或在最终工作流输出缺失时失败；V0.3 通过文档约束和用户检查补齐，后续可增加静态覆盖检查。

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
   ├─ graph_runner.py
   └─ rule_sandbox.py
```

`compiler_key` 由 SDL 规范 JSON 的 SHA-256 和编译器版本共同生成。SDL 或编译器版本改变时自动重新生成。

生成代码约束：

- 不硬编码 API Key。
- 不生成任意代码执行入口。
- Prompt、端口和条件都通过 JSON 安全序列化写入配置。
- `graph_runner.py` 来自受控模板，不允许用户文本直接拼接到 Python 源码中。
- `rule_sandbox.py` 与主运行器一同生成，负责规则与数据变换的 AST 校验、白名单解释和隔离子进程入口；同一沙箱以 `boolean` 模式执行 Agent 规则，以 `json` 模式执行三类数据变换。
- `manifest.json` 记录 SDL 哈希、编译器版本和生成文件列表，不写入运行时间等非确定性字段。
- 生成项目只依赖 Python 标准库和 `httpx`。

## 8. 运行时设计

### 8.1 运行状态

```text
RunState
├─ run_id
├─ status: queued | running | waiting | succeeded | failed | cancelled
├─ step
├─ inputs[agent_id][port]
├─ input_batches[agent_id][batch_id][port]
├─ outputs[agent_id][port]
├─ scheduled_batches
├─ aggregate_arrivals
├─ edge_traversals[edge_id]
├─ usage / agent_usage
├─ artifacts
├─ pending
├─ waiting_gate_ids
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

V0.3 使用批次感知的并行就绪队列：

1. 用 workflow inputs 初始化所有 `start` Agent 的 `batch-start` 输入。
2. 调度器在 `pending` 队列非空时按 SDL Agent 顺序填充可运行任务，最多同时运行 `max_parallel` 个 Agent。
3. 每次调度 Agent 时 `step` 加一并发送 `node_enter`；完成后保存输出、累计 Token、写产物，再发送 `node_exit`。
4. Agent 成功后按 SDL 中的边顺序计算所有出边；每条边先判断通用条件，再逐条 mapping 执行可选的边变换。
5. 边变换成功后，普通边把转换结果写入当前批次；`FAN_OUT` 为本次源节点执行创建新的 `fanout-*` 批次；`FEEDBACK`/`LOOP` 创建新的 `loop-*` 批次。
6. 目标 Agent/批次只有同时满足必填输入和聚合条件时才会进入 `pending`；`scheduled_batches` 保证同一 Agent 的同一批次只调度一次。
7. `HUMAN_GATE` 不占用 Agent 执行槽位。它生成 Gate 文件、发出等待事件，并在独立 Gate 线程池中等待人工决定或超时。
8. 没有运行中的 Agent、没有待决 Gate，且 `pending` 为空时，运行进入结果汇总。

关键规则：

- 多个必填输入端口是 join 关系，等待全部满足。
- 条件未触发的边不会阻塞下游。
- 可选输入不参与就绪判断。
- 进入新批次时会复制目标 Agent 上一批次输入作为基线，再覆盖本边映射，因此 Loop/Feedback 重入可携带原始上下文。
- 同一批次内同名端口再次收到值时，以最新值为准。
- Edge mapping `transform` 在目标端口写入之前执行；变换失败时不调度下游，也不发出该边的 `edge_traversed`。
- Edge mapping `transform` 可用于 `TEXT`、`DATA`、`FILE` 之间的显式转换，但目标 Agent 执行前仍会按目标端口类型与 `schema_hint` 校验。
- Agent 就绪判断先于 `input_transform`；输入变换不能补齐从未被调度器映射的必填端口。
- `FAN_OUT` 让多个下游 Agent 获得同一个新批次，从而可以并行执行。
- `AGGREGATE all` 等待同一批次的所有聚合入边；`AGGREGATE any` 在第一条入边到达后调度，后续同批次入边不会再次启动目标 Agent。
- 同一 Agent 在一次运行中可以有多次执行，例如不同 Fan-out 批次或 Loop/Feedback 批次。
- `FEEDBACK`/`LOOP` 边每传播一次，`edges.{edge_id}.traversals` 加一；超过 guard 上限后运行失败。
- 全局 `max_steps` 和运行超时是第二道保护。
- Agent 或 Gate 失败时默认 fail-fast，取消尚未开始的任务并终止子进程树。
- 并行的状态更新集中在主调度循环处理；Agent 线程只返回结果，不直接修改共享图状态。

### 8.3 Agent 调用

单个 Agent 的执行顺序固定为：

1. 发出 `node_enter`。
2. 若配置了 `input_transform`，以当前 `inputs`、工作流输入、边遍历计数和运行信息为上下文执行表达式，并整体替换输入；发出 `transform_started`、`transform_passed` 或 `transform_failed`。
3. 对变换后的输入执行必填、额外端口、`TEXT`/`DATA`/`FILE`/`MIXED` 与 `schema_hint` 校验。
4. 执行全部启用的 `input` 规则；失败时不发起模型请求。
5. 调用 Chat 或 Image Agent。
6. 模型服务返回 usage 后发出 `token_usage`；该事件不表示输出契约校验或输出变换成功。
7. 若配置了 `output_transform`，以模型刚解析出的 `raw_outputs`、变换后的输入、工作流输入、边遍历计数和运行信息为上下文执行表达式，并整体替换输出。
8. 校验输出端口必须存在、没有额外端口，并执行 `TEXT`/`DATA`/`FILE`/`MIXED` 与 `schema_hint` 校验。
9. 执行全部启用的 `output` 规则。
10. 将输出作为 artifacts 落盘并发出 `artifact_written`。
11. 发出成功的 `node_exit`。

因此输入变换、输入校验或输入规则失败时不会产生模型 Token；模型调用成功后即使输出变换或输出规则失败，日志中仍会出现 `token_usage`，但不会出现该 Agent 的 artifacts。输入变换可以删除已有端口并触发后续必填校验，但不能让调度器重新判断一个从未满足就绪条件的批次。

#### Chat Agent 调用

每个 Chat Agent 请求包含：

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
3. 应用可选的 `output_transform`，用变换结果整体替换输出；未配置时保留解析结果。
4. 校验最外层键与 Agent 输出端口完全一致。
5. 校验每个端口的 `TEXT`、`DATA`、`FILE`、`MIXED` 类型。
6. 校验端口上的 `schema_hint`。
7. 执行 Agent 的输出规则并及时记录 `token_usage`，然后把输出写入运行时状态。

因此 `Port 'outline' does not satisfy schema_hint` 表示模型已经返回了 `outline`，但值不满足端口声明的 JSON Schema 子集，例如声明为 `object` 却返回了字符串，或 `score` 超出 `minimum/maximum`。

超时和重试只覆盖网络错误、429 和 5xx。4xx 参数错误、JSON 解析失败和端口校验失败不做无限重试。

#### 图片 Agent 调用

`mode: image` 的 Agent 不调用 `/chat/completions`。运行时根据
`profile.image_generation.protocol` 选择图片适配器；当前 OpenAI Images 和
SiliconFlow 调用：

```text
POST {base_url}/images/generations
```

AUTOMATIC1111 / Forge 调用：

```text
POST {base_url}/sdapi/v1/txt2img
```

请求 Prompt 由全局背景 Prompt、Agent System Prompt 和当前输入 JSON 组成。当前输入中的 `TEXT`、`DATA` 会序列化为文字提示；`FILE` 只作为工作区内路径引用传入，不会自动上传或读取为二进制图片。因此 V0.3 是“根据工作流上下文生成图片”，不是图生图编辑。

响应处理顺序：

1. 校验 HTTP 状态与响应根对象。
2. 适配器提取图片引用。OpenAI Images 读取 `data[0]`；SiliconFlow 优先读取 `images[0]`，并兼容 `data[0]` 响应；AUTOMATIC1111 解码 `images[0]` 中的裸 Base64。
3. 图片项支持 `b64_json`、HTTP(S) URL 和 `data:image/...;base64,...`。外部 URL 必须解析到公网地址，并禁止重定向。
4. 单张图片最大 `20 MB`。
5. 通过文件头识别 PNG、JPEG 或 WebP，拒绝伪装成图片的任意二进制数据。
6. 写入 `runs/{run_id}/workspace/generated/images/{agent_id}/{uuid}.{ext}`。
7. 返回 `{ "path": "...", "media_type": "..." }` 给 Agent 的 `FILE` 输出端口。
8. 现有 artifacts 逻辑继续把图片复制到端口配置的 `output_dir`，并发出 `artifact_written`。

图片接口若返回 `usage`，运行时按 `prompt_tokens`、`completion_tokens`、`total_tokens` 累加，并受 Agent 与 Workflow 累计 Token 上限约束。网络错误、429 和 5xx 使用 Profile 的 `max_retries`；参数错误、非法图片和 URL 安全校验失败不重试。供应商差异只存在于“构造请求”和“提取图片引用”，其余行为由公共运行层统一实现。

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
- Gate 文件目录通过 `LOOMLANE_GATE_DIR` 指向 `runs/{run_id}/gates/`，服务端与子进程通过该目录交换人工决定。
- 服务端异步读取 stdout，逐行校验事件、追加到 `events.jsonl`，并广播给 SSE 订阅者。
- 用户取消运行时，服务端终止整个子进程树。

API 侧同一项目默认只允许一个活动运行，避免生成物或工作区相互污染。

### 8.5 人工确认状态机

Gate 文件名由运行时生成：`gate-{edge_id}-{counter}.json`。等待期间记录：

```json
{
  "gate_id": "gate-production-approval-1",
  "run_id": "...",
  "edge_id": "production-approval",
  "from": "reviewer",
  "to": "publisher",
  "batch_id": "batch-start",
  "prompt": "请确认内容是否可以发布",
  "requested_at": "2026-11-19T02:00:00+00:00",
  "timeout_seconds": 300,
  "decision": null,
  "comment": null
}
```

状态转换：

```text
waiting
  -> approve  -> edge_traversed -> 下游继续
  -> reject   -> on_reject=fail  -> run failed
              -> on_reject=skip  -> 当前路径结束
  -> timeout  -> on_timeout=fail | approve | reject
```

服务端使用短文件锁和原子替换避免“人工批准”和“超时写回”同时改写同一个 Gate。Gate 已决定后再次提交返回冲突；运行未处于 `waiting` 或 Gate 不存在时分别返回 `409`、`404`。

运行存在一个或多个待决 Gate 时，`meta.json` 的 `status` 为 `waiting`。所有 Gate 都解决后，若仍有 Agent 任务则回到 `running`，否则进入最终状态。运行详情会从 Gate 文件重建 `pending_gates`，刷新页面不会丢失待办。

### 8.6 产物自动落盘与运行结果

Agent 流生成物的位置分为三类：

- 编译产物：`workspace/projects/{project_id}/generated/{compiler_key}/`，包含 `workflow.py`、`config.py`、Agent 配置和运行时模板。
- 运行数据：`workspace/projects/{project_id}/runs/{run_id}/`，包含 `meta.json`、`input.json`、`events.jsonl`、`result.json`、`gates/` 和 `workspace/`。
- Agent 产物：每个成功输出端口都会自动写入运行工作区，默认目录为 `artifacts/`，也可由 `agents[].output_dir` 指定相对目录。
- 图片源文件：图片 Agent 先写入 `workspace/generated/images/{agent_id}/`，再由统一 artifacts 逻辑复制到目标产物目录。

自动落盘规则：

| 端口类型 | 扩展名 | 内容 |
| --- | --- | --- |
| `TEXT` | `.txt` | 原始文本 |
| `DATA` | `.json` | 格式化 JSON |
| `FILE` | 保留源文件扩展名 | 从工作区内的合法源文件复制 |
| `MIXED` | `.txt` | 字符串化后的值 |

文件名格式为：

```text
{step:05d}-{batch_id}-{agent_id}-{port_name}{extension}
```

`step` 和 `batch_id` 使循环、反馈和并行批次不会互相覆盖。路径中的文件名片段会做安全字符替换。

安全规则：

- `output_dir` 必须是相对路径，不能是绝对路径、盘符路径或包含 `..`。
- `FILE` 源路径必须解析到 `runs/{run_id}/workspace/` 内且文件存在。
- 所有路径在写盘前解析并检查仍位于工作区根目录内。

每次落盘会发出 `artifact_written` 事件，包含 `port`、`path`、`type`、`step`、`batch_id` 和 `bytes`。文件路径相对运行工作区，前端会显示为 `runs/{run_id}/workspace/{path}`。

图片 Agent 的 `workflow_outputs.picture` 与 `agent_outputs.{agent_id}.picture` 指向 `generated/images/...` 下的图片源文件；`artifacts` 列表指向按 step、batch 命名后的最终产物副本。排查时两者都可以打开，前者是本次生成原图，后者是统一产物命名后的交付文件。

`result.json` 的当前结构为：

```json
{
  "run_id": "...",
  "status": "succeeded",
  "workflow_hash": "...",
  "workflow_outputs": {},
  "agent_outputs": {},
  "agent_usage": {},
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "steps": 0,
  "edge_traversals": {},
  "artifacts": [],
  "error": null
}
```

`workflow_outputs` 是按 `workflow.outputs` 映射后的最终结果；`agent_outputs` 保留各 Agent 最新一次成功输出。所有批次共享 `agent_outputs`，因此早期批次会被后续同 Agent 输出覆盖。完整历史应结合 `events.jsonl`、`artifacts` 列表和文件名中的 `step`/`batch_id` 排查。

运行目录是排查“产物在哪里”和“为什么失败”的第一入口：先看 `events.jsonl` 定位最后成功事件和错误事件，再看 `result.json` 的 `workflow_outputs`、`agent_outputs`、`usage`、`artifacts` 和 `error`。

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

`seq` 在一次运行内单调递增。V0.3 事件类型为：

- `run_started`
- `node_enter`
- `node_exit`
- `edge_traversed`
- `token_usage`
- `rule_started`
- `rule_passed`
- `rule_failed`
- `transform_started`
- `transform_passed`
- `transform_failed`
- `artifact_written`
- `human_gate_waiting`
- `human_gate_resolved`
- `error`
- `run_finished`

关键事件数据：

| 事件 | 关键 data |
| --- | --- |
| `node_enter` / `node_exit` | `batch_id`、`step`、节点状态 |
| `edge_traversed` | `from`、`to`、`mapped_ports`、`batch_id` |
| `token_usage` | `prompt_tokens`、`completion_tokens`、`total_tokens` |
| `rule_started` / `rule_passed` | `phase`、`rule_index`、`expression`，可选 `message` |
| `rule_failed` | `phase`、`rule_index`、`expression`、`error`，可选 `message` |
| `transform_started` / `transform_passed` | `scope`、`phase`、`expression`，Edge mapping 另含从零开始的 `mapping_index` |
| `transform_failed` | `scope`、`phase`、`expression`、`error`，Edge mapping 另含 `mapping_index` |
| `artifact_written` | `port`、`path`、`type`、`step`、`batch_id`、`bytes` |
| `human_gate_waiting` | `gate_id`、`prompt`、`timeout_seconds` |
| `human_gate_resolved` | `gate_id`、`decision`、`comment`、`timed_out` |
| `error` | `type`、`message` |
| `run_finished` | `status`，失败时含 `error` |

事件只能追加，不能原地修改。

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

### 9.4 运行回放

前端把 SSE 收到的事件和从 `GET /api/runs/{run_id}` 补齐的历史事件做两件事：

1. 按 `seq` 去重并升序排序，避免重连补发造成重复计数。
2. 回放到用户选择的 `untilSeq`，用纯函数从空状态重新推导节点状态、边遍历次数、Token、Gate 和 artifacts。

回放能力：

- 回到开始、上一步、下一步、播放/暂停、跳到实时末尾。
- 任意序号滑块。
- `0.25s`、`0.5s`、`1s`、`2s` 四档播放间隔。
- 回放期间只改变视图，不再次调用模型，不再次写文件，也不能操作历史 Gate。
- 只有回到最新事件且运行确实处于 `waiting` 时，人工确认按钮才可用。

事件类型到视图状态的映射：

- `node_enter` -> `running`
- `node_exit` -> `succeeded` / `failed` / `skipped` / `cancelled`
- `edge_traversed` -> 该边遍历次数加一
- `token_usage` -> 运行总量和节点分量累加
- `rule_started` / `rule_passed` -> 保留在事件时间线，用于定位 Agent 已执行到哪条规则
- `rule_failed` -> 事件时间线展示规则失败详情，节点最终由 `error` 收敛为失败
- `transform_started` / `transform_passed` -> 保留在事件时间线，用于定位 Agent 或边已执行到哪个变换阶段
- `transform_failed` -> 事件时间线展示变换表达式和错误，后续由运行错误收敛为失败
- `artifact_written` -> artifacts 列表追加
- `human_gate_waiting` -> Gate 进入 `waiting`
- `human_gate_resolved` -> Gate 进入 `resolved`
- `error` -> 失败节点和错误信息
- `run_finished` -> 写入终态

## 10. 前端编辑器

### 10.1 页面结构

V0.3 只有一个实际工作界面：

- 中央画布。
- 左侧项目、运行输入、Gate 待办和运行历史。
- 右侧选择项、Workflow 执行配置、API Profile 配置。
- 底部 SDL、校验问题、事件回放和结果/产物。

不需要额外 landing page。首次打开直接进入最近项目或新建项目界面。

### 10.2 画布交互

- 从节点库拖入 Agent 节点。
- 节点间连线后选择边类型。
- 点击节点编辑名称、Chat/Image 模式、Prompt、输入输出端口、API Profile 和 JSON 输出。
- 点击边编辑类型、映射、通用条件、Feedback/Loop guard、Aggregate 模式和 Human Gate 策略。
- 选中 Agent 或边后，可用 `Delete`/`Backspace` 或检查器中的删除按钮移除。
- 删除 Agent 时，同时移除它的 `start` 引用、关联边、工作流输出和画布元素。
- 删除边时，同时移除 SDL 边和 `canvas.json` 中的布局记录。
- 支持 SDL 导入、导出和只读预览。
- 导入 SDL 时，如果没有对应 `canvas.json`，前端使用确定性自动布局生成节点位置，再要求用户确认保存。
- 校验结果显示在具体节点或边附近，同时提供问题列表。

### 10.3 三块面板收纳与调整

- 左侧导航、右侧检查器和底部抽屉都可以收纳，再次点击入口恢复。
- 左侧面板展开时支持拖拽右侧分隔条调整宽度，范围为 `220-420px`，默认 `270px`。
- 右侧检查器展开时支持拖拽左侧分隔条调整宽度，范围为 `300-560px`，默认 `360px`。
- 底部抽屉展开时支持拖拽顶部分隔条调整高度，范围为 `180-520px`，默认 `260px`。
- 三个分隔条都可获得键盘焦点，使用方向键微调；`Home`/`End` 跳到允许的最小/最大尺寸。
- 收起状态和宽度分别持久化到 `localStorage`：
  - `loomlane.left-panel.open`
  - `loomlane.left-panel.width`
  - `loomlane.inspector.open`
  - `loomlane.inspector.width`
  - `loomlane.drawer.open`
  - `loomlane.drawer.height`
- 移动端不显示拖拽分隔条，面板按单列布局展示，避免压缩画布。

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
- 谓词支持比较、范围、成员、前后缀、正则、空值、长度和集合运算；完整列表见 6.4。
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

### 10.7 Agent Python 规则编辑器

- 每个 Agent 检查器提供独立的“Python 规则”区域，默认不创建规则。
- 规则卡可启用/停用、切换 `input`/`output` 阶段、编辑表达式和失败提示。
- 新建规则使用安全表达式 `True`，不会默认引用尚不存在的 `outputs`。
- 编辑器只负责草稿保存；表达式是否合法在项目保存和运行时校验阶段判断。
- 规则按数组顺序执行并生成 `rule_started`、`rule_passed`、`rule_failed` 事件。

### 10.8 数据变换编辑器

- Agent 检查器提供“输入变换”和“输出变换”两个可选编辑器；未添加时不改变原有行为。
- 新增变换默认进入 `fields` 字段模式，以“输出路径 + 值表达式”的表格编辑，不再要求用户手写完整 JSON。
- 字段模式支持任意数量的点分层级路径，例如 `outline.sections`；后端负责组装嵌套 JSON 对象。
- 新建输入变换时根据 Agent 第一个输入端口生成 `inputs.<port>`，新建输出变换时根据第一个输出端口生成 `raw_outputs.<port>`。
- 字段模式可一键转换为高级表达式；转换结果只用于继续精细编辑，不会删除原有字段模式能力。
- 删除最后一个字段会清除整项变换，避免产生后端不接受的空字段表。
- 输入变换提示可访问 `inputs`、`workflow`、`edges`、`run`；输出变换额外提供 `raw_outputs`。
- Edge 检查器的每条 mapping 使用一个 `Braces` 图标入口添加或清除变换，避免和映射行操作重复。
- Mapping 变换提示可访问 `value`、`outputs`、`workflow`、`edges`、`run`；同一个 mapping 只显示一个变换编辑区。
- Edge mapping 默认进入高级表达式模式，并显式保存 `mode: expression`；这是为了保留任意严格 JSON 返回值的兼容能力。
- 字段值默认直接引用端口，不调用字符串方法；需要字符串化时使用安全 helper `str(...)`。
- 高级模式仍显示完整文本表达式，用于兼容已有项目和无法用对象字段表表达的 Edge 变换。
- 编辑失败或保存失败时保留当前草稿；保存项目和运行项目时由后端执行白名单 AST 校验，运行期错误通过 `transform_*` 事件展示。
- 复制 Agent 时深拷贝输入与输出变换，后续编辑副本不会修改原 Agent。

### 10.9 图片配置界面

- API 常用库和项目内 API Profile 都提供 `Image generation` 能力开关。
- 启用后显示尺寸、质量、输出格式和 API 响应格式。质量与格式可以为空，表示使用供应商默认值。
- Agent 模式切换为 `Image` 时，前端自动寻找兼容 Profile、关闭 JSON 输出，并把输出整理成单个 `FILE` 端口。
- Image 模式的 Profile 下拉会禁用未启用图片能力的 Profile，并在当前选择不兼容或输出契约错误时显示行内提示。
- 切回 `Chat` 时，如果当前只有一个 `FILE` 输出，会转换为 `TEXT`；其他端口结构保持不变。
- 图片大小、质量和格式最终由目标服务校验，前端字段不会代替供应商能力表。

### 10.10 运行态

节点状态：

- `idle`
- `waiting`
- `ready`
- `running`
- `succeeded`
- `failed`
- `skipped`
- `cancelled`

运行状态：

- `queued`
- `running`
- `waiting`
- `succeeded`
- `failed`
- `cancelled`

边状态区分未执行、已遍历、条件和错误。运行期间高亮当前节点和已遍历边；运行结束后保留最终状态，直到用户开始下一次运行或手动清除。

Gate 待办：

- 左侧运行区显示所有 `pending_gates`。
- 每张卡片显示边 ID、提示语、超时时间、可选评论、通过和拒绝按钮。
- 回放未处于运行末尾时禁用操作，避免用户误以为正在修改历史状态。

事件回放：

- 事件页顶部提供回放工具条和进度滑块。
- 事件列表只显示当前回放序号之前的事件。
- 节点、边、Token、Gate 和 artifacts 都按同一回放进度同步重建。

结果与产物：

- 结果页左侧展示 `result.json`。
- 右侧展示当前回放进度下的 artifacts，路径、Agent、批次、类型和字节数可见。
- “生成文件”折叠区展示最近一次编译生成的文件列表。

底部抽屉展示 SDL、校验问题、运行事件和最终结果。运行事件不能修改当前编辑草稿，避免执行状态污染尚未保存的工作流。

## 11. HTTP API

```text
GET    /api/health

GET    /api/api-profiles
POST   /api/api-profiles
PUT    /api/api-profiles/{profile_id}
DELETE /api/api-profiles/{profile_id}
GET    /api/api-profiles/{profile_id}/secret/status
PUT    /api/api-profiles/{profile_id}/secret
DELETE /api/api-profiles/{profile_id}/secret
POST   /api/api-profiles/{profile_id}/secret/import-from-project

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
GET    /api/projects/{project_id}/runs
GET    /api/runs/{run_id}
GET    /api/runs/{run_id}/events
POST   /api/runs/{run_id}/gates/{gate_id}
POST   /api/runs/{run_id}/cancel
```

后端通过 Pydantic 自动生成 OpenAPI，前端可以从 schema 生成类型。项目更新响应必须返回新 revision；前端保存失败时提示重新加载 SDL 或重新应用本地更改。

关键响应语义：

- 全局 API Profile 元数据持久化在数据目录的 `api-profiles.json`，不属于任何单个项目；项目 SDL 中的 Profile 可通过 `library_id` 引用全局 Profile，但密钥始终不进入该文件。
- 全局 Profile 的 `DELETE` 删除元数据和对应凭据库记录，不会批量改写项目 SDL；如果项目仍在引用被删除的 `library_id`，该项目的凭据状态会变为未配置，前端必须提示用户重新绑定或配置项目级密钥。
- `secret/import-from-project` 只复制兼容来源下的项目密钥到全局 Profile，不把明文密钥返回给前端。
- 项目 `DELETE` 删除整个本地项目目录，属于不可撤销操作，前端必须二次确认。
- 密钥 `DELETE` 只删除系统凭据库和 DPAPI 回退中的对应记录，不修改 SDL。
- Profile 删除没有独立服务端资源；它是在 SDL 更新中删除 `api_profiles` 项，并同步重写引用它的 Agent。
- `POST /api/projects/{project_id}/runs` 返回 `202` 和初始 `queued` 运行摘要；同一项目已有活动运行时返回 `409`。
- `GET /api/projects/{project_id}/runs` 按创建时间倒序返回该项目的运行列表，供运行选择器和回放入口使用。
- 运行状态包含 `queued`、`running`、`waiting`、`succeeded`、`failed`、`cancelled`。Gate 尚未全部处理时，运行保持 `waiting`。
- Gate 决策请求体为 `{"decision":"approve|reject","comment":"..."}`。Gate 不存在返回 `404`；运行已结束、当前不在等待 Gate、Gate 已决策或并发决策锁超时返回 `409`。
- 事件接口是 SSE。客户端可用 `?after=<seq>` 或 `Last-Event-ID` 从指定序号之后续传；已完成运行会直接回放 `events.jsonl` 中的历史事件后关闭连接。
- 请求体被 Pydantic 拒绝时返回 `422`。响应中的 `loc` 会指出具体字段，例如 `["body","sdl","agents",0,"json_output"]`。
- Pydantic 模型使用 `extra="forbid"`。前端提交了后端模型不存在的字段时，错误类型为 `extra_forbidden`；这通常表示后端进程仍在运行旧代码。

## 12. 安全与可靠性

- 不允许用户提交任意 Python、Jinja2 或系统命令。Agent 规则仅执行经过 AST 白名单校验的表达式子集。
- 条件求值器使用白名单 AST，不调用 `eval`。
- Agent 规则不使用 `eval`/`exec`，不开放 import、任意函数调用、属性写入、赋值和控制流语句。
- Agent 规则在 `python -I -S` 独立子进程中运行，使用临时工作目录、最小化环境变量、JSON 输入输出和 `2s` 默认超时。
- 规则同时受表达式长度、AST 节点数、AST 深度、上下文字节数、操作数、字符串长度、集合长度和整数位宽限制。
- 正则谓词禁止分组、lookaround、内联 flag 和反向引用，并限制 pattern、目标文本和量词大小。
- YAML 使用 `safe_load` 并设置输入大小上限。
- 项目 ID、Gate ID 和产物路径都做格式与根目录校验；拒绝绝对路径、盘符路径、`..` 和解析后逃逸工作区的路径。
- `FILE` 产物只允许复制运行工作区内的普通文件；目标目录 `output_dir` 必须是工作区内的相对路径。
- 图片 URL 下载禁止重定向，拒绝内网、回环、链路本地和其他非公网地址，并忽略 URL 中的用户名密码。
- 图片响应支持 PNG、JPEG、WebP，单张最大 `20 MB`；Base64 预览和解码后尺寸都会检查，避免无界内存占用。
- 图片文件必须先写入运行工作区，再经过现有 `FILE` 路径检查和 artifacts 复制流程。
- 子进程只继承必要环境变量。
- 运行管理器最多保留 `10,000` 条事件，单条事件最多 `1,000,000` 字节，stderr 最多保留 `128,000` 字节。
- 单次运行默认超时 `3,600` 秒，并受 SDL `limits.max_steps` 约束；子进程退出后统一收敛为成功、失败或取消状态。
- Agent 累计 Token 和 Workflow 累计 Token 都在模型调用返回 usage 后检查。限额检查可以阻止后续调用，但触发超限的那一次调用可能使实际累计值略高于配置值。
- Gate 决策使用独占锁文件 `gate-<id>.lock` 和原子 JSON 替换，防止两个审批请求同时写入同一 Gate；锁等待超时返回冲突。
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
| 后端 pytest | 131 passed, 1 warning |
| 前端 Vitest | 36 passed |
| 前端生产构建 | 通过 |
| 浏览器检查 | 本轮未执行；当前仓库未安装 Playwright |

唯一后端警告是 Starlette/httpx 测试客户端的弃用提示，不影响本轮测试结果。生产构建存在 Vite 的大 chunk 提示，属于当前单页编辑器包体积提醒，不是构建错误。

### 13.2 后端已覆盖

- SDL 结构校验、错误路径和 `extra="forbid"` 行为。
- 端口类型兼容、普通环、Feedback/Loop 祖先规则和 Aggregate 入边一致性。
- 条件 AST 安全求值、非法路径和非法操作符拒绝。
- `between`、字符串前缀/后缀、长度、空值、集合包含和受限正则条件。
- 正则 pattern/target 长度、重复次数、分组、lookaround、内联 flag 和反向引用限制。
- Agent 输入/输出规则的语法白名单、允许根、非布尔结果拒绝和资源上限。
- 规则隔离子进程执行、超时处理，以及 `rule_started`/`rule_passed`/`rule_failed` 事件。
- 输入规则失败阻止模型调用；输出规则失败发生在 Token 计量之后、产物落盘之前。
- Agent 输入/输出变换与 Edge mapping 变换的上下文根隔离、严格 JSON 结果和跨端口转换。
- 输入变换在校验和模型调用前执行；输出变换在 Token 计量之后、输出校验和 artifacts 之前执行。
- Edge mapping 变换在目标端口写入前执行，失败时不调用下游且不发出 `edge_traversed`。
- 严格 JSON 拒绝 tuple、set、非字符串键、`NaN`、`Infinity`、循环引用、`MISSING` 和超出资源上限的结果。
- 编译模板生成的独立 `rule_sandbox.py` 可在 JSON 模式执行，保持源码模板与后端沙箱行为一致。
- 输入 join、条件分支、Feedback/Loop 重入与 guard 超限。
- Agent JSON 输出的 `response_format` 开关、端口校验和尾随文本提取。
- Fan-out 分支并行执行、批次传播、Aggregate `all` 等待全部分支和 `any` 的批次级单次执行。
- 多 Gate 等待、批准/拒绝、超时决策和迟到审批冲突。
- Agent/Workflow 累计 Token 上限，以及失败后的运行状态收敛。
- `TEXT`、`DATA`、`FILE` 自动落盘、自定义输出目录和路径逃逸拒绝。
- 系统凭据库、DPAPI 回退、原子写入、origin 绑定和删除行为。
- 全局 API Profile CRUD、项目级/全局密钥、Profile 导入和删除行为。
- 图片 Profile 的 `capabilities.image_generation`、`image_generation.protocol` 与供应商参数可通过 API 创建、读取和更新。
- 图片 Agent 的校验规则、Base64 图片保存、非图片内容拒绝和非公网 URL 拒绝。
- OpenAI Images、SiliconFlow 与 AUTOMATIC1111 适配器分别生成对应请求字段，均不误调用 `/chat/completions`，并生成 `FILE` artifacts。
- SiliconFlow `images[0].url` 返回 data URL 时可解码、校验并落盘；OpenAI-only 参数不会泄漏到 SiliconFlow 请求。
- AUTOMATIC1111 本地 `txt2img` 请求包含宽高、采样参数和批次参数，裸 Base64 响应可解码、校验并落盘，本地回环地址无需 API Key。
- 项目创建、保存、revision、导入、生成、运行、运行列表、Gate 决策和删除 API。
- 生成项目哈希稳定性和密钥不进入生成物。

### 13.3 前端已覆盖

- SDL 与画布状态互转。
- API Profile 删除、Agent 自动迁移和唯一 Profile 阻止删除。
- 左、右、底部面板收纳、尺寸边界和 UI 状态持久化。
- 条件谓词追加和多层组合。
- Agent 输入/输出规则的新增、删除、启停和表达式编辑。
- Agent 输入/输出变换编辑、Edge mapping 单入口变换编辑和复制 Agent 时的变换深拷贝。
- 增强条件运算符及正则配置的类型映射。
- 七种边的编辑器配置、删除确认和 Gate/回放相关状态映射。
- 事件回放的时间线裁剪、节点/边/Token/Gate/artifacts 同步重建和进度边界。
- 关键类型转换和安全默认值。
- JSON 模板编辑使用稳定行 key，避免字段重命名导致失焦。
- 图片能力默认值仍保持 Chat 兼容，新增 Profile 和 Agent 数据结构不破坏既有工作流工具。

### 13.4 手工与后续测试

- 已手工创建临时项目，验证包含 `json_output` 的 SDL 可以保存、重新读取并删除。
- 已通过浏览器连续输入 JSON 字段，确认输入框不再因行 key 变化而失焦。
- 本轮未重新执行浏览器视觉检查；补充 Playwright 后应覆盖桌面和窄屏下的三面板收纳、Gate 审批、回放拖动和 artifacts 展示。
- 后续应将“创建项目 -> 配置 Profile -> 连线 -> 校验 -> 生成 -> 运行 -> Gate -> 回放 -> 查看产物”固化为 CI 全流程测试。

## 14. 实施状态

| 阶段 | 状态 | 已落地内容 |
| --- | --- | --- |
| A：契约和基础工程 | 已完成 | 前后端工程、SDL 模型、示例项目、校验器、测试基础 |
| B：编译与无界面运行 | 已完成 | Jinja2 生成、OpenAI Chat/Image 调用、批次感知并行调度、Loop/Feedback guard、规则/变换沙箱、事件输出 |
| C：API 与本地持久化 | 已完成 | 项目 CRUD、revision、原子写入、密钥存储、编译缓存、SSE、取消运行 |
| D：可视化编辑器 | 已完成 | React Flow、七种边配置、三面板收纳/调整、条件树、JSON/Image 配置、输入/输出与边 mapping 变换编辑、Gate 待办、运行高亮、回放和 artifacts |
| E：加固与文档 | 基本完成 | 路径与图片 URL 安全、Gate 文件锁、Token/事件上限、密钥隔离、规则/变换资源边界、测试、示例和故障排查；全流程 Playwright CI 留作后续 |

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
   .\.venv\Scripts\python -m uvicorn loomlane.main:app --app-dir backend/src --reload-dir backend/src --port 8000
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

### 15.8 运行一直处于 waiting

- 检查 `GET /api/runs/{run_id}` 返回的 `pending_gates`，其中会列出 Gate、来源边、提示文案和超时时间。
- 申请人工确认：

  ```powershell
  Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/runs/<run_id>/gates/<gate_id> `
    -ContentType application/json `
    -Body '{"decision":"approve","comment":"确认通过"}'
  ```

- 多个 Gate 必须全部解决后运行才会继续。已决策 Gate 再次提交会返回 `409`，不存在的 Gate 返回 `404`。
- 如果配置了 `timeout_seconds`，应检查 `on_timeout`。超时后运行时会写入默认决策，迟到的审批不会覆盖该决策。

### 15.9 artifacts 没有出现或路径报错

- 只处理 Agent 的 `TEXT`、`DATA`、`FILE` 输出端口；确认对应端口确实产生了值。
- 检查 `output_dir` 是否是工作区内的相对路径，不能包含绝对路径、盘符或 `..` 逃逸。
- `FILE` 输出引用的源文件必须是运行工作区内的已存在普通文件；不存在、目录、符号链接逃逸和越界路径都会被拒绝。
- 默认产物目录是 `runs/{run_id}/workspace/artifacts/`，文件名为 `{step:05d}-{batch_id}-{agent_id}-{port_name}{extension}`。
- 通过运行详情或回放查看 `artifact_written` 事件，事件中会记录相对路径、Agent、批次、类型和字节数。

### 15.10 Token 累计上限被触发

- Agent 限额检查该 Agent 跨所有批次和重入的累计 usage；Workflow 限额检查整个运行。
- 限额在请求结束并拿到 usage 后检查，因此最后一次请求可能让累计值超过配置上限，但不会继续发起后续调用。
- 检查 SDL 中的 `limits.max_total_tokens`、单次 `max_tokens` 和 `max_steps`，三者是不同层级，不能用其中一个替代另一个。
- 如果模型不返回 usage，运行时会保留已记录的 usage，无法对缺失 usage 进行精确计费。

### 15.11 Aggregate 节点不执行

- `AGGREGATE all` 会等待同一批次下所有聚合入边到达；只要一个分支失败或没有产出，目标就不会执行。
- `AGGREGATE any` 在第一条同批入边到达时执行一次，后到的同名端口可能覆盖先前值；不要依赖“所有分支都已合并”的语义。
- 检查聚合 mapping 是否为目标必填输入提供了端口；当前校验器不检查 mapping 覆盖率。
- 检查 Fan-out 的源 Agent、批次 ID 和聚合边是否属于同一逻辑分支。普通边与聚合边不能混用到同一个目标。

### 15.12 图片生成失败

- `/images/generations` 返回 404：确认 `base_url` 只写到 API 版本根路径，并确认供应商确实提供图片生成接口；DeepSeek 文本模型不能生成图片。
- AUTOMATIC1111 返回 404：确认 WebUI 使用 `--api` 启动，Profile 的 `base_url` 是根地址，并且运行时可访问该地址。
- AUTOMATIC1111 连接被拒绝或超时：确认 WebUI 正在运行、端口正确，并适当提高 `request.timeout_seconds`；本地服务默认允许不配置 API Key。
- 返回模型或无权限错误：确认图片 Profile 的 `model`、API Key 和账号图片权限；图片 Key 与文本 Key 可以分开配置。
- 提示 Profile 不支持图片：在 API Profile 中启用 `Image generation`，并确保 Agent 选择的是该 Profile。
- 提示必须有一个 FILE 输出：Image Agent 只保留一个输出端口，类型设为 `FILE`。
- 提示不是支持的图片：服务返回了 HTML、JSON 错误页或其他二进制内容；查看运行日志中的 HTTP 状态与供应商错误详情。
- 提示非公网地址：图片服务返回了指向本机、内网或链路本地地址的 URL。当前安全策略不允许运行器下载这类 URL，可让服务改为返回 `b64_json`。
- 图片超过 20 MB：降低尺寸或质量，或让供应商返回压缩格式；该限制不能通过 Profile 绕过。

### 15.13 Agent Python 规则执行失败

- 保存时返回 `invalid_agent_rule`：检查表达式是否使用了未允许的根、import、赋值、lambda、推导式、循环、dunder 属性、关键字参数或任意方法调用。
- `input` 阶段只能读取 `inputs`、`workflow`、`edges`、`run`；只有 `output` 阶段可以读取 `outputs`。
- 规则必须返回 Python `bool`。`1`、`"ok"`、`None` 等真值或空值不会被自动转换。
- 规则超时默认是 `2` 秒，包含隔离子进程启动时间。持续超时时应简化表达式，并检查机器是否因安全软件显著拖慢 Python 子进程启动。
- 规则失败会记录 `rule_failed`，其中的 `error` 和可选 `message` 应结合 `events.jsonl` 查看。输入规则失败时不会产生模型调用；输出规则失败时可能已有 `token_usage`，但不会写入该 Agent 的 artifacts。
- 表达式长度、AST 节点/深度、上下文、字符串、集合、整数和操作次数均有限额，超过限额属于预期拒绝，不应通过关闭沙箱规避。

### 15.14 增强条件未生效或保存失败

- `starts_with`、`ends_with` 的右侧必须是字符串；`length_eq` 的右侧必须是非负整数；`between` 的右侧必须是两个元素的数组。
- `contains_all`、`contains_any` 的右侧必须是数组，左侧必须是字符串、对象或集合值。
- `matches` 不允许分组、lookaround、内联 flag 和反向引用；pattern 最大 `256` 字符，目标文本最大 `4096` 字符，重复次数上限为 `1000`。
- 条件引用缺失或类型不匹配时，运行时谓词返回 `false`；保存时引用了不存在的 Agent、端口、工作流输入或边，仍会作为校验错误返回。
- 前端结构化编辑器与高级 JSON 应保持同一种条件树。切换后出现字段丢失时，先展开高级 JSON 核对 `op`、`args`、`left` 和 `right`，不要混用旧版不支持的运算符名称。

### 15.15 数据变换失败

- `invalid_transform_field`：字段模式的 `path` 必须是小写标识符点分隔路径，最多 8 段；检查重复路径，以及 `outline` 与 `outline.sections` 这类父子冲突。
- 字段模式的值只填写表达式，不要填写完整 JSON 对象。例如路径 `outline.sections` 对应值表达式 `raw_outputs.sections`，后端会负责生成 `{"outline": {"sections": ...}}`。
- `input_transform must return an object` 或 `output_transform must return an object`：Agent 变换的结果必须是 JSON 对象；只返回字符串、数组或标量时会被拒绝。Edge mapping 变换可以使用任意严格 JSON 返回值。
- `transform result type ... is not JSON-compatible`：删除 tuple、set、自定义对象或 `MISSING`。表达式中的 tuple/set 字面量可以被解释器构造，但不会被允许作为最终结果。
- `transform result object keys must be strings`：所有对象键必须显式转换为字符串；当前沙箱不会像 JavaScript 一样自动转换数字键。
- `transform result contains NaN or Infinity` 或 `contains a circular reference`：改为有限数字和普通树状数组/对象，不能返回递归结构。
- 变换后提示缺少必填输入：调度器已在变换前判断 Agent/批次就绪，`input_transform` 只能保留、删除或重组已送达端口，不能补齐一条从未映射的必填端口。应修正边 mapping。
- Edge 变换后提示目标端口类型或 `schema_hint` 不匹配：表达式必须返回目标端口契约需要的值；保存阶段放宽源/目标同类型检查，不代表运行时会跳过目标校验。
- `invalid_agent_transform` 或 `invalid_edge_transform`：检查上下文根。输入变换不能引用 `outputs`/`raw_outputs`，输出变换不能引用 `outputs`，边 mapping 变换不能引用 `inputs`。
- 表达式禁止任意方法调用。需要裁剪时可使用安全 helper 或组合表达式；不要写 `inputs.text.strip()`，应使用当前已支持的字符串 helper，或在输入进入工作流前完成清理。
- 通过 `events.jsonl` 或回放中的 `transform_started`、`transform_passed`、`transform_failed` 定位失败阶段；`scope`、`phase` 和 Edge 的 `mapping_index` 会标明具体位置。

## 16. V0.3 验收结果

- [x] 工作流可以保存为可读 SDL，重新打开后语义和布局正确恢复。
- [x] 端口、引用、普通环、条件、Feedback/Loop guard 和 Aggregate 入边一致性会被校验。
- [x] 同一 SDL 在相同编译器版本下生成确定性、独立可运行的 Python 项目。
- [x] Pipe、条件分支和有界 Feedback/Loop 通过后端测试。
- [x] Fan-out 分支可在有界并发下执行，并按批次传播。
- [x] Aggregate `all` 等待同批全部分支，Aggregate `any` 按批次只执行一次。
- [x] 多 Gate 可暂停运行，支持批准、拒绝、超时决策和并发决策冲突保护。
- [x] 运行事件写入 `events.jsonl`，运行详情可从事件重建 Gate 待办和最终状态。
- [x] 前端回放工具条支持播放/暂停、单步、拖动、倍速和回到实时状态。
- [x] Agent/Workflow 累计 Token、单次请求 Token 和最大步数形成分层限额。
- [x] `TEXT`、`DATA`、`FILE` 输出自动落盘，并记录 artifacts 事件。
- [x] API Profile 可复用，密钥不出现在项目文件、SDL、日志、生成代码或 Git 中。
- [x] 左、右、底部面板支持收纳；左/右宽度和底部高度可调整，偏好可持久化。
- [x] Agent、边、API Profile 和项目具有明确的删除及引用清理语义。
- [x] 条件编辑器支持多谓词、嵌套、`AND`/`OR`/`NOT` 和高级 JSON。
- [x] Agent 可独立启用层次化 JSON 输出，并发送 `json_object` 响应格式。
- [x] Agent 可切换图片生成模式，并通过独立图片 Profile 调用 OpenAI 兼容图片接口或本地 AUTOMATIC1111 `txt2img`。
- [x] 图片结果支持 Base64 与公网 URL，经过格式和大小校验后写入工作区并生成 FILE artifact。
- [x] 图片 Profile 参数可通过 API CRUD 往返保存，Agent 模式与 Profile 能力不一致时校验失败。
- [x] Agent 支持输入前和输出后 Python 精细规则，并通过白名单 AST、隔离子进程、最小环境和资源上限执行。
- [x] 规则表达式禁止 import、赋值、lambda、推导式、循环、dunder、关键字/星号参数和任意函数调用。
- [x] 规则上下文按阶段隔离，输入阶段不能提前读取 Agent 输出；规则必须返回严格布尔值。
- [x] 每条执行规则产生 `rule_started`、`rule_passed` 或 `rule_failed` 事件，输入失败阻止模型调用，输出失败阻止产物落盘。
- [x] Agent 可在模型调用前执行输入变换，并在模型返回后、端口校验和 artifacts 前执行输出变换；两类结果都必须为 JSON 对象。
- [x] Agent 变换默认使用可读的字段模式，由后端组装 JSON 表达式，同时保留旧版高级表达式兼容。
- [x] Edge mapping 可在目标端口写入前执行数据变换，并可用显式表达式完成 `TEXT`/`DATA`/`FILE` 之间的受控转换。
- [x] 三类变换使用隔离上下文根和严格 JSON 结果，拒绝非 JSON 类型、循环引用、非有限数字和超出资源上限的结果。
- [x] 变换按固定顺序执行并产生 `transform_started`、`transform_passed` 或 `transform_failed` 事件；失败不会越过当前阶段继续执行。
- [x] 条件语言支持 `between`、`starts_with`、`ends_with`、`matches`、`empty`、`not_empty`、`length_eq`、`contains_all` 和 `contains_any`。
- [x] 正则条件实施长度、重复次数和危险结构限制，不开放任意代码或动态求值。
- [x] 生成项目包含独立 `runners/rule_sandbox.py`，其编译器版本记录为 `0.3.2`。

内置 V0.2 综合演示仍使用 SDL `0.2`，用于保留向后兼容样本；V0.3 新增的规则和条件字段均为可选，不要求已有项目批量迁移。

## 17. 当前限制与后续演进

- `result.json.agent_outputs` 只保留每个 Agent 最新批次的输出；历史批次以事件和 artifacts 为准。
- `AGGREGATE any` 的后到同名端口采用覆盖语义，调用方不应把它当作完整合并。
- Validator 尚未检查 Aggregate mapping 是否覆盖目标 Agent 的全部必填输入。
- 并行只限单机生成项目内的线程池，不提供跨主机、持久队列或资源感知调度。
- Token 限额在 usage 返回后检查，最后一次调用可能轻微超限。
- Gate 审批与运行等待依赖当前本地进程和文件锁；进程异常退出后需要用户重新启动运行。
- 图片服务目前只支持单图生成，多图数量、图生图、局部编辑和异步任务轮询尚未建模。
- URL 图片下载在 DNS 解析后由 HTTP 客户端再次连接；完全消除 DNS rebinding 需要固定解析地址的自定义传输层，当前作为本地工具的后续加固项。
- Playwright 全流程、窄屏视觉检查和产物下载/预览尚未纳入自动化验收。
- Python 规则有意停留在布尔校验表达式；数据变换只提供无副作用表达式，不提供脚本级语句、文件访问、网络访问或持久变量。
- 当前只实现 OpenAI 兼容 Chat/Image Provider；多 Provider 协议抽象和能力路由留待后续版本。
- 条件正则使用 Python `re` 并限制 pattern/target 大小，但没有跨语言正则兼容层或完整的计算复杂度静态分析。

SDL 版本升级遵循以下规则：

- 新字段优先增加为可选字段，并给默认值。
- 删除或改变语义必须提升 `sdl_version`。
- 编译器按 SDL 版本选择迁移器。
- 生成项目 manifest 记录编译器版本，保证历史运行可追溯。

