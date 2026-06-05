---
name: harness-init
description: "Project harness lifecycle management. Initialize on first run, health-check and recommend upgrades on subsequent runs. Trigger: /harness-init or say initialize project / check harness / harness status"
disable-model-invocation: true
---

# Harness Init — Project Harness Lifecycle Manager

> **当前平台：Codex** — 生成 `AGENTS.md`，不生成 `CLAUDE.md`。
> 子目录同理：只生成 `*/AGENTS.md`。

## 架构

```
共享层（平台无关）
├── ~/.local/bin/harness-init.py                             ← 项目诊断（JSON 输出）
├── ~/.local/bin/harness-plan.py                             ← 执行计划生成（JSON action plan）
├── ~/.local/bin/sync-docs.py                                ← 根文档 CODE_MAP 块渲染；子目录不做整文件同步
├── ~/.local/share/harness-hooks/generate_subdir_harness.py  ← 子目录 GitNexus 事实块生成与刷新
├── ~/.local/share/harness-hooks/harness_monitor.py          ← PostToolUse Hook（CODE_MAP + 根文档块 + 子目录 + 成长检测）
├── ~/.local/share/harness-hooks/generate_descriptions.py    ← CODE_MAP 描述生成（AI+GitNexus / fallback）
├── ~/.local/share/harness-hooks/session_context.py          ← SessionStart Hook（git 状态注入）
├── ~/.local/share/harness-hooks/codemaps/<project>/CODE_MAP.md ← CODE_MAP 共享缓存
└── 项目/CODE_MAP.md                                         ← ignored 本地投影 + harness cache 来源

平台入口
├── ~/.claude/skills/harness-init/SKILL.md                   ← 本文件
└── ~/.codex/skills/harness-init/SKILL.md                    ← Codex 入口
```

## 核心原则

- **渐进式构建**：根据实测复杂度信号判断，不提前堆叠
- **多语言感知**：每种语言独立评估 LSP 价值
- **跨平台对等**：CLAUDE.md / AGENTS.md 各自维护平台根文档，CODE_MAP 内容通过托管块渲染；CODE_MAP.md 以 harness cache 共享、worktree 本地投影
- **实测优于拍数字**：grep 噪声度、类型覆盖率
- **确定性执行**：Hook 通过 AI CLI（claude -p / codex exec）直接完成更新，不依赖概率性消息

## 执行流程

**脚本驱动**：所有确定性决策由脚本完成，AI 只执行需要理解力的部分。

### Step 1: 生成执行计划（脚本，不暂停）

```bash
python3 ~/.local/bin/harness-plan.py . --platform codex
```

输出 JSON action plan，包含所有后续步骤的确定性决策。示例：

```json
{
  "platform": "codex",
  "doc_file": "AGENTS.md",
  "root_doc": {"action": "copy", "from": "CLAUDE.md"},
  "codemap": {"action": "refresh", "dirs_needing": ["src/core"]},
  "codemap_local_projection": {
    "mode": "local_projection",
    "tracked": false,
    "ignored": true,
    "migration": "none",
    "cache_path": "~/.local/share/harness-hooks/codemaps/.../CODE_MAP.md"
  },
  "gitnexus": {"action": "analyze"},
  "subdirs": {
    "copy": [{"dir": "src/api", "from": "CLAUDE.md"}],
    "generate": [{"dir": "src/utils", "depth": 1}],
    "skip": ["src/common"],
    "layers": [[1, ["src/utils"]]]
  },
  "lsp": [{"language": "Python", "action": "recommend", "plugin": "..."}],
  "codex_gitnexus_wrapper": {"action": "skip"}
}
```

**拿到 plan 后立即执行 Step 2，不暂停。** 只有 `gitnexus.action == "install_and_index"` 或 `lsp.action == "recommend"` 才询问用户。

### Step 2: 按 plan 逐项执行

按 plan JSON 的字段顺序执行，每项根据 `action` 走对应分支：

#### 2.1 GitNexus（plan.gitnexus）

| action | 执行 |
|---|---|
| `skip` | 无操作 |
| `analyze` | `npx gitnexus analyze` |
| `install_and_index` | 询问用户是否安装，确认后执行 |
| `suggest_install` | 提示用户（不强制） |

#### 2.2 根文档（plan.root_doc）

根文档只在缺失时 bootstrap；持续维护的是托管 CODE_MAP 块，不做根 `CLAUDE.md` / `AGENTS.md` 整文件同步。

| action | 执行 |
|---|---|
| `skip` | 无操作；后续 CODE_MAP 刷新仍可渲染托管块 |
| `copy` | `python3 ~/.local/bin/sync-docs.py . --platform codex`，仅 bootstrap 缺失根文档并渲染 CODE_MAP 块 |
| `generate` | AI 按模板生成含托管 CODE_MAP 块的根文档 |

#### 2.3 CODE_MAP 描述（plan.codemap）

| action | background | 执行 |
|---|---|---|
| `skip` | — | 无操作 |
| `refresh` | `false`（小仓库，待刷新目录 < 6） | 对 `dirs_needing` 中的目录按 provider **同步**生成描述 |
| `refresh` | `true`（大仓库，待刷新目录 ≥ 6，AI 批次会阻塞数分钟） | 派发后台 worker，**立即返回不阻塞**：`python3 ~/.local/share/harness-hooks/harness_monitor.py --refresh-bg .` |

后台分支的返回 JSON 有三种 `status`：`started`（已派发，附 `job_id`，告知用户「CODE_MAP 描述已在后台生成，状态见 `jobs/<job_id>.json`」）、`already_running`（已有 worker 在跑，无需重复派发）、`error`（如 `not_a_git_repo`，回退到同步生成或提示用户）。

> `--refresh-bg` 的 worker 跑：CODE_MAP 结构 → 描述 → 共享缓存同步 → 根文档 CODE_MAP 块渲染（即耗时的 AI 部分），flock 锁保护、原子写，并**钉定到派发时的分支**（中途 `git checkout` 不会把刷新写到别的分支）。GitNexus `analyze` 已在 2.1 按 plan 同步完成（worker 内部的 `ensure_gitnexus_fresh` 只做一次幂等复查）。

CODE_MAP 存储模型：
- `CODE_MAP.md` 是 ignored local projection，不应作为 tracked 文件提交。
- 真实共享副本在 `~/.local/share/harness-hooks/codemaps/<project-key>/CODE_MAP.md`，`project-key` 基于 Git common dir，因此同一 repo 的 linked worktree 共享一份 cache。
- 根 `CLAUDE.md` / `AGENTS.md` 不依赖文件导入语义；CODE_MAP 内容渲染进托管 `<!-- codemap:start/end -->` 块。
- `CODE_MAP.counts.json` 是 harness cache 下的机器状态，用于 description-baseline symbol counts 和 stale threshold 判断；不进 Git，也不内联进平台文档。
- SessionStart 发现当前 worktree 缺少 `CODE_MAP.md` 时，会从共享 cache materialize 一份本地投影；cache 不存在时仅跳过，不阻塞会话。
- 如果 plan 的 `codemap_local_projection.tracked == true`，提示用户执行 `git rm --cached CODE_MAP.md` 后提交；后台 hook 不自动修改 git index。

描述规则：
1. 先识别目录类型：code_process / code_symbols / test / docs / example / artifact
2. code_process 才优先走 GitNexus+AI；tests/docs/examples/artifact 走确定性摘要
3. 格式：`{核心职责}：{2-3 个关键功能}`，中文 ≤ 50 字

质量规则：
- `📌` 手工描述永不覆盖
- `.harness/codemap_descriptions.json` 项目级 override 优先于自动生成
- 已有高质量描述在未过期时保留
- `load_module / load_module`、函数名列表、截断 token、`Tests for ... package` 等低质量描述视为待刷新
- AI 输出必须通过质量门禁才写入
- fallback 只能写可信 docstring；关键词 fallback 必须带 `⚠️` 低置信度标记，后续继续尝试刷新

执行规则：
- Hook 只调度后台 CODE_MAP job，不同步等待 GitNexus/AI
- 后台 job 状态写入 `~/.local/share/harness-hooks/jobs/*.json`
- CODE_MAP 本地投影写入使用临时文件 + 原子替换，失败时保留旧文件；写入后同步 harness cache
- Hook 会确保 `.gitignore` 包含 `CODE_MAP.md`；若历史上已 tracked，仍需显式 `git rm --cached CODE_MAP.md`
- AI+GitNexus 描述生成按小批次执行；后台 hook 固定用 `--ai-timeout 150`（手动 CLI 默认 `--batch-size 2 --ai-timeout 180`）
- 失败或超时的 AI batch 会自动拆成单目录 retry（顺序执行），timeout 不低于 240 秒
- 大项目可显式运行：`python3 ~/.local/share/harness-hooks/generate_descriptions.py . --generate --refresh-dir src/core --batch-size 2 --ai-timeout 180`
- 指纹增量检查：`python3 ~/.local/share/harness-hooks/generate_descriptions.py . --dry-run --use-fingerprints`
- `HARNESS_CODEMAP_AI_BATCH_SIZE` 调整 batch size（含后台）；`HARNESS_CODEMAP_AI_TIMEOUT` 只作用于手动 CLI（后台 timeout 固定 150s）
- AI 已尝试但失败时不写关键词 fallback，避免函数名列表冒充刷新结果
- 运行输出包含 `classification`、`ai_report`、`quality_before` / `quality_after`，用于审计刷新质量

#### 2.4 子目录文档（plan.subdirs）

子目录 `AGENTS.md` 只维护确定性 GitNexus facts block；不再 copy/sync 整文件，也不由 AI 自动写约束或危险操作 prose。

| action | 执行 |
|---|---|
| `refresh_facts` | `python3 ~/.local/share/harness-hooks/generate_subdir_harness.py . --refresh-facts --platform codex --dirs {目录列表}`；只替换 `<!-- harness:start/end -->` 内 facts |
| `rebaseline` | cache-only rebaseline，不改文档 |
| `bootstrap` | 仅手动 `/harness-init` 可创建缺失子目录文档 |
| `manual_migration` | 不自动改；人工先把旧块 prose 移到 `## 补充约束（手动维护）` |
| `skip` | 无操作 |

子目录 harness 块由 `generate_subdir_harness.py` 生成。后台只刷新已有 facts-only 块或 cache-only rebaseline；遇到旧 prose 块时只报告 `manual_migration_required`。手动迁移时必须先把旧块内容移到 `## 补充约束（手动维护）`，再写入新的 `## GitNexus 事实` 块。

#### 2.5 LSP（plan.lsp）

| action | 执行 |
|---|---|
| `skip` | 无操作 |
| `recommend` | 提示用户安装对应插件（询问确认） |

#### 2.6 Codex GitNexus 包装器（plan.codex_gitnexus_wrapper）

| action | 执行 |
|---|---|
| `skip` | 无操作（已配置 / 非 Codex / 无 hooks.json） |
| `fix` | 提示用户重新运行 `python3 <harness-init>/install.py` 安装并注册 Codex GitNexus 包装器；`status`/`reason` 字段说明具体问题（missing_wrapper / not_configured / self_test_failed 等） |

### 根 AGENTS.md 模板

```markdown
# {项目名} — {一句话定位}

## 构建与测试

{自动生成}

## CODE_MAP
<!-- codemap:start -->
# Code Map
<!-- codemap:end -->

## 关键领域概念

{3-5 个业务概念}

## 危险操作

{不可逆操作警告}

## 工具选择（GitNexus 已安装时生成此段）

GitNexus 擅长函数级调用链，不擅长文本搜索。以下场景直接用 grep/rg：
- 查变量/枚举/环境变量/字符串
- 查模块间 import 关系
- 模糊搜索/不确定符号名（先 grep/rg 确认，再用 GitNexus 查调用链）

<!-- gitnexus:start -->
<!-- gitnexus:end -->
```

总行数 ≤ 100 行。

### 子目录 AGENTS.md 模板

复杂模块（符号数 ≥ 100）可拥有独立事实文件。生成器只写 harness 托管块；人工约束写在块外。

输出模板：

```markdown
# {目录名}/ — {一句话职责}

## 测试

{测试命令，ls 验证路径存在}

<!-- harness:start -->
## GitNexus 事实

- 被调用: Parser: 40
- 相关模块: core: 3
- 相关流程: AnalyzeProject: 2
<!-- harness:end -->

## 补充约束（手动维护）
```

标记机制：`<!-- harness:start/end -->` 区域自动更新，标记外永不动。facts 块内容只来自 GitNexus/Cypher 结构化输出，不包含 AI prose。

### 参考：Hooks / GitNexus / LSP

Hooks、GitNexus、LSP 的决策已由 `harness-plan.py` 输出的 plan JSON 驱动，以下仅供参考。

| 组件 | 参考 |
|---|---|
| **Hooks** | harness_monitor.py (PostToolUse/Bash) + session_context.py (SessionStart) |
| **GitNexus** | plan.gitnexus 决定 skip/analyze/install。索引已最新时绝不跑 analyze |
| **LSP** | 仅 Claude Code 支持。plan.lsp 决定 skip/recommend |

LSP 插件参考表：

| 语言 | 门槛 | 插件名 |
|---|---|---|
| Python | 类型覆盖率 ≥ 30% | code-intelligence-python |
| 强类型（TS/Go/Rust/Java/Kotlin/C#/Swift） | 文件数 ≥ 30 | code-intelligence-{lang} |
| 弱类型（JS/Ruby/PHP） | 不推荐 | — |

### Step 3: 输出报告

```
🔧 驾具状态报告 — {项目名}

📊 语言分布
   {语言}: {行数} 行 ({占比}%) — {文件数} 个文件

📊 复杂度信号
   grep 噪声度: `{module}` → {N} 个文件
   类型覆盖: {N}%

📐 驾具金字塔
   Layer 1 CODE_MAP.md            {✅/🔧}
   Layer 1 CLAUDE.md              {✅/🔧}
   Layer 1 AGENTS.md              {✅/🔧}
   Layer 2 Hooks (Claude Code)    {✅/⚠️}
   Layer 2 Hooks (Codex)          {✅/⚠️}
   Layer 3 Skills                 {✅/⏭️}
   Layer 4 GitNexus               {✅/💡/⏭️}
   Layer 5 LSP (逐语言)
     {语言}                       {✅/💡/⏭️}
```

## 跨平台对照表

| 产出物 | 共享 | Claude Code | Codex |
|---|---|---|---|
| CODE_MAP.md | ✅ cache + ignored 投影 | 本地投影 | 本地投影 |
| 根 CODE_MAP 块 | 从 cache 渲染 | CLAUDE.md inline | AGENTS.md inline |
| .gitnexus/ | ✅ 一份 | | |
| Hook 脚本 | ✅ ~/.local/share/ | | |
| CLAUDE.md | | ✅ 生成 | 可读 |
| AGENTS.md | | 可读 | ✅ 生成 |
| Hook 注册 | | settings.json | hooks.json |
| MCP 注册 | | .claude.json | config.toml |
| LSP 插件 | | ✅ 支持 | ❌ 不支持 |

## 文档权威层级

```
项目已有文档（ARCHITECTURE.md / 合约文档 / README）  ← 最高
根 CLAUDE.md / AGENTS.md                            ← 手动维护
子目录 手动区域（harness 标记外）                     ← 手动维护
子目录 <!-- harness:start/end -->                    ← 自动生成
CODE_MAP.md                                          ← 自动生成的 ignored 本地投影 / cache 来源
```

## 注意事项

- `disable-model-invocation: true`：只能 `/harness-init` 手动触发
- 幂等：多次执行安全，脚本自动判断 skip/refresh
- 📌 前缀保护手动描述永不被覆盖
- `harness-plan.py` 输出的 JSON 是唯一的执行依据，AI 不自行判断 skip/copy/generate
