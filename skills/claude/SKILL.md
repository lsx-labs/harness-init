---
name: harness-init
description: "项目驾具生命周期管理：首次执行初始化基础架构，后续执行检查健康状态并根据项目成熟度推荐升级。触发：/harness-init 或用户说「初始化项目」「配置驾具」「检查驾具」「harness 状态」"
disable-model-invocation: true
---

# Harness Init — 项目驾具生命周期管理

## 架构

```
共享层（平台无关）
├── ~/.local/bin/harness-init.sh                        ← 核心诊断脚本（JSON 输出）
├── ~/.local/share/harness-hooks/harness-monitor.py     ← 统一 Hook（CODE_MAP 更新 + 项目成长检测）
└── 项目/CODE_MAP.md                                    ← 独立文件，两边引用

平台入口
├── ~/.claude/skills/harness-init/SKILL.md              ← 本文件
└── ~/.codex/skills/harness-init/SKILL.md               ← Codex 入口
```

## 核心原则

- **渐进式构建**：根据实测复杂度信号判断，不提前堆叠
- **多语言感知**：每种语言独立评估
- **跨平台对等**：CLAUDE.md / AGENTS.md 同时维护，CODE_MAP.md 共享
- **实测优于拍数字**：grep 噪声度、类型覆盖率

## 执行流程

### Step 1: 运行诊断

```bash
bash ~/.local/bin/harness-init.sh .
```

输出 JSON：`languages` / `grep_noise` / `type_coverage` / `lsp_assessment` / `existing`

向用户展示诊断结果，确认后继续。

### Step 2: 五层驾具逐层处理

#### Layer 1: CLAUDE.md + AGENTS.md + CODE_MAP.md

**三个文件职责分离**：

| 文件 | 谁读 | 维护方式 | 内容 |
|---|---|---|---|
| `CODE_MAP.md` | 两边都读 | Hook 自动 | 目录/模块级导航索引 |
| `CLAUDE.md` | Claude Code | 本 Skill + GitNexus | 项目约束 + `@CODE_MAP.md` |
| `AGENTS.md` | Codex | 本 Skill + GitNexus | 项目约束 + `@CODE_MAP.md` |

**Code Map 是独立文件**。CLAUDE.md 和 AGENTS.md 通过 `@CODE_MAP.md` 引用，单一数据源。

**检查与行动**：
- `CODE_MAP.md` 不存在 → `python3 ~/.local/share/harness-hooks/harness-monitor.py` 生成骨架（通过模拟 Bash 触发）
- `CODE_MAP.md` 存在但有空描述 → **AI 补全描述**（见下方）
- `CLAUDE.md` / `AGENTS.md` 不存在 → 生成（模板见下方）
- 已存在但缺少 `@CODE_MAP.md` → 追加引用行

**AI 补全 Code Map 描述（核心步骤）**：

Hook 自动生成的 CODE_MAP.md 只有目录名和符号数，没有语义描述。本 Skill 执行时负责补全：

1. 读取 CODE_MAP.md，找到所有缺少 `—` 描述的条目
2. 对每个缺描述的目录，读取其中的 README.md 或 2-3 个核心源文件的 docstring
3. 用一句话总结该目录的职责（中文，≤ 50 字）
4. 写回 CODE_MAP.md

**示例**：
```
补全前: ### autoresearch/ (4394 symbols)
补全后: ### autoresearch/ (4394 symbols) — 闭环因子研究平台：实验编排/权重优化/分布式执行

补全前: - **distributed/** (1529 symbols)
补全后: - **distributed/** — coordinator + worker 分布式执行框架 (1529 symbols)

补全前: - **_lib/** (332 symbols)
补全后: - **_lib/** — 核心共享库：baseline 契约/路径/回测引擎/执行计划 (332 symbols)
```

**保护规则**：已有描述的条目不覆盖。后续 Hook 更新时只刷新符号数，保留人工描述。

**CLAUDE.md / AGENTS.md 模板**（内容一致）：

```markdown
# {项目名} — {一句话定位}

## 构建与测试

{自动生成}

@CODE_MAP.md

## 关键领域概念

{3-5 个业务概念}

## 危险操作

{不可逆操作警告}

## Code Map 描述自动更新

当 Hook 输出包含 stale_descriptions 和 action 字段时，立即用 subagent 执行：
1. 读取列出的目录的 2-3 个核心源文件
2. 用一句话（中文，≤50 字）总结该目录职责
3. 更新 CODE_MAP.md 对应条目，替换 ⚠️ 标记
不需要用户确认，直接执行。

<!-- gitnexus:start -->
<!-- gitnexus:end -->
```

总行数 ≤ 100 行（CODE_MAP.md 按需加载，不占 CLAUDE.md 行数）。

#### Layer 2: Hooks

**前置检查：GitNexus Hook 脚本可达性**

`gitnexus setup` 把 Hook 脚本安装到 `~/.claude/hooks/gitnexus/`（硬编码路径）。
纯 Codex 环境下 `~/.claude/` 目录不存在，`gitnexus setup` 会跳过安装。

检测并修复：
```bash
# 如果 gitnexus-hook.cjs 不存在（纯 Codex 环境）
if [ ! -f ~/.claude/hooks/gitnexus/gitnexus-hook.cjs ]; then
    mkdir -p ~/.claude/hooks/gitnexus
    # 从 gitnexus npm 包中复制 hook 脚本
    cp "$(npm root -g)/gitnexus/hooks/claude/gitnexus-hook.cjs" ~/.claude/hooks/gitnexus/
fi
```

**Hook 完整性检查**：

两个平台共用同一套 Hook，内容相同：
- PreToolUse [Grep|Glob|Bash] → `~/.claude/hooks/gitnexus/gitnexus-hook.cjs`（第三方）
- PostToolUse [Bash|Write] → `~/.local/share/harness-hooks/harness-monitor.py`（自定义）

**Claude Code** (`~/.claude/settings.json`) + **Codex** (`~/.codex/hooks.json`)：逐项检查，缺失 → 给出修复命令。

#### Layer 3: Skills

跟随 Layer 4（GitNexus analyze 自动生成）。

#### Layer 4: GitNexus MCP

**判断**：`grep_noise.grep_noise_files`

| grep 噪声 | 判断 |
|---|---|
| ≤ 10 | ⏭️ 不需要 |
| 11-20 | 💡 可选 |
| > 20 | 💡 建议安装 |

**跨平台检查**：`existing.mcp_claude` + `existing.mcp_codex`，任一侧缺失给出注册命令。

**行动**：建议安装 → `npx gitnexus analyze`（一次索引两边共享）。

#### Layer 5: LSP / Code Intelligence

**判断**：`lsp_assessment` 数组，逐语言展示。

| 语言类别 | 判断依据 |
|---|---|
| 强类型（TS/Go/Rust/Java/Kotlin/C#/Swift） | 文件数 ≥ 30 |
| Python | 类型覆盖率 ≥ 30% |
| C/C++ | 文件数 ≥ 30 + compile_commands.json |
| 弱类型（JS/Ruby/PHP） | 不推荐 |

**LSP 插件安装命令因平台而异**，分别给出。

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

## 注意事项

- `disable-model-invocation: true`：只能 `/harness-init` 手动触发
- 幂等：多次执行安全
- 不强制：建议附测量数据，用户确认才执行
- 核心脚本只诊断不修改文件，所有写操作由本 Skill 控制
