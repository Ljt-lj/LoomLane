# LoomLane

LoomLane 是一个本地优先的多 Agent 流水线可视化编排与编译系统。V0.1 已打通“可视化编辑 -> SDL 校验 -> 生成独立 Python 项目 -> 子进程运行 -> SSE 实时回传”的完整闭环。

## V0.1 功能

- 使用 React Flow 编辑 Agent、端口、连线和反馈边。
- 支持 `PIPE`、`CONDITIONAL`、`FEEDBACK` 三类边。
- 右侧检查器支持最小化收纳和 `300-560px` 宽度调节，并记忆上次 UI 状态。
- 支持 Agent、边和 API Profile 删除；删除 Agent 会清理关联边和工作流输出，删除 Profile 会迁移或阻止仍被引用的 Agent。
- 条件编辑器支持 `AND`、`OR`、`NOT`、多谓词嵌套、删除和高级 JSON 编辑。
- 每个 Agent 可独立启用层次化 JSON 输出模板，并通过 `response_format={"type":"json_object"}` 约束模型返回 JSON。
- 以 `workflow.sdl.yaml` 作为唯一业务语义真源，`canvas.json` 只保存布局。
- 支持 SDL 编辑、导入、导出、结构校验和语义校验。
- 从 SDL 确定性生成可独立运行的 Python 工作流项目。
- 通过 OpenAI 兼容的 `/chat/completions` 接口执行 Agent。
- 通过 NDJSON 和 SSE 回传节点、边、Token 用量和运行错误。
- 保存运行历史、事件和结果，并支持取消运行。
- API Key 优先存储在操作系统凭据库；Windows 凭据库不可用时，使用当前用户 DPAPI 加密的本地回退文件。密钥不进入 SDL、项目文件或生成代码。
- 提供桌面和移动端自适应工作界面。

## 仓库结构

```text
LoomLane/
├─ apps/web/                 React + TypeScript + Vite 前端
├─ backend/
│  ├─ src/loomlane/          FastAPI、SDL、校验、编译和运行时
│  ├─ templates/generated/   独立 Python 工作流模板
│  └─ tests/                 后端测试
├─ docs/                     技术方案
├─ examples/content-review/  示例 SDL 与画布
└─ workspace/                本地运行数据，默认不提交 Git
```

## 环境要求

- Python 3.12
- Node.js 20+
- pnpm 10+，推荐使用 pnpm 11
- 操作系统凭据库，例如 Windows Credential Manager、macOS Keychain 或 Linux Secret Service。Windows 上凭据库不可用时会自动使用 DPAPI 加密回退。

## 安装

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\backend[dev]"
pnpm --dir apps/web install
```

`apps/web/pnpm-workspace.yaml` 已包含 pnpm 11 的 `allowBuilds` 和依赖构建脚本配置。如果全新安装仍提示构建脚本被忽略，先在 `apps/web` 目录执行 `pnpm approve-builds`，确认只允许项目所需的 `esbuild` 构建脚本。

## 启动

终端一启动后端：

```powershell
.\.venv\Scripts\python -m uvicorn loomlane.main:app --app-dir backend/src --reload --port 8000
```

终端二启动前端：

```powershell
pnpm --dir apps/web dev
```

- 前端：`http://127.0.0.1:5173`
- API 健康检查：`http://127.0.0.1:8000/api/health`
- OpenAPI 文档：`http://127.0.0.1:8000/docs`

首次启动时会自动创建一个“内容审查示例”项目。

## 使用流程

1. 在画布中编辑 Agent、端口和边，或导入已有 SDL。
2. 点击“校验”，处理右侧和 SDL 预览中的问题。
3. 点击“生成”，得到确定性的 `compiler_key` 和独立 Python 项目。
4. 打开右侧 `API` 检查器，为 API Profile 输入 Key 并保存。修改 Profile 配置后需要先保存项目，再保存密钥。
5. 在运行输入中填写工作流启动节点所需的输入，点击“运行”。
6. 在画布和历史面板查看节点、边、Token 用量、错误与最终结果。

API Key 按项目 ID 和 Profile ID 隔离，并绑定 Profile 的 `base_url` origin。修改 origin 后，旧密钥会被视为未配置。Windows 上若系统凭据库写入失败，LoomLane 会把密钥保存到 `workspace/credentials.dpapi`；该文件使用当前 Windows 用户的 DPAPI 加密，`workspace/` 默认不进入 Git。

修改 Pydantic 模型、SDL 字段或生成模板后必须重启后端；如果保存时出现 `422 extra_forbidden`，通常说明后端仍在运行旧代码。更多配置格式和排障步骤见 [docs/initial-technical-plan.md](docs/initial-technical-plan.md)。

## 本地数据

默认数据目录是仓库根目录下的 `workspace/`：

```text
workspace/projects/{project_id}/
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

可通过 `LOOMLANE_DATA_DIR` 改写数据根目录：

```powershell
$env:LOOMLANE_DATA_DIR = "D:\LoomLaneData"
.\.venv\Scripts\python -m uvicorn loomlane.main:app --app-dir backend/src --reload --port 8000
```

项目写入使用 revision 冲突检测。保存请求中的 `base_revision` 与当前版本不一致时，API 返回 `409 Conflict`。

## 生成项目

每次生成会写入：

```text
generated/{compiler_key}/
├─ workflow.py
├─ config.py
├─ manifest.json
├─ requirements.txt
├─ agents/
└─ runners/
```

生成项目不依赖 `loomlane` Python 包，第三方运行依赖只有 `httpx`。进入生成目录后，可独立安装和执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

$env:LOOMLANE_RUN_ID = "standalone-run"
$env:LOOMLANE_WORKFLOW_HASH = "<compiler_key>"
$env:LOOMLANE_SECRET_OPENAI_MAIN = "<api-key>"

.\.venv\Scripts\python workflow.py --input input.json --result result.json
```

环境变量名由 Profile ID 转成大写并将非字母数字字符替换为下划线得到。例如 `openai-main` 对应 `LOOMLANE_SECRET_OPENAI_MAIN`。对于 `localhost`、`127.0.0.1` 和 `::1` 上的本地兼容服务，可以省略密钥。

生成项目从 `--input` 指定的 JSON 文件读取工作流输入，stdout 输出 NDJSON 事件，`--result` 指定最终结果文件。

## V0.1 边界

- 只支持本地单用户使用，不包含多租户、权限和云端协作。
- 只实现 OpenAI 兼容接口，不包含完整的多 Provider 适配。
- v0.1 采用确定性串行调度，不并行执行节点。
- `LOOP`、`FAN_OUT`、`AGGREGATE`、`HUMAN_GATE` 尚未实现。
- 条件使用受限 AST，不允许执行任意 Python、JavaScript 或 Jinja2 表达式。
- 模型输出要求是严格 JSON 对象，v0.1 不提供猜测式修复。
- `FILE` 端口只传递工作区内文件引用，不自动把模型文本落盘为文件。
- Token 用量会统计和展示，但累计 Token 上限中断留待后续版本。
- 运行事件是节点和边级事件，不包含 token 级模型流式文本。

## 测试与构建

```powershell
.\.venv\Scripts\python -m compileall -q backend\src
.\.venv\Scripts\python -m pytest backend\tests -q
pnpm --dir apps/web test
pnpm --dir apps/web build
```

完整技术方案见 [docs/initial-technical-plan.md](docs/initial-technical-plan.md)。

