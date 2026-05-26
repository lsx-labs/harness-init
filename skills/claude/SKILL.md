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

**子目录 CLAUDE.md / AGENTS.md（渐进式披露）**：

复杂模块需要独立的约束文件。进入子目录时自动加载，不在该目录工作时不消耗 context。

**触发条件**：CODE_MAP.md 中符号数 ≥ 100 的目录。

**生成策略：自底向上**

多层嵌套目录时从最深层开始生成，父层复用子层总结，不重复读源码：

```
autoresearch/                  ← 最后生成（复用子层总结）
├── distributed/               ← Step 1（叶子层，读源码）
├── weight_grid/               ← Step 2（叶子层）
├── continuous/                ← Step 3（叶子层）
├── _lib/                      ← Step 4（叶子层）
└── qdata/                     ← Step 5（叶子层）
```

执行流程：
1. 从 CODE_MAP.md 筛出符号数 ≥ 100 的目录
2. 按目录深度排序（最深优先）
3. 对每个目录：
   - 不存在 → 全量生成（含 `<!-- harness:start/end -->` 标记）
   - 已存在 → **增量更新**：只重写 `<!-- harness:start -->` 到 `<!-- harness:end -->` 之间的内容，标记外的手动内容保留不动
4. 叶子层：读源码 + GitNexus 查询生成
5. 父层：读子层已生成的 CLAUDE.md + 补充跨模块约束

**数据源：GitNexus 优先，grep 降级**

叶子层目录读取规则（最多 5 个文件，≤ 500 行）：

| GitNexus 优先 | grep 降级 |
|---|---|
| `gitnexus context {核心函数}` → 调用者/被调用者 | 读 `__init__.py` + 被 import 最多的文件 |
| `gitnexus impact {高扇入符号} -d upstream` → 影响范围 | `grep -rl` 统计引用数 |
| 社区数据 → 按符号数排序确定核心文件 | `grep -c` 估算 |

父层目录读取规则：
1. 读所有子层 CLAUDE.md（已生成，token 极少）
2. 读该层直属 .py 文件的 docstring
3. GitNexus 查跨子模块调用关系

**各 section 提取规则**

`## 测试`：
- 查 `tests/{module}/` 路径是否存在 → 构造 `pytest tests/{module}/ -v`
- **必须 `ls` 验证路径存在**，不存在标注 `⚠️ 测试目录未找到`

`## 约束`（逐项检查，有则写无则跳，最终 3-5 条）：

| 约束类型 | GitNexus 方式 | grep 降级 |
|---|---|---|
| 公开 API 契约 | `context {类}` → incoming calls 多 = 签名不可随意改 | `grep -rl` 统计引用 |
| 注册模式 | `query "register"` → 注册流程 | `grep "@register\|__all__"` |
| 类继承约束 | `context {基类}` → extends 关系 | `grep "class.*(Base\|ABC)"` |
| 配置耦合 | 检查目录内 JSON/YAML 是否被代码读取 | `grep "json.load\|yaml.load"` |
| 状态依赖 | `impact {状态函数}` → 影响链 | `grep "global\|singleton"` |

**硬约束**：
- ✅ 每条必须附代码出处：`（见 {文件名} {符号名}）`
- ❌ 禁止通用废话（"保持代码整洁"）
- ❌ 禁止与根 CLAUDE.md 重复
- ❌ 禁止编造代码中不存在的模式

`## 危险操作`：
- GitNexus `impact -d upstream` → 调用者 > 10 的符号 = 高风险文件
- 查 write/delete/persist 相关操作
- 查并发/锁相关代码
- **必须指明**：具体文件名 + 为什么危险 + 影响 N 个调用者

**输出模板**

```markdown
# {目录名}/ — {一句话职责，来自 CODE_MAP.md}

## 测试

{精确到该模块的测试命令，路径已验证}

<!-- harness:start -->
## 约束（自动生成，基于代码分析）

- {约束 1}（见 {文件名} {符号名}）
- {约束 2}（见 {文件名}）
- ...

## 危险操作（自动生成）

- **{文件名}**: {为什么危险}，影响 {N} 个外部调用者
<!-- harness:end -->

## 补充约束（手动维护，自动更新不会覆盖此区域）

{用户/团队手动添加的约束，如部署流程、团队约定等}
```

**标记机制**：
- `<!-- harness:start -->` 到 `<!-- harness:end -->`：自动生成区域，增量更新时重写
- 标记外的内容（标题、测试、补充约束）：永远不动
- 首次生成时自动带上标记；已有文件增量更新时只替换标记内的内容

叶子层自动区域 ≤ 20 行，父层 ≤ 15 行。手动区域不限。

**增量更新逻辑**：
1. 读取现有文件
2. 找到 `<!-- harness:start -->` 和 `<!-- harness:end -->` 标记
3. 只替换标记之间的内容（基于最新代码分析）
4. 标记外的所有内容（标题、测试命令、补充约束）原封不动
5. 如果标记不存在（旧格式文件）→ 在约束段前后插入标记，保留现有内容

**生成后自检清单**：
1. ✅ 测试命令路径存在（`ls` 验证）
2. ✅ 每条约束的 `（见 xxx）` 引用文件存在
3. ✅ 与根 CLAUDE.md 无重复
4. ✅ 无通用废话
5. ✅ CLAUDE.md 和 AGENTS.md 内容完全一致
6. ✅ `<!-- harness:start/end -->` 标记完整成对
7. ✅ 手动区域内容未被修改

**与 CODE_MAP.md 的关系**：
- CODE_MAP.md = 全局导航（一行一模块，"这里有什么"）
- 子目录 CLAUDE.md/AGENTS.md = 模块深度约束（"在这里怎么干活"）
- 不重叠，互补

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
