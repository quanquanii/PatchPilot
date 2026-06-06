# PatchPilot

面向 Python/pytest 项目的轻量级 Code Repair Agent。它运行 pytest 获取失败日志，自动或手动选择相关文件，调用 DeepSeek 生成 unified diff patch，校验并应用 patch，再次运行 pytest 验证结果，输出 `report.json` 和 `report.md`。

---

## 核心能力

- 自动运行 pytest 获取 baseline 失败日志
- `--auto-files`：从失败日志中提取相关 `.py` 文件（规则检索）
- 调用 DeepSeek API 生成 unified diff patch
- 安全校验：拒绝路径遍历、敏感文件、非 `.py` 文件、测试文件修改
- `git apply --check` 校验后再应用 patch
- pytest 再次验证修复结果
- `--max-iters`：失败时最多多轮重试，每轮携带上一轮错误信息
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
DeepSeek API
      │
      ▼
extract patch           ← 从响应中提取 diff --git 块
      │
      ▼
safety check            ← 路径、扩展名、tests/ 限制
      │
      ▼
git apply --check / git apply
      │
      ▼
pytest verify
      │
   passed? ──── yes ──▶ success, write report
      │
     no
      │
   iter < max? ── yes ──▶ update feedback, next iteration
      │
     no
      ▼
   failure, write report
```

---

## 安装与配置

```bash
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY
```

> `.env` 包含敏感信息，**不要提交到 Git**（已在 `.gitignore` 中忽略）。

### 环境变量

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | **必填** |
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

### 自动文件模式（推荐）

```bash
python3 agent.py \
  --repo ../demo_project \
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
  --repo ../demo_project \
  --pytest "pytest -q" \
  --llm \
  --files calculator.py tests/test_calculator.py \
  --max-iters 3 \
  --timeout 60
```

### Patch harness 模式（验证修复流程）

```bash
python3 agent.py \
  --repo ../demo_project \
  --pytest "pytest -q" \
  --patch fix_add.patch \
  --timeout 60
```

### 参数说明

| 参数 | 说明 |
|---|---|
| `--repo` | 目标仓库路径 |
| `--pytest` | pytest 命令字符串 |
| `--llm` | 使用 DeepSeek 生成 patch（与 `--patch` 二选一） |
| `--patch` | 手动指定 .patch 文件（与 `--llm` 二选一） |
| `--files` | 手动指定相关文件（与 `--auto-files` 二选一，需配合 `--llm`） |
| `--auto-files` | 自动从日志提取相关文件（与 `--files` 二选一，需配合 `--llm`） |
| `--max-iters` | 最大修复轮数，1–5，默认 1 |
| `--timeout` | 每次 pytest 的超时秒数，默认 600 |

---

## 输出产物

每次运行在 `runs/<run_id>/` 下生成：

```
baseline_pytest.log
retrieved_files_iter_<n>.json   （--auto-files 模式）
repair_prompt_iter_<n>.txt      （--llm 模式）
llm_response_iter_<n>.txt       （--llm 模式）
generated_patch_iter_<n>.diff   （--llm 模式）
pytest_iter_<n>.log
report.json
report.md
```

### report.json 字段

| 字段 | 说明 |
|---|---|
| `success` | 本次修复是否成功 |
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
| `history` | 每轮详情列表（见下） |

`history` 每个元素包含：`iteration`、`selected_files`、`prompt_path`、`llm_response_path`、`generated_patch_path`、`patch_applied`、`pytest_passed`、`pytest_log_path`、`llm_error`、`patch_error`、`failure_category`。

### failure_category 取值

| 值 | 含义 |
|---|---|
| `llm_api_error` | DeepSeek API 调用失败（网络、SSL、认证） |
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

## 安全边界

LLM 生成的 patch 在应用前经过以下校验，任意一条不通过则拒绝应用：

1. patch 必须以 `diff --git` 开头
2. 路径不允许包含 `../`（路径遍历）
3. 路径不允许为绝对路径
4. 路径不允许包含 `.env`、`id_rsa`、`secret`、`token`、`credentials`
5. 只允许修改 `.py` 文件
6. 默认禁止修改 `tests/` 目录下的文件

---

## 目录结构

```
PatchPilot/
  agent.py                   # 主入口，含多轮 repair loop
  llm/
    client.py                # LLMClient（DeepSeek / OpenAI-compatible）
    prompt_builder.py        # build_repair_prompt()
    parser.py                # extract_diff_from_response()
  tools/
    tester.py                # run_pytest()
    patcher.py               # check_patch() / apply_patch()
    retriever.py             # retrieve_files_from_pytest_log()
    safety.py                # validate_patch() / extract_modified_files()
    classifier.py            # classify_failure()
  report/
    reporter.py              # write_report() → report.json
    markdown_reporter.py     # write_markdown_report() → report.md
  runs/                      # 每次运行产物
  .env.example
  requirements.txt
  README.md
```

---

## 当前限制

- 主要支持 Python/pytest 项目，不支持其他语言或测试框架
- `--auto-files` 是规则检索，不是向量检索；对复杂项目未必准确
- 复杂的跨多文件 bug 不保证能成功修复
- 需要目标项目有可运行的 pytest 测试
- 不支持 Web UI，不支持 GitHub PR 自动创建

---

## 关于这个项目

PatchPilot 的核心思路是 **LLM + 工程验证闭环**：LLM 负责生成 patch，pytest 负责验证结果，两者形成反馈循环。LLM 不是单纯地输出文字，而是嵌入一个有明确成功/失败判断的工程流程中。

