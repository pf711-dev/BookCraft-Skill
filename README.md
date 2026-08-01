# BookCraft

BookCraft 是一个将英文 EPUB 电子书或 Markdown 文件一键翻译成中英对照或纯中文格式的 skill。


## 功能

- **双格式支持**：目前仅支持 EPUB 和 Markdown 格式
- **无需配置**：复用当前对话模型，逐段翻译，无需配置第三方 API Key；
- **术语表统一译名**：自动提取全书专有名词候选（人名/地名/机构名），先翻译术语表再翻译正文，保证人名译名全书一致；
- **富文本保留**：保留原文的链接、加粗、图片等 DOM 结构，对照排版美观；
- **两种翻译类型**：`bilingual`（中英对照，默认）/ `chinese_only`（仅中文，标题保留英文）

## 安装

### 环境要求

- Python ≥ 3.9
- 一个支持 Skill 的 AI Agent 运行环境（如 Claude Code、Codex、OpenCode、Zcode等）

### 快捷安装（让 AI 助手代劳）

最省事的方式——把下面这句话发给你的 AI 助手，它会自动下载、安装并配置依赖：

> 请帮我安装 BookCraft 翻译工具：执行 `curl -fsSL https://raw.githubusercontent.com/pf711-dev/BookCraft-Skill/main/install.sh | bash`，完成后报告结果。

或者你自己跑一行命令（无需手动 clone）：

```bash
curl -fsSL https://raw.githubusercontent.com/pf711-dev/BookCraft-Skill/main/install.sh | bash
```

安装脚本会自动：复制 BookCraft 到 `~/.agents/skills/BookCraft` → 安装 Python 依赖 → 验证环境。

### 手动安装

如果你想自己控制每一步：

1. 克隆仓库到 Agent 的 skills 目录：

   ```bash
   git clone https://github.com/pf711-dev/BookCraft-Skill.git ~/.agents/skills/BookCraft
   ```

2. 安装 Python 依赖：

   ```bash
   pip3 install -r ~/.agents/skills/BookCraft/requirements.txt
   ```

3. 首次使用时，Agent 会自动检查依赖是否就绪。

## 🚀 使用

安装完成后，直接在支持 Skill 的 AI 助手对话里发起翻译请求即可，无需手动调用脚本：

| 你说 | 行为 |
|------|------|
| 「翻译 book.epub」 | 执行完整翻译工作流 |
| 「把这个 Markdown 翻译成中文」 | 翻译 Markdown |
| 「翻译 book.epub，只要中文」 | 使用 `chinese_only` 类型 |

Agent 会自动：提取段落 → 翻译术语表 → 分批翻译正文 → 组装最终文件，并告知你输出路径。

> 💡 不使用 Skill 框架？`scripts/translate.py` 也可独立运行。它提供 `extract`（提取段落到 JSON）和 `build`（把翻译结果组装回原格式）两个子命令，中间的翻译步骤你可以接入任意模型。详见脚本头部文档。

### 手动术语表（可选）

若想固定某些人名/地名/机构名的译名，编辑用户术语表：

```bash
# 文件路径：~/.bookcraft/glossary.json
{
  "Elizabeth": "伊丽莎白",
  "William": "威廉"
}
```

翻译时会自动读取该文件，且**优先于自动生成的术语表**使用，保证全书一致。



## ⚠️ 边界

- 只翻译**英文 → 中文**，不支持其他语言对
- 只处理 **EPUB 和 Markdown** 两种格式
- 不做 OCR，不处理扫描版 PDF（如需翻译 PDF，请先转换为 EPUB）
- 翻译质量取决于驱动模型的翻译能力，不提供人工审校

## 📄 许可证

[MIT](LICENSE)
