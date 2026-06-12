# PatchPilot

面向 Python/pytest 项目的轻量级 Code Repair Agent。它运行 pytest 获取失败日志，自动或手动选择相关文件，调用 LLM 生成 unified diff patch，经 **policy guard** 校验后应用 patch，再次运行 pytest 验证结果，输出 `report.json` 和 `report.md`。

---

## 核心能力

- 自动运行 pytest 获取 baseline 失败日志
- `--auto-files`：从失败日志中提取相关 `.py` 文件（规则检索）
- 调用 DeepSeek / OpenAI-compatible API 生成 unified diff patch
- `--llm-mode mock`：无需 API key 即可运行完整流程（用于 demo 和 CI）
- **Policy guard**：通过 `policies/policy.yaml` 配置校验规则，拒绝危险 patch
- `git apply --check` 校验后再应用 patch
- pytest 再次验证修复结果
- `--max-iters`：失败时最多多轮重试，每轮携带上一轮错误信息
- `final_decision` 字段明确输出 `BLOCKED` 或 `NEEDS_HUMAN_REVIEW`，AI patch 始终需要人工审查
- `runs/<run_id>/` 下保存每轮完整产物，便于复盘

---

## 架构流程

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

### Mock 模式（无需 API key，推荐用于 demo / CI）

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

| 参数 | 说明 |
|---|---|
| `--repo` | 目标仓库路径（必须是 git repo） |
| `--pytest` | pytest 命令字符串 |
| `--llm` | 使用 LLM 生成 patch（与 `--patch` 二选一） |
| `--llm-mode` | LLM 后端：`openai`（默认）或 `mock`（无需 API key） |
| `--patch` | 手动指定 .patch 文件（与 `--llm` 二选一） |
| `--files` | 手动指定相关文件（与 `--auto-files` 二选一，需配合 `--llm`） |
| `--auto-files` | 自动从日志提取相关文件（与 `--files` 二选一，需配合 `--llm`） |
| `--max-iters` | 最大修复轮数，1–5，默认 1 |
| `--timeout` | 每次 pytest 的超时秒数，默认 600 |
| `--policy` | policy YAML 路径，默认 `policies/policy.yaml` |

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

`examples/demo_project/` 是一个独立的演示目标仓库，包含一个故意引入的加法 bug：

```python
# calculator.py
def add(a, b):
    return a - b  # bug
```

使用前需要先初始化为独立 git repo（`git apply --check` 要求目标目录是 git repo）：

```bash
bash scripts/reset_demo_project.sh
```

脚本会重置 `calculator.py` 为 buggy 状态并重新 `git init`，适合反复演示。

---

## 目录结构

```
PatchPilot/
  agent.py                   # 主入口，含多轮 repair loop
  policies/
    policy.yaml              # 默认 policy 配置
  llm/
    client.py                # LLMClient（OpenAI-compatible）+ MockLLMClient + create_llm_client()
    prompt_builder.py        # build_repair_prompt()
    parser.py                # extract_diff_from_response()
    fake_llm.py              # 独立工具：从文件读取 patch（未接入主流程）
  tools/
    patch_guard.py           # policy-driven patch 校验
    policy_loader.py         # 从 YAML 加载 policy，缺失字段使用默认值
    tester.py                # run_pytest()
    patcher.py               # check_patch() / apply_patch()
    retriever.py             # retrieve_files_from_pytest_log()
    safety.py                # validate_patch()（辅助校验，保留）
    classifier.py            # classify_failure()
  report/
    reporter.py              # write_report() → report.json
    markdown_reporter.py     # write_markdown_report() → report.md
  examples/
    demo_project/            # 演示目标仓库（需先运行 reset 脚本初始化）
      calculator.py
      tests/test_calculator.py
      pytest.ini
  scripts/
    reset_demo_project.sh    # 重置 demo_project 到 buggy 初始状态
  runs/                      # 每次运行产物（gitignored）
  .env.example
  requirements.txt
  README.md
```

---

## 当前限制

- 主要支持 Python/pytest 项目，不支持其他语言或测试框架
- `--auto-files` 是规则检索，不是向量检索；对复杂项目未必准确
- 复杂的跨多文件 bug 不保证能成功修复
- 需要目标项目有可运行的 pytest 测试，且目标目录必须是 git repo
- 不支持 Web UI，不支持 GitHub PR 自动创建
- `final_decision` 永远不会自动设为 PASS；AI patch 必须经过人工审查

---

## 关于这个项目

PatchPilot 的核心思路是 **LLM + 工程验证闭环**：LLM 负责生成 patch，pytest 负责验证结果，policy guard 负责守住安全边界，三者共同构成一个有明确成功/失败判断的工程流程。LLM 不是单纯地输出文字，而是嵌入这个流程中被约束和验证的一个环节。
