---
name: BookCraft
description: |
  双语翻译工具（BookCraft）：将英文 EPUB 电子书或 Markdown 文件翻译成中英对照版本。
  使用当前对话模型逐段翻译，无需配置任何 API Key，开箱即用。
  触发条件：用户说"翻译"、"双语翻译"、"中英对照"、"translate"、"bilingual"、
  "翻译 EPUB"、"翻译 Markdown"、"翻译电子书"等，或用户提供 EPUB/Markdown 文件要求翻译。
---

# Skill: BookCraft
# 双语翻译工具

将英文 EPUB 电子书或 Markdown 文件翻译成中英对照版本。

## 支持的文件类型

| 类型 | 扩展名 | 输出 |
|------|--------|------|
| EPUB | `.epub` | 中英对照 EPUB（双语 CSS 样式） |
| Markdown | `.md` `.markdown` | 中英对照 Markdown（中文用 `>` 引用块标注） |

## 翻译类型

| 类型 | 说明 |
|------|------|
| `bilingual` | 中英对照（默认） |
| `chinese_only` | 仅中文（标题保留英文原文） |

---

## 翻译工作流

使用当前对话模型逐段翻译，无需配置任何 API Key。

### 步骤 1：提取段落

```bash
python3 {{SKILL_DIR}}/scripts/translate.py extract \
  --input "{input_path}" \
  --workdir "{workdir}" \
  --type {bilingual|chinese_only} \
  --output "{output_path}"
```

`workdir` 使用 `/tmp/bilingual_{timestamp}` 作为工作目录。

### 步骤 1.5：翻译术语表（保证人名/专有名词全书译名统一）

1. 读取 `{workdir}/glossary_candidates.json`，得到候选专有名词列表
2. 若存在用户手动术语表 `~/.bookcraft/glossary.json`，将其译名**合并进来并优先使用**
3. 将候选词一次性翻译为中文，写入 `{workdir}/glossary.json`：
   ```json
   {
     "glossary": {
       "Elizabeth": "伊丽莎白",
       "William": "威廉"
     }
   }
   ```

**术语表翻译 Prompt：**
```
请将以下英文专有名词（人名/地名/机构名/作品名等）翻译为中文，生成本书术语表：
{候选词列表，每行一个}

要求：
1. 人名遵循通行中文译法（如 Elizabeth → 伊丽莎白）
2. 只输出一个 JSON 对象，键为英文原文，值为中文译名
3. 非专有名词或无需翻译的词，值输出空字符串
```

### 步骤 2：读取段落并分批翻译

1. 读取 `{workdir}/paragraphs.json`
2. 将段落分批，每批 **5-10 段**（根据段落长度调整，总字符数控制在 3000 以内）
3. **如果 `{workdir}/glossary.json` 存在，先读取术语表**，翻译每批段落时注入
4. 对每批段落执行翻译

**翻译 Prompt（严格遵循，有术语表时注入第 3 条）：**

```
请将以下英文段落翻译为中文。要求：
1. 准确翻译，保留原文含义和语气
2. 翻译流畅，符合中文阅读习惯
3. 人名、地名、机构名等专有名词必须使用以下对照表中的译名，不得随意改译：
   - Elizabeth → 伊丽莎白
   - William → 威廉
4. 仅输出翻译结果，不要添加编号、解释或其他内容
5. 每个段落的翻译用空行分隔，顺序与原文一一对应

英文段落：
{段落1}

{段落2}

...
```

4. 将每批翻译结果追加收集

### 步骤 3：写入翻译结果

将所有翻译结果写入 `{workdir}/translations.json`：
```json
{
  "translations": ["翻译1", "翻译2", ...]
}
```

### 步骤 4：组装最终文件

```bash
python3 {{SKILL_DIR}}/scripts/translate.py build \
  --input "{input_path}" \
  --workdir "{workdir}"
```

### 步骤 5：清理

删除临时工作目录 `{workdir}`。

### 步骤 6：向用户报告结果

告知翻译完成、输出文件路径、总段落数。

---

## 手动术语表（可选）

当用户要求"补充术语"、"修正译名"、"加个术语表"时：

1. 创建或编辑 `~/.bookcraft/glossary.json`：
   ```json
   {
     "Elizabeth": "伊丽莎白",
     "William": "威廉"
   }
   ```
2. 翻译时会自动读取该文件，且**优先于自动生成的术语表**使用
3. 用于固定人名/地名/机构名等专有名词的译名，保证全书一致

---

## 使用示例

| 用户说 | 行为 |
|--------|------|
| "翻译 book.epub" | 执行翻译工作流 |
| "把这个 Markdown 翻译成中文" | 翻译 Markdown |
| "翻译 book.epub，只要中文" | 使用 chinese_only 类型 |

---

## 依赖检查

首次使用时，检查 Python 依赖是否安装：

```bash
python3 -c "from bs4 import BeautifulSoup; import lxml; print('OK')"
```

如果缺少依赖，提示用户安装：
```bash
pip3 install beautifulsoup4 lxml
```

Markdown 翻译可选安装 `mistune`（未安装时使用内置简易解析器）：
```bash
pip3 install mistune
```

---

## 对话规则

1. **收到翻译请求后直接执行**，不要反复确认。如果文件路径不明确，问一句即可。
2. **翻译过程中展示进度**。每完成一批报告进度。
3. **大文件提前预估**。如果文件超过 200 段，提前告知预计耗时约 10-30 分钟。
4. **输出文件路径告知用户**，方便查找。
5. **翻译类型尊重用户选择**。用户没说则默认 bilingual。
6. **不要修改原文件**，翻译结果始终写入新文件。

## 边界

- 只翻译英文到中文，不支持其他语言对
- 只处理 EPUB 和 Markdown 两种文件格式
- 不做 OCR，不处理扫描版 PDF（如需翻译 PDF，先让用户转换为 EPUB）
- 不做网页抓取翻译
- 翻译质量取决于模型能力，不提供人工审校
