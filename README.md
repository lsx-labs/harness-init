# harness-init

项目驾具生命周期管理器 — 为 AI 编程工具（Claude Code / Codex）自动配置最优开发环境。

## 功能

- **CLAUDE.md / AGENTS.md 生成**：项目约束 + `@CODE_MAP.md` 引用
- **CODE_MAP.md 自动维护**：GitNexus 知识图谱结构 + AI 语义描述，Hook 自动更新
- **GitNexus / LSP 推荐**：实测 grep 噪声度和类型覆盖率，按需建议安装
- **项目成长检测**：每 20 个新文件自动诊断，推荐合适时机升级工具链
- **跨平台**：Claude Code + Codex 对等支持

## 安装

```bash
bash install.sh
```

## 使用

在任何项目中：
```
/harness-init
```

## 卸载

```bash
bash uninstall.sh
```

## 文件结构

```
harness-init/
├── scripts/
│   ├── harness-init.sh      ← 核心诊断脚本（跨平台，JSON 输出）
│   └── harness-monitor.py   ← 统一 Hook（CODE_MAP 更新 + 项目成长检测）
├── skills/
│   ├── claude/SKILL.md      ← Claude Code 入口（完整执行逻辑）
│   └── codex/SKILL.md       ← Codex 入口
├── install.sh               ← 一键安装
├── uninstall.sh             ← 一键卸载
└── README.md
```

## 安装后的文件分布

```
共享层
├── ~/.local/bin/harness-init.sh
└── ~/.local/share/harness-hooks/harness-monitor.py

Claude Code
├── ~/.claude/skills/harness-init/SKILL.md
└── ~/.claude/settings.json  (PostToolUse hook)

Codex
├── ~/.codex/skills/harness-init/SKILL.md
└── ~/.codex/hooks.json  (PostToolUse hook)
```
