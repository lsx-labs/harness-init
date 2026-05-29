---
name: harness-init
description: "项目驾具生命周期管理：首次执行初始化基础架构，后续执行检查健康状态并根据项目成熟度推荐升级。触发：/harness-init 或用户说「初始化项目」「配置驾具」「检查驾具」「harness 状态」"
disable-model-invocation: true
---

# Harness Init — 项目驾具生命周期管理

> **当前平台：Claude Code** — 生成 `CLAUDE.md`，不生成 `AGENTS.md`。
> 子目录同理：只生成 `*/CLAUDE.md`。

## 架构

```
共享层（平台无关）
├── ~/.local/bin/harness-init.py                             ← 项目诊断（JSON 输出）
├── ~/.local/bin/harness-plan.py                             ← 执行计划生成（JSON action plan）
├── ~/.local/bin/sync-docs.py                                ← 跨平台文档同步（CLAUDE.md ↔ AGENTS.md）
├── ~/.local/share/harness-hooks/harness_monitor.py          ← PostToolUse Hook（CODE_MAP + 子目录 + 成长检测）
├── ~/.local/share/harness-hooks/generate_descriptions.py    ← CODE_MAP 描述生成（AI+GitNexus / fallback）
├── ~/.local/share/harness-hooks/session_context.py          ← SessionStart Hook（git 状态注入）
└── 项目/CODE_MAP.md                                         ← 独立导航文件，两边引用

平台入口
├── ~/.claude/skills/harness-init/SKILL.md                   ← 本文件
└── ~/.codex/skills/harness-init/SKILL.md                    ← Codex 入口
```

## 核心原则

- **渐进式构建**：根据实测复杂度信号判断，不提前堆叠
- **多语言感知**：每种语言独立评估 LSP 价值
- **跨平台对等**：CLAUDE.md / AGENTS.md 同时维护，CODE_MAP.md 共享
- **实测优于拍数字**：grep 噪声度、类型覆盖率
- **确定性执行**：Hook 通过 AI CLI（claude -p / codex exec）直接完成更新，不依赖概率性消息

## 执行流程

**脚本驱动**：所有确定性决策由脚本完成，AI 只执行需要理解力的部分。

### Step 1: 生成执行计划（脚本，不暂停）

```bash
python3 ~/.local/bin/harness-plan.py . --platform claude
```

输出 JSON action plan，包含所有后续步骤的确定性决策。示例：

```json
{
  "platform": "claude",
  "doc_file": "CLAUDE.md",
  "root_doc": {"action": "copy", "from": "AGENTS.md"},
  "codemap": {"action": "refresh", "dirs_needing": ["src/core"]},
  "gitnexus": {"action": "analyze"},
  "subdirs": {
    "copy": [{"dir": "src/api", "from": "AGENTS.md"}],
    "generate": [{"dir": "src/utils", "depth": 1}],
    "skip": ["src/common"],
    "layers": [[1, ["src/utils"]]]
  },
  "lsp": [{"language": "Python", "action": "recommend", "plugin": "..."}]
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

| action | 执行 |
|---|---|
| `skip` | 无操作 |
| `copy` | `python3 ~/.local/bin/sync-docs.py . --platform claude` |
| `generate` | AI 按模板生成（见下方模板） |

#### 2.3 CODE_MAP 描述（plan.codemap）

| action | 执行 |
|---|---|
| `skip` | 无操作 |
| `refresh` | 对 `dirs_needing` 中的目录按 provider 生成描述 |

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
- CODE_MAP 写入使用临时文件 + 原子替换，失败时保留旧文件
- AI+GitNexus 描述生成按小批次执行，默认 `--batch-size 2 --ai-timeout 180 --max-workers 1`
- 大项目可显式运行：`python3 ~/.local/share/harness-hooks/generate_descriptions.py . --generate --refresh-dir tests/autoresearch --batch-size 2 --max-workers 2 --ai-timeout 180`
- 指纹增量检查：`python3 ~/.local/share/harness-hooks/generate_descriptions.py . --dry-run --use-fingerprints`
- 可用 `HARNESS_CODEMAP_AI_BATCH_SIZE`、`HARNESS_CODEMAP_AI_MAX_WORKERS`、`HARNESS_CODEMAP_AI_TIMEOUT` 调整后台默认值
- AI 已尝试但失败时不写关键词 fallback，避免函数名列表冒充刷新结果
- 运行输出包含 `classification`、`ai_report`、`quality_before` / `quality_after`，用于审计刷新质量

#### 2.4 子目录文档（plan.subdirs）

**先 copy，再 generate，不暂停。**

**Copy**（脚本完成）：`python3 ~/.local/bin/sync-docs.py . --platform claude --dirs {copy列表}`

**Generate**（AI 完成，按 layers 逐层并行）：

按 `plan.subdirs.layers` 从深到浅执行，同层并行 spawn 子 Agent：

```
layers: [[2, ["src/utils/parser"]], [1, ["src/utils"]]]

Layer 2: spawn 子 Agent 生成 src/utils/parser/CLAUDE.md（并行）
  ↓ 等待完成
Layer 1: spawn 子 Agent 生成 src/utils/CLAUDE.md（可复用下层）
```

每个子 Agent prompt 包含：目录路径、项目名、输出模板。

#### 2.5 LSP（plan.lsp）

| action | 执行 |
|---|---|
| `skip` | 无操作 |
| `recommend` | 提示用户安装对应插件（询问确认） |

### 根 CLAUDE.md 模板

```markdown
# {项目名} — {一句话定位}

## 构建与测试

{自动生成}

@CODE_MAP.md

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

### 子目录 CLAUDE.md 模板

复杂模块（符号数 ≥ 100）生成独立约束文件。

数据源：**所有约束和危险操作必须先通过 GitNexus 查询获取事实，再由 AI 总结。禁止 AI 直接读代码推断。**

**约束生成流程（严格 GitNexus 驱动）**：

```
Step 1: gitnexus_context({目录核心函数})
  → 获取：callers / callees / 参与的执行流
  → AI 从返回数据中识别：公开 API 契约（incoming calls 多 = 签名不可改）

Step 2: gitnexus_impact({目录核心符号}, direction=upstream)
  → 获取：影响节点数 / risk 等级 / affected_modules
  → AI 从返回数据中识别：高扇入符号 = 约束（改签名需排查 N 个调用者）

Step 3: gitnexus_query({目录名})
  → 获取：相关执行流列表
  → AI 从返回数据中识别：跨模块依赖约束
```

每条约束必须标注**GitNexus 查询来源**：
```
- `load_baseline_contract` 被 13 个函数调用（gitnexus_context 返回 13 incoming calls）
  → 改签名需排查 status.py/post_oos.py/vbt_runner.py 等（见 baseline_contract.py L92）
```

**危险操作生成流程（严格 GitNexus 驱动）**：

```
Step 1: gitnexus_impact({目录所有公开函数}, direction=upstream)
  → 筛选：risk=HIGH 或 impactedCount > 10 的符号

Step 2: 对每个高风险符号，AI 读该函数源码（仅该函数，不读整个文件）
  → 判断"为什么危险"（写操作？状态修改？不可逆？）

Step 3: 组合 GitNexus 数据 + 源码判断 → 写危险描述
```

格式：`**{文件名}**: {为什么危险}（gitnexus_impact: {N} 个调用者, risk={LEVEL}）`

**禁止**：
- ❌ AI 直接通读代码文件推断约束（绕过 GitNexus）
- ❌ 没有 GitNexus 查询数据支撑的约束
- ❌ 没有 impact 分析的危险操作

**降级**：GitNexus 不可用时，不生成约束和危险操作（留空），只生成测试命令。

输出模板：

```markdown
# {目录名}/ — {一句话职责}

## 测试

{测试命令，ls 验证路径存在}

<!-- harness:start -->
## 约束（基于 GitNexus 事实）

- {符号名} 被 {N} 个函数调用（gitnexus_context: {N} incoming）→ {约束}（见 {文件名}）

## 危险操作（基于 GitNexus impact 分析）

- **{文件名}**: {为什么危险}（gitnexus_impact: {N} callers, risk={LEVEL}）
<!-- harness:end -->

## 补充约束（手动维护）
```

标记机制：`<!-- harness:start/end -->` 区域自动更新，标记外永不动。

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
| CODE_MAP.md | ✅ 一份 | @引用 | @引用 |
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
CODE_MAP.md                                          ← 自动生成
```

## 注意事项

- `disable-model-invocation: true`：只能 `/harness-init` 手动触发
- 幂等：多次执行安全，脚本自动判断 skip/refresh
- 📌 前缀保护手动描述永不被覆盖
- `harness-plan.py` 输出的 JSON 是唯一的执行依据，AI 不自行判断 skip/copy/generate
