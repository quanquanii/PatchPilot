# PatchPilot

面向 Python/pytest 项目的轻量级 AI 工程助手，提供四个独立 workflow：

- **repair**（默认）：运行 pytest 获取失败日志 → 调用 LLM 生成 unified diff patch → 经 **policy guard** 校验 → `git apply` → pytest 验证，输出 `report.json` / `report.md`。
- **spec-review**（`--spec-review`）：读取 `requirements.md` → 调用 LLM 生成结构化需求评审 → 输出 clarifying questions、risks、acceptance criteria 等，`report.json` / `report.md`。不修改代码，不需要 git repo。
- **design-review**（`--design-review`）：读取 `requirements.md` + `design.md` → 调用 LLM 对设计方案做交叉评审 → 输出 requirement coverage、design risks、edge cases、security risks、test strategy 等，`report.json` / `report.md`。不修改代码，不需要 git repo。
- **review-diff**（`--review-diff`）：读取 `.diff` / `.patch` 文件 → 调用 LLM 做代码变更评审 → 输出 summary、bug risks、security risks、maintainability findings、test coverage gaps 等，`report.json` / `report.md`。不修改代码，不 apply patch，不需要 git repo。

---

## 核心能力

**Repair workflow**
- 自动运行 pytest 获取 baseline 失败日志
- `--auto-files`：从失败日志中提取相关 `.py` 文件（规则检索）
- 调用 DeepSeek / OpenAI-compatible API 生成 unified diff patch
- **Policy guard**：通过 `policies/policy.yaml` 配置校验规则，拒绝危险 patch
- `git apply --check` 校验后再应用 patch
- pytest 再次验证修复结果
- `--max-iters`：失败时最多多轮重试，每轮携带上一轮错误信息
- `final_decision` 字段明确输出 `BLOCKED` 或 `NEEDS_HUMAN_REVIEW`，AI patch 始终需要人工审查

**Spec-review workflow**
- 读取 `requirements.md`，调用 LLM 进行结构化需求评审
- 输出 `clarifying_questions`、`functional_scope`、`out_of_scope`、`risks`、`acceptance_criteria`、`suggested_test_cases`
- 不修改代码，不需要 git repo，不需要 pytest
- `final_decision` 始终为 `NEEDS_HUMAN_REVIEW`

**Design-review workflow**
- 读取 `requirements.md` 和 `design.md`，调用 LLM 对设计方案做交叉评审
- 输出 `requirement_coverage`、`missing_requirements`、`design_risks`、`edge_cases`、`security_risks`、`test_strategy`、`interfaces_and_boundaries`
- 不修改代码，不需要 git repo，不需要 pytest
- `final_decision` 始终为 `NEEDS_HUMAN_REVIEW`

**Review-diff workflow**
- 读取 `.diff` / `.patch` 文件，调用 LLM 进行代码变更评审
- 输出 `summary`、`bug_risks`、`security_risks`、`maintainability_findings`、`test_coverage_gaps`、`suggested_followups`、`blocking_findings`
- 不修改代码，不执行 `git apply`，不运行 pytest，不需要 git repo
- `final_decision` 始终为 `NEEDS_HUMAN_REVIEW`，`human_review_required` 始终为 `true`

**共同能力**
- `--llm-mode mock`：无需 API key 即可运行完整流程（用于 demo 和 CI）
- `runs/<run_id>/` 下保存每次运行的完整产物，便于复盘

---

## 架构流程

### Repair workflow

```
baseline pytest
      │
      ▼
retrieve files          ← --auto-files: 从日志提取
      │                 ← --files: 手动指定
      ▼
build prompt            ← 包含日志 + 文件内容 + 轮次 + 上轮错误
      │
      ▼
LLM（openai / mock）
      │
      ▼
extract patch           ← 从响应中提取 diff --git 块
      │
      ▼
policy guard            ← policies/policy.yaml：扩展名、路径、大小、tests 限制
      │                    git apply --check（可配置）
   passed?
      │ no ──▶ BLOCKED，写 report，退出
      │ yes
      ▼
git apply
      │
      ▼
pytest verify
      │
   passed? ──── yes ──▶ NEEDS_HUMAN_REVIEW，写 report
      │
     no
      │
   iter < max? ── yes ──▶ update feedback，next iteration
      │
     no
      ▼
   NEEDS_HUMAN_REVIEW，写 report
```

> **注意**：即使测试通过，`final_decision` 仍为 `NEEDS_HUMAN_REVIEW`。AI 生成的 patch 始终需要人工审查后才能合并。

### Spec-review workflow

```
read requirements.md
      │
      ▼
build spec-review prompt
      │
      ▼
LLM（openai / mock）
      │
      ▼
parse JSON response      ← 支持 ```json 代码块或直接 JSON
      │
      ▼
write report.json / report.md   → final_decision: NEEDS_HUMAN_REVIEW
```

spec-review 不产生任何 patch，不调用 `git apply`，不运行 pytest，因此也不需要 policy guard。

### Design-review workflow

```
read requirements.md + design.md
      │
      ▼
build design-review prompt   ← 包含需求文档 + 设计文档
      │
      ▼
LLM（openai / mock）
      │
      ▼
parse JSON response           ← 支持 ```json 代码块或直接 JSON
      │
      ▼
write report.json / report.md → final_decision: NEEDS_HUMAN_REVIEW
```

design-review 同样不产生 patch，不调用 `git apply`，不运行 pytest，不需要 policy guard。与 spec-review 的区别在于：它同时读取需求和设计两份文档，评审重点是**设计方案是否充分覆盖需求**，以及设计层面的风险和遗漏。

### Review-diff workflow

```
read .diff / .patch file
      │
      ▼
build diff-review prompt     ← 包含完整 diff 内容
      │
      ▼
LLM（openai / mock）
      │
      ▼
parse JSON response           ← 支持 ```json 代码块或直接 JSON
      │
      ▼
write report.json / report.md → final_decision: NEEDS_HUMAN_REVIEW
```

review-diff 不产生任何新 patch，不执行 `git apply`，不运行 pytest，不需要 policy guard。它的输入是**已有的 diff 文件**，评审重点是变更本身的质量：bug 风险、安全风险、可维护性、测试覆盖缺口和潜在的阻塞性问题。

---

## 安装与配置

```bash
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY（mock 模式下不需要）
```

> `.env` 包含敏感信息，**不要提交到 Git**（已在 `.gitignore` 中忽略）。

### 环境变量

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | `--llm-mode openai` 时必填；`--llm-mode mock` 时无需设置 |
| `DEEPSEEK_BASE_URL` | 可选，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 可选，默认 `deepseek-chat` |

兼容旧命名（优先级低于 DeepSeek 前缀）：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。

### macOS SSL 证书问题

macOS 自带 Python 可能缺少 CA 证书，报 `CERTIFICATE_VERIFY_FAILED`。解决方法：

```bash
pip install --upgrade certifi
```

`llm/client.py` 已通过 `certifi.where()` 显式指定证书包，无需额外操作。

---

## 使用方式

### Spec-review（需求评审）

```bash
python3 agent.py \
  --spec-review \
  --requirements examples/demo_project/requirements.md \
  --llm-mode mock
```

不需要 `--repo`，不需要 `--pytest`，不需要 API key（mock 模式）。输出 `runs/<run_id>/report.json`，包含结构化评审内容。

真实 LLM 用法：

```bash
python3 agent.py \
  --spec-review \
  --requirements path/to/requirements.md
# 默认 --llm-mode openai，需要 DEEPSEEK_API_KEY
```

### Design-review（设计评审）

```bash
python3 agent.py \
  --design-review \
  --requirements examples/demo_project/requirements.md \
  --design examples/demo_project/design.md \
  --llm-mode mock
```

同时提供需求文档和设计文档，LLM 评审设计方案对需求的覆盖情况、潜在风险和遗漏点。不需要 `--repo`，不需要 `--pytest`，不需要 API key（mock 模式）。

真实 LLM 用法：

```bash
python3 agent.py \
  --design-review \
  --requirements path/to/requirements.md \
  --design path/to/design.md
# 默认 --llm-mode openai，需要 DEEPSEEK_API_KEY
```

### Review-diff（代码变更评审）

```bash
python3 agent.py \
  --review-diff \
  --diff examples/demo_project/sample.diff \
  --llm-mode mock
```

提供一个 `.diff` 或 `.patch` 文件，LLM 对变更内容做结构化评审。不需要 `--repo`，不需要 `--pytest`，不需要 API key（mock 模式）。patch 文件不会被 apply 到任何 repo。

真实 LLM 用法：

```bash
python3 agent.py \
  --review-diff \
  --diff path/to/my_change.diff
# 默认 --llm-mode openai，需要 DEEPSEEK_API_KEY
```

### Repair — Mock 模式（无需 API key，推荐用于 demo / CI）

```bash
# 初始化 demo 目标仓库（仅首次或重置时需要）
bash scripts/reset_demo_project.sh

python3 agent.py \
  --repo examples/demo_project \
  --pytest "pytest -q" \
  --llm \
  --llm-mode mock \
  --files calculator.py
```

mock 模式返回一个固定 patch，修复 `examples/demo_project/calculator.py` 中的加法 bug，完整走通 policy guard → git apply → pytest verify 流程，无需网络连接。

### 自动文件模式（真实 LLM）

```bash
python3 agent.py \
  --repo ../my_project \
  --pytest "pytest -q" \
  --llm \
  --auto-files \
  --max-iters 3 \
  --timeout 60
```

`--auto-files` 从 baseline 失败日志中提取相关 `.py` 文件：

1. 解析 `FAILED path.py::test_name`、`File "path.py", line N`、`path.py:N` 等格式
2. 若只找到测试文件，根据测试名和 `assert` 行关键词搜索业务代码文件
3. 排除 `.venv`、`venv`、`__pycache__`、`.pytest_cache`、`site-packages`、`.git`
4. 最多返回 8 个文件

检索结果保存到 `runs/<run_id>/retrieved_files_iter_<n>.json`。

### 手动文件模式

```bash
python3 agent.py \
  --repo ../my_project \
  --pytest "pytest -q" \
  --llm \
  --files src/calculator.py \
  --max-iters 3
```

### Patch 模式（手动提供 patch）

```bash
python3 agent.py \
  --repo ../my_project \
  --pytest "pytest -q" \
  --patch fix.patch
```

手动 patch 同样经过 policy guard 校验；不通过则不会执行 `git apply`。

### 参数说明

**Review 参数（spec-review / design-review / review-diff）**

| 参数 | 说明 |
|---|---|
| `--spec-review` | 启用需求评审模式（三个 review 模式互斥） |
| `--design-review` | 启用设计评审模式（三个 review 模式互斥） |
| `--review-diff` | 启用代码变更评审模式（三个 review 模式互斥） |
| `--requirements` | `requirements.md` 路径（`--spec-review` 和 `--design-review` 时必填） |
| `--design` | `design.md` 路径（`--design-review` 时必填） |
| `--diff` | `.diff` / `.patch` 文件路径（`--review-diff` 时必填） |

**Repair 参数**

| 参数 | 说明 |
|---|---|
| `--repo` | 目标仓库路径（必须是 git repo） |
| `--pytest` | pytest 命令字符串 |
| `--llm` | 使用 LLM 生成 patch（与 `--patch` 二选一） |
| `--patch` | 手动指定 .patch 文件（与 `--llm` 二选一） |
| `--files` | 手动指定相关文件（与 `--auto-files` 二选一，需配合 `--llm`） |
| `--auto-files` | 自动从日志提取相关文件（与 `--files` 二选一，需配合 `--llm`） |
| `--max-iters` | 最大修复轮数，1–5，默认 1 |
| `--timeout` | 每次 pytest 的超时秒数，默认 600 |
| `--policy` | policy YAML 路径，默认 `policies/policy.yaml` |

**共用参数**

| 参数 | 说明 |
|---|---|
| `--llm-mode` | LLM 后端：`openai`（默认）或 `mock`（无需 API key） |
| `--runs-dir` | 产物目录，默认 `runs/` |

---

## Policy Guard

所有 patch（LLM 生成或手动提供）在 `git apply` 前经过 `tools/patch_guard.py` 的策略校验。规则由 `policies/policy.yaml` 定义，可按项目调整。

### 默认规则（`policies/policy.yaml`）

| 规则 | 默认值 | 说明 |
|---|---|---|
| `allowed_file_extensions` | `[".py", ".md"]` | 只允许修改这些扩展名的文件 |
| `blocked_paths` | `.env`, `id_rsa`, `secret`, `token`, `credentials` | 路径包含这些关键词则拒绝 |
| `protected_paths` | `policies/`, `.github/workflows/` | 以这些前缀开头的路径拒绝修改 |
| `max_changed_files` | `3` | 单次 patch 最多修改文件数 |
| `max_patch_lines` | `120` | 单次 patch 最多变更行数（`+`/`-` 行） |
| `allow_test_modification` | `false` | 是否允许修改测试文件 |
| `require_tests` | `true` | 保留字段，后续版本使用 |
| `require_git_apply_check` | `true` | 是否执行 `git apply --check` 作为 policy 一部分 |

### Repair vs Review workflows

| | Repair | Spec-review / Design-review / Review-diff |
|---|---|---|
| 修改代码 | 是（`git apply`） | **否** |
| 需要 git repo | 是 | **否** |
| 运行 pytest | 是 | **否** |
| Policy guard | 是（`policy.yaml` + `patch_guard.py` + `git apply --check`） | **否** |
| 输出约束 | structured JSON report + policy checks + pytest verify | structured JSON report + checklist + human review |
| `final_decision` | `BLOCKED` 或 `NEEDS_HUMAN_REVIEW` | 始终 `NEEDS_HUMAN_REVIEW` |

review workflows（spec-review、design-review、review-diff）都是**只读分析**：它们不产生可执行的 patch，不写入任何 repo，因此 policy guard 的所有门控对它们没有意义。

### 检查项

每次运行会保存 `policy_result.json`（或 `policy_result_iter_<n>.json`），包含每条检查的通过状态和原因：

```json
{
  "passed": true,
  "changed_files": ["src/foo.py"],
  "patch_lines": 4,
  "checks": [
    { "name": "unified_diff",       "passed": true,  "reason": null },
    { "name": "parseable_files",    "passed": true,  "reason": null },
    { "name": "no_absolute_paths",  "passed": true,  "reason": null },
    { "name": "no_path_traversal",  "passed": true,  "reason": null },
    { "name": "allowed_extensions", "passed": true,  "reason": null },
    { "name": "no_blocked_paths",   "passed": true,  "reason": null },
    { "name": "no_protected_paths", "passed": true,  "reason": null },
    { "name": "no_test_modification","passed": true, "reason": null },
    { "name": "max_changed_files",  "passed": true,  "reason": null },
    { "name": "max_patch_lines",    "passed": true,  "reason": null }
  ],
  "git_apply_check": { "required": true, "passed": true, "stderr": "" }
}
```

---

## 输出产物

每次运行在 `runs/<run_id>/` 下生成：

**Repair 产物**
```
baseline_pytest.log
retrieved_files_iter_<n>.json   （--auto-files 模式）
repair_prompt_iter_<n>.txt      （--llm 模式）
llm_response_iter_<n>.txt       （--llm 模式）
generated_patch_iter_<n>.diff   （--llm 模式）
policy_result_iter_<n>.json     （--llm 模式，每轮 policy 检查结果）
policy_result.json              （--patch 模式）
pytest_iter_<n>.log
report.json
report.md
```

**Spec-review 产物**
```
input_requirements.md           （输入需求文档副本）
spec_review_prompt.md           （发送给 LLM 的完整 prompt）
llm_response.txt                （LLM 原始响应）
report.json
report.md
```

**Design-review 产物**
```
input_requirements.md           （输入需求文档副本）
input_design.md                 （输入设计文档副本）
design_review_prompt.md         （发送给 LLM 的完整 prompt）
llm_response.txt                （LLM 原始响应）
report.json
report.md
```

**Review-diff 产物**
```
input_diff.patch                （输入 diff 文件副本）
diff_review_prompt.md           （发送给 LLM 的完整 prompt）
llm_response.txt                （LLM 原始响应）
report.json
report.md
```

### report.json 字段

| 字段 | 说明 |
|---|---|
| `success` | 本次修复是否成功（patch 应用且测试通过） |
| `baseline_passed` | baseline pytest 是否通过 |
| `final_passed` | 最终 pytest 是否通过 |
| `iterations` | 实际执行轮数 |
| `max_iters` | 最大允许轮数 |
| `pytest_cmd` | pytest 命令 |
| `patch_source` | `"llm"` 或 `"manual"` |
| `file_selection_mode` | `"auto"`、`"manual"` 或 null |
| `selected_files` | 最后一轮实际使用的文件列表 |
| `modified_files` | 成功应用的 patch 修改了哪些文件 |
| `failure_category` | 最后一轮失败分类（见下表） |
| `baseline_log_path` | baseline 日志路径 |
| `final_log_path` | 最终 pytest 日志路径 |
| `warning` | baseline 已通过时的提示，否则 null |
| `history` | 每轮详情列表 |
| `policy_passed` | policy guard 是否通过 |
| `policy_result_path` | policy_result.json 路径 |
| `git_apply_check_passed` | `git apply --check` 是否通过 |
| `human_review_required` | 是否需要人工审查（policy 通过时始终为 true） |
| `final_decision` | `BLOCKED`（policy 不通过）或 `NEEDS_HUMAN_REVIEW` |
| `decision_reasons` | 决策原因列表 |

`history` 每个元素包含：`iteration`、`selected_files`、`prompt_path`、`llm_response_path`、`generated_patch_path`、`patch_applied`、`pytest_passed`、`pytest_log_path`、`llm_error`、`patch_error`、`failure_category`、`policy_result_path`。

### Spec-review report.json 字段

| 字段 | 说明 |
|---|---|
| `mode` | 固定为 `"spec-review"` |
| `requirements_path` | 输入需求文档的绝对路径 |
| `final_decision` | 固定为 `"NEEDS_HUMAN_REVIEW"` |
| `human_review_required` | 固定为 `true` |
| `clarifying_questions` | 需要澄清的问题列表 |
| `functional_scope` | 功能范围列表 |
| `out_of_scope` | 明确不在范围内的项目 |
| `non_functional_requirements` | 非功能性需求 |
| `risks` | 风险列表 |
| `acceptance_criteria` | 验收标准列表 |
| `suggested_test_cases` | 建议测试用例列表 |
| `llm_error` | LLM 调用失败时的错误信息（正常时不存在） |
| `parse_error` | JSON 解析失败时的错误信息（正常时不存在） |
| `raw_response` | 解析失败时保留的 LLM 原始响应 |

### Design-review report.json 字段

| 字段 | 说明 |
|---|---|
| `mode` | 固定为 `"design-review"` |
| `requirements_path` | 输入需求文档的绝对路径 |
| `design_path` | 输入设计文档的绝对路径 |
| `final_decision` | 固定为 `"NEEDS_HUMAN_REVIEW"` |
| `human_review_required` | 固定为 `true` |
| `requirement_coverage` | 设计对各需求的覆盖情况 |
| `missing_requirements` | 设计未覆盖的需求点 |
| `design_risks` | 设计层面的风险列表 |
| `edge_cases` | 边界情况和异常输入 |
| `security_risks` | 安全风险列表 |
| `test_strategy` | 建议的测试策略 |
| `interfaces_and_boundaries` | 接口与模块边界观察 |
| `llm_error` | LLM 调用失败时的错误信息（正常时不存在） |
| `parse_error` | JSON 解析失败时的错误信息（正常时不存在） |
| `raw_response` | 解析失败时保留的 LLM 原始响应 |

### Review-diff report.json 字段

| 字段 | 说明 |
|---|---|
| `mode` | 固定为 `"review-diff"` |
| `diff_path` | 输入 diff 文件的绝对路径 |
| `final_decision` | 固定为 `"NEEDS_HUMAN_REVIEW"` |
| `human_review_required` | 固定为 `true` |
| `summary` | 变更内容的一句话描述列表 |
| `bug_risks` | 变更引入或暴露的 bug 风险 |
| `security_risks` | 安全风险列表 |
| `maintainability_findings` | 可读性或可维护性观察 |
| `test_coverage_gaps` | 缺失或应补充的测试列表 |
| `suggested_followups` | 后续可改进的任务或建议 |
| `blocking_findings` | 合并前必须修复的阻塞性问题（空列表表示无阻塞项） |
| `llm_error` | LLM 调用失败时的错误信息（正常时不存在） |
| `parse_error` | JSON 解析失败时的错误信息（正常时不存在） |
| `raw_response` | 解析失败时保留的 LLM 原始响应 |

### failure_category 取值

| 值 | 含义 |
|---|---|
| `llm_api_error` | LLM API 调用失败（网络、SSL、认证） |
| `no_diff_generated` | LLM 响应中未找到有效 diff |
| `invalid_patch_path` | patch 路径包含不允许的模式 |
| `unsafe_patch` | patch 试图修改测试文件 |
| `patch_apply_failed` | git apply 失败 |
| `syntax_error` | 修复后代码有语法错误 |
| `import_error` | 修复后有导入错误 |
| `assertion_mismatch` | 修复后测试断言仍不通过 |
| `timeout` | pytest 超时 |
| `test_collection_error` | pytest 收集不到测试 |
| `unknown` | 其他 |

### 退出码

- `success=true` → 退出码 `0`
- `success=false` → 退出码 `1`
- baseline 失败是正常输入，不代表运行出错

---

## Demo 项目

`examples/demo_project/` 包含两个演示场景：

**Repair demo** — 一个故意引入的加法 bug：

```python
# calculator.py
def add(a, b):
    return a - b  # bug: should be a + b
```

repair 演示需要先初始化为独立 git repo（`git apply --check` 要求目标目录是 git repo）：

```bash
bash scripts/reset_demo_project.sh

python3 agent.py \
  --repo examples/demo_project \
  --pytest "pytest -q" \
  --llm --llm-mode mock \
  --files calculator.py
```

**Spec-review demo** — `requirements.md` 描述 `add(a, b)` 功能需求，故意包含若干 unclear 点（非数字输入行为未定义、精度无规定），供 LLM 提出 clarifying questions 和 risks：

```bash
python3 agent.py \
  --spec-review \
  --requirements examples/demo_project/requirements.md \
  --llm-mode mock
```

spec-review 不需要 `reset_demo_project.sh`，可直接运行。

**Design-review demo** — `design.md` 描述 `add(a, b)` 的实现方案，故意包含若干评审点（无类型校验、模块边界不清晰、测试覆盖不足），与 `requirements.md` 一起送入 LLM 做交叉评审：

```bash
python3 agent.py \
  --design-review \
  --requirements examples/demo_project/requirements.md \
  --design examples/demo_project/design.md \
  --llm-mode mock
```

design-review 同样不需要 `reset_demo_project.sh`，可直接运行。

**Review-diff demo** — `sample.diff` 是将 `calculator.py` 从错误实现（`return a - b`）改成正确实现（`return a + b`）的最小 unified diff，用于演示 review-diff 对变更内容的结构化评审：

```bash
python3 agent.py \
  --review-diff \
  --diff examples/demo_project/sample.diff \
  --llm-mode mock
```

review-diff 不会修改任何文件，不需要 `reset_demo_project.sh`，可直接运行。

**Demo 项目文件清单**

```
examples/demo_project/
  calculator.py              # 含故意 bug：return a - b（repair demo 输入）
  tests/test_calculator.py   # 失败的 pytest（repair demo 输入）
  pytest.ini                 # pythonpath = . 供 pytest 8+ 使用
  requirements.md            # spec-review / design-review demo 输入
  design.md                  # design-review demo 输入
  sample.diff                # review-diff demo 输入
```

---

## 目录结构

```
PatchPilot/
  agent.py                   # 主入口：repair + spec-review + design-review + review-diff
  policies/
    policy.yaml              # 默认 policy 配置（repair 专用）
  llm/
    client.py                # LLMClient + MockLLMClient + create_llm_client()
                             #   generate_patch() → repair 用
                             #   generate()       → spec-review / design-review / review-diff 用
    prompt_builder.py        # build_repair_prompt()
                             # build_spec_review_prompt()
                             # build_design_review_prompt()
                             # build_diff_review_prompt()
    parser.py                # extract_diff_from_response()
    fake_llm.py              # 独立工具：从文件读取 patch（未接入主流程）
  tools/
    patch_guard.py           # policy-driven patch 校验（repair 专用）
    policy_loader.py         # 从 YAML 加载 policy，缺失字段使用默认值
    tester.py                # run_pytest()
    patcher.py               # check_patch() / apply_patch()
    retriever.py             # retrieve_files_from_pytest_log()
    safety.py                # validate_patch()（辅助校验，保留）
    classifier.py            # classify_failure()
  report/
    reporter.py              # write_report() → report.json
    markdown_reporter.py     # write_markdown_report() → repair report.md
  examples/
    demo_project/            # 演示目标仓库（repair 需先运行 reset 脚本初始化）
      calculator.py          # 含故意 bug：return a - b
      tests/test_calculator.py
      pytest.ini
      requirements.md        # spec-review / design-review demo 输入
      design.md              # design-review demo 输入
      sample.diff            # review-diff demo 输入
  scripts/
    reset_demo_project.sh    # 重置 demo_project 到 buggy 初始状态（repair demo 用）
  runs/                      # 每次运行产物（gitignored）
  .env.example
  requirements.txt
  README.md
```

---

## 当前限制

- **Repair**：主要支持 Python/pytest 项目，不支持其他语言或测试框架
- **Repair**：`--auto-files` 是规则检索，不是向量检索；对复杂项目未必准确
- **Repair**：复杂的跨多文件 bug 不保证能成功修复
- **Repair**：目标目录必须是 git repo（`git apply --check` 依赖）
- **Spec-review / Design-review / Review-diff**：输出质量依赖 LLM 能力；mock 模式返回固定内容，仅用于演示
- 不支持 Web UI，不支持 GitHub PR 自动创建
- `final_decision` 永远不会自动设为 PASS；所有 AI 输出都需要人工审查

---

## 关于这个项目

PatchPilot 的核心思路是**将 LLM 嵌入有明确成功/失败判断的工程流程中**，而不是让它自由输出文字。repair workflow 中，LLM 生成 patch，pytest 验证结果，policy guard 守住安全边界，三者形成反馈闭环。spec-review workflow 中，LLM 对需求文档做结构化分析，输出可供工程师直接使用的评审意见。design-review workflow 中，LLM 同时读取需求和设计两份文档，检查设计对需求的覆盖情况，识别风险与遗漏。review-diff workflow 中，LLM 对已有 diff 做代码评审，输出 bug 风险、安全风险、测试缺口和阻塞性问题。四个 workflow 都以 `NEEDS_HUMAN_REVIEW` 结束——AI 是助手，最终判断权在人。
