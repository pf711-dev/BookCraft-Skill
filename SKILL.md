---
name: bookcraft
description: |
  双语翻译工具（BookCraft）：将英文 EPUB、Markdown 或 PDF 翻译成中英对照或仅中文版本。
  触发条件：用户说"翻译"、"双语翻译"、"中英对照"、"translate"、
  "翻译 EPUB"、"翻译 Markdown"、"翻译 PDF"、"翻译电子书"等，或用户提供 EPUB/Markdown/PDF 文件要求翻译。
---

# Skill: BookCraft
# 双语翻译工具

将英文 EPUB、Markdown 或 PDF 翻译成中英对照或仅中文版本。

## 支持的文件类型

| 类型 | 扩展名 | 输出 |
|------|--------|------|
| EPUB | `.epub` | 中英对照 EPUB（双语 CSS 样式）/ 仅中文 EPUB |
| Markdown | `.md` `.markdown` | 中英对照 Markdown（中文用 `>` 引用块标注）/ 仅中文 Markdown |
| PDF | `.pdf` | 仅中文或中英对照 EPUB + Markdown 备份（保留原图与扫描原件） |

> PDF 翻译由结构化提取层 + 组装层实现：文本型 PDF 按章节提取段落，
> 图片按原位置锚定，整页扫描件译文在前、原图附后。扫描页占比超 60%
> （整页文本 <20 字符的页面）会提示先 OCR，不在本 skill 能力范围内。

## 翻译类型

| 类型 | 说明 |
|------|------|
| `bilingual` | 中英对照（默认，标题保留英文并在其后附中文） |
| `chinese_only` | 仅中文（正文与标题均只保留中文，EPUB 标题标签保留以沿用原书样式） |
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

**PDF 输入的章节检测参数**（提取前先翻目录抽样看几页结构，再选策略）：

| 参数 | 说明 |
|------|------|
| `--chapters auto` | 默认。有 PDF 书签用 `outline`，否则 `heading` 自动字号检测 |
| `--chapters outline` | 按 PDF 书签目录分章（最可靠，书签有时可选） |
| `--chapters heading` | 字号 >= 正文 + `--heading-delta`（默认 2pt）的页首短文本视为章节标题 |
| `--chapters pattern --chapter-pattern '...'` | 按正则匹配页首文本，适配无大字号标题的书（如信件集）。**正则要用行锚定**（如 `^((Interim|Annual) Letter)\.?$`），避免正文词汇误匹配；支持多行标题拼接匹配 |

提取后**必须检查 meta.json 的章节数与页码区间是否合理**（对比目录页），
不合理就换策略重跑 extract。警告"扫描页占比超 60%"时告知用户需先 OCR。

提取层同时处理：页码/页眉/页脚剔除、跨页段落合并、断词修复、
电话/数据表行标记为 verbatim（保留原文不翻译，防数字误译）、
整页扫描件导出（`scans/`，由步骤 2.5 识图翻译）。

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

#### 模式 A：对话内翻译（小文件，< 200 段）

1. 读取 `{workdir}/paragraphs.json`
2. 将段落分批，每批 **5-10 段**（根据段落长度调整，总字符数控制在 3000 以内）
3. **如果 `{workdir}/glossary.json` 存在，先读取术语表**，翻译每批段落时注入
4. 对每批段落执行翻译

#### 模式 B：并行子代理翻译（大文件，>= 200 段，PDF 几乎必用）

1. 将段落切分为每批约 3000 英文词（段落不跨批拆分），
   每批写入 `{workdir}/translations/input_batch_NN.json`：
   `{"pids": [...], "texts": [...]}`（pids 从 `{workdir}/pids.json` 按 texts 顺序截取）
2. 派发并行子代理（**并发不超过 4**，过高会触发 429 限流），
   每个代理：读输入批 + 术语表 → 翻译 → 用 write 工具写
   `{workdir}/translations/output_batch_NN.json`：`{"translations": [...]}`
3. 每批完成即落盘，中断后按"缺失的 output 文件"重派即可续跑，不从头再来
4. 全部完成后校验：每批输出段数 == 输入段数，不符则该批重试；
   空段、"与原文相同"的段落逐个检查（纯数字/百分比/表格碎片属合理保留，其余重译）
5. 按批次顺序拼接为 `{workdir}/translations.json`

**翻译 Prompt（严格遵循，有术语表时注入第 3 条）：**

```
请将以下英文段落翻译为中文。要求：
1. 准确翻译，保留原文含义和语气（若是个人化文本如信件/回忆录，不要译成干巴的报告体）
2. 翻译流畅，符合中文阅读习惯
3. 人名、地名、机构名等专有名词必须使用以下对照表中的译名，不得随意改译：
   - Elizabeth → 伊丽莎白
   - William → 威廉
4. 数字、金额、百分比原样保留（如 $2bn 译为 20 亿美元；日期改为中文习惯：
   18th January 2002 → 2002 年 1 月 18 日）
5. 个别无法翻译的词（如无可查证译名的冷门专有名词）保留英文原文
6. 仅输出翻译结果，不要添加编号、解释或其他内容
7. 每个段落的翻译用空行分隔，顺序与原文一一对应；禁止增删段

英文段落：
{段落1}

{段落2}

...
```

> PDF/财经类文本尤其注意第 4 条：数字和金额译错是最大风险源，
> 宁可原样保留也不要改写数字。

4. 将每批翻译结果追加收集

### 步骤 3：写入翻译结果

将所有翻译结果写入 `{workdir}/translations.json`：
```json
{
  "translations": ["翻译1", "翻译2", ...]
}
```

### 步骤 3.5：扫描页识图翻译（仅 PDF，meta.json 中 scan_pages > 0 时）

1. 逐张查看 `{workdir}/scans/scan_pNNN.png`（多模态读图）
2. 将内容翻译为中文，连同 `{workdir}/scan_translations.json`：
```json
{
  "scan_p003.png": {
    "title": "页面标题",
    "note": "此页为原件扫描图，以下为译文",
    "blocks": [
      {"h": "小标题"},
      {"p": "正文段落"},
      {"i": "注释/信头/页脚（斜体样式）"},
      {"i": "［方框内容以 ［ 开头自动渲染为浅灰底框］"}
    ]
  }
}
```
3. 图中模糊无法辨认的词按字形转录并保留英文，有原图兜底

### 步骤 4：组装最终文件

```bash
python3 {{SKILL_DIR}}/scripts/translate.py build \
  --input "{input_path}" \
  --workdir "{workdir}"
```

PDF 输入输出为 EPUB（默认 `{原文件名}_chinese_only.epub`）+ 同名 Markdown 备份；
未翻译的段落自动保留英文原文，不丢内容。

### 步骤 4.5：完整性校验（仅 PDF）

1. build 输出的 `paragraphs_translated / paragraphs_total`：未译段落需逐个确认原因
2. 解包 EPUB 抽查：章节数 == meta.json 章节数；图片数 == content_images + scan_pages；
   抽读 2-3 章开头确认译文渲染与中英占比合理

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

**PDF 翻译额外依赖**（仅处理 .pdf 输入时需要）：
```bash
python3 -c "import fitz; import ebooklib; print('PDF OK')"
```
缺失时：`pip3 install pymupdf ebooklib`

---

## 对话规则

1. **收到翻译请求后直接执行**，不要反复确认。如果文件路径不明确，问一句即可。
2. **翻译过程中展示进度**。每完成一批报告进度。
3. **大文件提前预估**。文件超过 200 段时改用模式 B 并行子代理翻译（并发 ≤ 4）；
   提前告知耗时（每 1000 段约 10-20 分钟）与 token 消耗量级。
4. **输出文件路径告知用户**，方便查找。
5. **翻译类型尊重用户选择**。用户没说则默认双语。
6. **不要修改原文件**，翻译结果始终写入新文件。
7. **PDF 章节切分必须人工核对**。提取后对比 meta.json 章节列表与原书目录，
   切分错误时换策略重跑，宁可多核对一次也不要带错翻译。

## 边界

- 只翻译英文到中文，不支持其他语言对
- 处理 EPUB、Markdown、文本型 PDF 三种格式
- PDF：支持文本型 PDF（含少量整页扫描件，走识图翻译）；**不支持扫描版书籍**
  （扫描页占比 >60% 的 PDF 会提示先 OCR，整本识图翻译不现实；
  单页判定：整页文本 <20 字符，`--scan-min-chars` 可调）
- 不做 OCR（整页扫描件的识图翻译除外，那是多模态能力而非 OCR 流水线）
- 不做网页抓取翻译
- 翻译质量取决于模型能力，不提供人工审校
