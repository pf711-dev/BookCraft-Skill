# BookCraft 📖

将英文 EPUB 电子书或 Markdown 文件翻译成**中英对照**版本的双语翻译工具。

BookCraft 是一个 **AI Agent Skill**：它把「解析文件 → 分批调用模型翻译 → 组装回原格式」的流程封装好，让 AI 助手（如 ZCode、Claude Code 等）直接驱动翻译，**无需配置任何 API Key，开箱即用**。

## ✨ 特性

- **双格式支持**：EPUB（输出带双语 CSS 样式的 EPUB）、Markdown（输出 `>` 引用块标注的对照版）
- **零配置翻译**：复用当前对话模型，逐段翻译，无需申请第三方 API Key
- **术语表统一译名**：自动提取全书专有名词候选（人名/地名/机构名），先翻译术语表再翻译正文，保证人名译名全书一致；支持手动术语表覆盖
- **富文本保留**：保留原文的链接、加粗、图片等 DOM 结构，对照排版美观
- **两种翻译类型**：`bilingual`（中英对照，默认）/ `chinese_only`（仅中文，标题保留英文）

## 📦 安装

### 环境要求

- Python ≥ 3.9
- 一个支持 Skill 的 AI Agent 运行环境（如 ZCode）

### 步骤

1. 把本仓库放到 Agent 的 skills 目录下：

   ```bash
   # ZCode 用户
   cp -r BookCraft ~/.agents/skills/
   ```

2. 安装 Python 依赖：

   ```bash
   pip3 install -r requirements.txt
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

## 🗂 项目结构

```
BookCraft/
├── SKILL.md              # Skill 定义：Agent 读取的工作流与规则
├── scripts/
│   └── translate.py      # 核心脚本：提取段落 / 组装文件（EPUB + Markdown）
├── requirements.txt      # Python 依赖
└── README.md
```

## 🛠 翻译工作流

```
输入文件 ──extract──▶ paragraphs.json + glossary_candidates.json
                              │
                   ┌──────────┴───────────┐
                   ▼                        ▼
          翻译术语表 → glossary.json   分批翻译段落（注入术语表）
                   └──────────┬───────────┘
                              ▼
                    translations.json
                              │
                          build ──▶ 双语对照输出文件
```

## ⚠️ 边界

- 只翻译**英文 → 中文**，不支持其他语言对
- 只处理 **EPUB 和 Markdown** 两种格式
- 不做 OCR，不处理扫描版 PDF（如需翻译 PDF，请先转换为 EPUB）
- 翻译质量取决于驱动模型的翻译能力，不提供人工审校

## 📄 许可证

[MIT](LICENSE)
