# harness-init

项目驾具生命周期管理器 — 为 AI 编程工具（Claude Code / Codex）自动配置最优开发环境。

## 功能

- **CODE_MAP.md 自动维护**：GitNexus 知识图谱结构 + AI 语义描述，后台 job 原子更新
- **CLAUDE.md / AGENTS.md 生成**：项目约束 + `@CODE_MAP.md` 引用
- **子目录约束文件**：自底向上生成，`<!-- harness:start/end -->` 增量更新
- **SessionStart Hook**：新会话自动注入 git 状态 + 模块映射
- **GitNexus / LSP 推荐**：实测 grep 噪声度和类型覆盖率，按需建议
- **项目成长检测**：文件增量达阈值时诊断，推荐升级工具链
- **跨平台**：Claude Code + Codex 对等支持
- **安全默认**：Hook 只在 main 分支写文件，feature 分支只通知

## 前置依赖

| 依赖 | 必须 | 用途 |
|---|---|---|
| **Python 3** | ✅ | 诊断脚本 + Hook 脚本 |
| **Node.js 18+** | ✅ | GitNexus 运行环境 |
| **GitNexus** | 推荐 | 知识图谱 + CODE_MAP 描述 + 搜索增强。未安装时降级 |
| Claude Code 或 Codex | 至少一个 | AI 编程平台 |

## 安装

```bash
# 普通用户（复制模式）
python3 install.py

# 开发者（符号链接模式 — 改源码立即生效）
python3 install.py --link
```

install.py 自动检测依赖，一键安装 GitNexus（可选）。

## 使用

```bash
/harness-init    # 在任何项目中执行
```

每次执行：诊断项目 → 生成/刷新 CODE_MAP.md → 检查 GitNexus/LSP → 输出报告。

## 文件结构

```
harness-init/
├── scripts/
│   ├── harness_shared.py         ← 公共常量和工具函数
│   ├── harness_init.py          ← 项目诊断（JSON 输出）
│   ├── harness_plan.py          ← 执行计划生成（JSON action plan）
│   ├── sync_docs.py             ← 跨平台文档同步
│   ├── harness_monitor.py       ← PostToolUse Hook
│   ├── generate_descriptions.py ← CODE_MAP 描述生成
│   └── session_context.py       ← SessionStart Hook
├── skills/
│   ├── claude/SKILL.md          ← Claude Code 执行规范
│   └── codex/SKILL.md           ← Codex 执行规范
├── install.py                   ← 安装（--link 开发者模式）
├── uninstall.py                 ← 卸载
├── VERSION                      ← 版本号
└── LICENSE                      ← MIT
```

## 安装后的 Hooks

| Hook | 事件 | 功能 |
|---|---|---|
| harness_monitor.py | PostToolUse [Bash] | main 分支 git 操作后：后台调度 CODE_MAP + 子目录 + 成长检测 |
| session_context.py | SessionStart [startup\|clear] | 注入 git 状态 + 模块映射 |
| gitnexus-hook.cjs | PreToolUse [Grep\|Glob\|Bash] | GitNexus 搜索增强（第三方） |

CODE_MAP 描述生成带质量门禁：`📌` 描述永不覆盖；函数名列表、截断 token、
`load_module / load_module`、`Tests for ... package` 等低质量描述会进入待刷新队列。
AI+GitNexus 按小批次运行，默认超时 180 秒；可用 `--batch-size`、`--max-workers`、
`--ai-timeout` 或 `HARNESS_CODEMAP_AI_*` 环境变量调整。fallback 只在 AI 不可用时写
可信 docstring/`⚠️` 关键词；AI 已尝试但失败时不会用函数名关键词覆盖结果。
每次运行都会输出 `quality_before` / `quality_after` 质量报告。
后台任务状态写入 `~/.local/share/harness-hooks/jobs/*.json`。

## 卸载

```bash
python3 uninstall.py
```
