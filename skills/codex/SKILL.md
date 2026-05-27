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
├── ~/.local/bin/harness-init.py                             ← 核心诊断脚本（JSON 输出）
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

### Step 1: 运行诊断 → Step 2: 逐层处理（连续执行，不暂停）

```bash
python3 ~/.local/bin/harness-init.py .
```

输出 JSON（schema_version: 1）：`languages` / `grep_noise` / `type_coverage` / `lsp_assessment` / `existing`

**诊断完成后立即进入 Step 2，不暂停等待用户确认。** 全部执行完毕后，在最终报告（Step 3）中一并展示诊断数据和执行结果。

只有涉及全局环境变更的操作（安装 GitNexus、安装 LSP 插件）才暂停询问用户。

### 五层驾具逐层处理

#### Layer 1: CODE_MAP.md + CLAUDE.md + AGENTS.md

**四类文件，三种维护方式**：

| 文件 | 谁读 | /harness-init 时 | 日常 Hook |
|---|---|---|---|
| `CODE_MAP.md` | 两边 | **按需刷新（空描述 + 变化 ≥ 20%）** | 只更新符号数 + AI CLI 刷新描述 |
| 根 `CLAUDE.md` / `AGENTS.md` | 两边 | 首次生成，之后不修改 | 不自动修改 |
| 子目录 `*/CLAUDE.md` / `*/AGENTS.md` | 进入时 | 自底向上生成/增量更新 | 符号数变化 ≥ 20% → AI CLI 更新 harness 区域 |

**CODE_MAP.md 描述生成（按需刷新）**：

`/harness-init` 执行时，逐条判断是否需要生成/刷新描述：

| 条目状态 | 动作 |
|---|---|
| 📌 前缀 | 跳过（永不覆盖） |
| 无描述（空） | **生成**（查 GitNexus + 写描述） |
| 有描述 + 符号数变化 ≥ 20% | **重新生成**（内容可能过期） |
| 有描述 + 符号数变化 < 20% | **跳过**（保留现有描述） |

生成规则：
1. 对该目录调用 `gitnexus_context` 查询核心函数
2. 基于 GitNexus 返回的事实写描述，不自行推测
3. 格式：`{核心职责}：{2-3 个关键功能}`，中文 ≤ 50 字

首次执行（所有条目为空）= 全量生成。后续执行 = 只刷新有变化的。
5. 生成成本低：每目录 1 次 MCP 查询

日常 Hook（main 分支 git 操作后）：
- 更新符号数 + 调 AI CLI 刷新描述
- AI CLI 不可用时降级为 docstring / 关键词（**只填空，不覆盖已有**）

**根 AGENTS.md**（本平台生成）：

生成策略：
1. `AGENTS.md` 已存在 → **跳过**
2. `AGENTS.md` 不存在但 `CLAUDE.md` 存在 → **复制 `CLAUDE.md` 为 `AGENTS.md`**（内容一致，跨平台复用）
3. 都不存在 → **从模板生成**

**同步规则**：更新 `AGENTS.md` 后，如果 `CLAUDE.md` 也存在，将更新内容同步复制过去（Hook 自动执行）。

初始化写一次，之后手动维护。只有 GitNexus 更新其 `<!-- gitnexus:start/end -->` 标记区域。

**模板**（CLAUDE.md 和 AGENTS.md 内容一致）：

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

**子目录 AGENTS.md（渐进式披露）**：

生成策略同根文件：已有 → 跳过，对方平台文件存在 → 复制，都没有 → 生成。同步规则同上：两边都有时，更新后自动复制保持一致。

**不暂停询问用户，直接生成。** 即使需要多次 GitNexus 查询，也连续执行到完成。这是 /harness-init 的一部分，不需要单独确认。

复杂模块（符号数 ≥ 100）生成独立约束文件。自底向上策略：

```
autoresearch/                  ← 最后生成（复用子层总结）
├── distributed/               ← Step 1（叶子层，读源码 + GitNexus）
├── weight_grid/               ← Step 2
├── continuous/                ← Step 3
├── _lib/                      ← Step 4
└── qdata/                     ← Step 5
```

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

#### Layer 2: Hooks

**两个 Hook，两个平台对齐注册**：

| Hook | 事件 | matcher | 功能 |
|---|---|---|---|
| harness_monitor.py | PostToolUse | Bash | git 操作后：CODE_MAP 更新 + 子目录更新 + 成长检测 |
| session_context.py | SessionStart | startup\|clear | 注入 git 状态 + 模块映射 + harness 健康 |

第三方 Hook（GitNexus 管理）：
- PreToolUse [Grep|Glob|Bash] → gitnexus-hook.cjs（搜索增强）

前置检查：纯 Codex 环境下 `~/.claude/hooks/gitnexus/gitnexus-hook.cjs` 可能不存在 → install.py 自动复制。

#### Layer 3: Skills

跟随 Layer 4（GitNexus analyze 自动生成 6 个 Skills）。

#### Layer 4: GitNexus MCP

确保 GitNexus 可用且当前项目已索引：

```
已安装？ → NO + grep 噪声 > 20 → 提示安装
已索引？（existing.gitnexus.indexed）
  NO  → npx gitnexus analyze（首次索引）
  YES → 检查 existing.gitnexus.up_to_date
        up_to_date=true  → ✅ 跳过（不跑 analyze，节省 25s+）
        up_to_date=false → npx gitnexus analyze（增量更新）
```

**重要：索引已最新时绝不跑 `gitnexus analyze`。** 即使增量模式也需 25s+ 启动开销。

| grep 噪声 | 判断 |
|---|---|
| ≤ 10 | ⏭️ 不需要 |
| 11-20 | 💡 可选 |
| > 20 | 💡 建议安装 |

#### Layer 5: LSP / Code Intelligence

**仅 Claude Code 支持。** Codex 无 LSP 插件体系。

先检查是否已安装（全局，跨项目）→ 已装跳过。

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
- 幂等：多次执行安全，每次刷新描述确保最新
- 📌 前缀保护手动描述永不被覆盖
- AI CLI 超时 15s（< Hook 20s 截止时间），超时静默降级到 fallback
