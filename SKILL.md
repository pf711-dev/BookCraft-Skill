---
name: bookcraft
version: 1.4.0
display_name: "BookCraft 电子书双语翻译"
display_name_en: "BookCraft"
description_zh: "BookCraft 是一个英文电子书翻译工具。\n- 支持 EPUB、Markdown、PDF（含扫描版）一键翻译成中英对照，或仅中文格式。\n- 翻译前预检：时间/token 消耗/输出文件预估，一次确认后自动跑完。\n- 表格逐格翻译、公式保护、术语表统一译名；扫描页识图翻译。"
description_en: "Bilingual eBook translator — EPUB / Markdown / PDF (incl. scanned) to English-Chinese dual or Chinese-only. Pre-flight check with time/token/output estimates before translating. Cell-level table translation, formula protection, unified glossary, vision-based scan-page translation."
description: |
  BookCraft 是一个将「英文 EPUB/Markdown/PDF」电子书一键翻译成「中英或仅中文 EPUB/Markdown」的 Skill。
  触发条件：用户说"翻译"、"双语翻译"、"中英对照"、"translate"、
  "翻译 EPUB"、"翻译 Markdown"、"翻译 PDF"、"翻译电子书"等，或用户提供 EPUB/Markdown/PDF 文件要求翻译。
---

# Skill: BookCraft
# BookCraft 电子书双语翻译

BookCraft 是一个将英文 EPUB、Markdown、PDF 电子书一键翻译成中英或仅中文 EPUB、Markdown 格式的 Skill。

## 能力速览

- EPUB / Markdown / PDF 三种输入（PDF 含文本型与图片型/扫描版）
- 中英对照 或 仅中文 两种输出
- **翻译前预检**：时间 / token 消耗 / 输出文件预估，一次确认后自动跑完
- 表格逐格翻译（Markdown / EPUB；PDF 数据行保留原文、表头翻译）
- 数学公式一律原样保留，译文中的公式与原文完全一致
- 扫描页识图翻译（需模型视觉能力，预检时自动判断）
- 术语表统一全书人名译名（支持手动术语表）
- 数据表行、电话、地址自动保留原文，防数字误译

## 支持的文件类型

| 类型 | 扩展名 | 输出 |
|------|--------|------|
| EPUB | `.epub` | 中英对照 EPUB（双语 CSS 样式）/ 仅中文 EPUB |
| Markdown | `.md` `.markdown` | 中英对照 Markdown（中文用 `>` 引用块标注，表格附中文表副本）/ 仅中文 Markdown；**支持表格逐格翻译与公式保护** |
| PDF | `.pdf` | 仅中文或中英对照 EPUB + Markdown 备份（保留原图与扫描原件）；公式行/数据表行自动 verbatim 保留原文 |

> PDF 翻译由结构化提取层 + 组装层实现：文本型 PDF 按章节提取段落，
> 图片按原位置锚定，整页扫描件译文在前、原图附后。图片型 PDF（扫描页占比高）
> 自动回退整本单章提取，扫描页全部导出 `scans/` 由识图翻译，
> 是否可译取决于当前模型是否具备视觉能力——预检（步骤 1.2）时自动判断。
> 单页判定：整页文本 <20 字符，`--scan-min-chars` 可调。

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
不合理就换策略重跑 extract。若提取层提示"回退为整本单章"且扫描页占比高，
说明是图片型 PDF，是否能继续由步骤 1.2 预检的视觉探测决定。

提取层同时处理：页码/页眉/页脚剔除、跨页段落合并、断词修复、
电话/数据表行/公式行标记为 verbatim（保留原文不翻译，防数字误译与公式改写）、
整页扫描件导出（`scans/`，由步骤 2.5 识图翻译）。

**Markdown 公式保护**：`$...$`、`$$...$$`、`\(...\)`、`\[...\]` 公式在提取前
被替换为 `⟦Mn⟧` 占位符（公式不该翻译，模型改写极易破坏表达式），映射存
`formulas.json`，build 时自动还原。翻译时**必须原样保留占位符**（见 prompt 规则），
译文丢失占位符时该段自动回退英文，公式绝不因翻译而丢失。

**Markdown 表格翻译**：整表作为一个翻译单元（表头+各行，保证列语义，
如 "Q1" 知道是季度列），段落文本为 `[TABLE]` 开头的结构化格式
（`Header: A | B` / `Row: a | b` 行）。翻译时保持格式逐格翻译（见 prompt 规则），
写回时重建 `|` 结构；译文列数不匹配或格式完全无法解析时整表回退英文。

**EPUB 导航条目**：阅读器目录面板显示的标题来自 nav.xhtml / toc.ncx，
其中带链接条目（如 `<li><a>Part One</a></li>`）游离于正文容器结构之外，
由专门的导航机制提取并追加到段落末尾（meta.json 的 `nav_entries` / `nav_start`），
build 时自动写回并保留 href。分批翻译时必须覆盖 `paragraphs.json` 全量段落
（含末尾导航条目），否则目录仍会残留英文。纯页码/罗马数字条目自动跳过不译。

### 步骤 1.2：预检与确认（必须，三种格式统一）

extract 完成后、开始翻译前，向用户展示预检报告并等待确认——
这是**全流程唯一一次确认点**，确认后一口气跑完不再中断。

**1. 视觉探测**（仅 PDF 且 meta.json `scan_pages > 0` 时）：
用 read 工具读一张 `{workdir}/scans/` 下的截图并描述其内容——
能读到并正确描述 → 当前模型有视觉能力；工具报错或看不到图 → 无视觉。

**2. 预检报告**（列表形式，数据取自 extract stdout / meta.json）：

```
📋 翻译预检 — 请确认后开始

《{书名}》（{页数/章节数} · 约 {total_words} 万词 · {content_images} 图 · {scan_pages} 扫描页）
- 翻译类型：{中英对照（默认）/ 仅中文}
- 输出文件：{输出路径，含命名后缀 _bilingual/_chinese_only}
- 工作目录：{workdir}
- 当前模型：{模型名}（{支持 ✓ / 不支持 ✗}视觉）
- 预计耗时：约 N 分钟（{est_batches} 批文本 ÷ 并发4 [+ {scan_pages} 页识图]）
- 预计消耗：输入 ~N tok · 输出 ~N tok{（约 ¥N，参考价）}
- 章节预览：共 N 章，前 3 章为：…（发现切分错误在此时换策略重跑）

确认无误回复「开始」；要调整直接说明（如：改成仅中文 / 章节不对）。
```

**3. 估算口径**（统一使用，token 为硬数据、价格为参考值）：

| 项 | 口径 |
|----|------|
| 输入 token | 英文词数 × 1.3 |
| 输出 token | 仅中文 ≈ 词数 × 2；中英对照 ≈ 词数 × 3.3 |
| 识图调用 | 每页 ≈ 输入 2k tok + 译文输出（几百 tok） |
| 文本耗时 | 批次数 ÷ 4 并发 ×（flash 级 ~1.5 分/批；旗舰级 ~4 分/批） |
| 识图耗时 | 页数 × ~0.5 分（可 4 并发） |
| 价格 | 按当前模型公开定价折算，标注"参考价"，不硬编码 |

**4. 异常处理**：
- 无视觉 + 扫描页 > 0 → 报告中追加一行：
  `⚠️ 当前模型无法识图，N 页扫描件：a) 换多模态模型重跑 b) 跳过扫描页只译文本（保留原图）`
  等用户选择后再继续
- 工作量较大（est_batches > 30 或扫描页 > 100）→ 提示预计耗时较长，确认后继续
- 全书无文本（total_words ≈ 0 且 scan_pages > 0）→ 纯识图翻译，
  预检报告只需识图相关行

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
6. ⟦Mn⟧ 是数学公式占位符，**必须原样保留**，禁止翻译、改写、合并或省略
7. 以 [TABLE] 开头的段落是表格：保持 "Header: ..." / "Row: ..." 行结构，
   单元格用 " | " 分隔、数量与原文一致，逐格翻译；单元格内 \| 是转义竖线，原样保留
8. 仅输出翻译结果，不要添加编号、解释或其他内容
9. 每个段落的翻译用空行分隔，顺序与原文一一对应；禁止增删段

英文段落：
{段落1}

{段落2}

...
```

> PDF/财经类文本尤其注意第 4 条：数字和金额译错是最大风险源，
> 宁可原样保留也不要改写数字。
> 含 ⟦Mn⟧ 或 [TABLE] 的批次特别注意第 6/7 条：占位符丢失或表格格式
> 破坏会触发整段/整表回退英文，等于白翻。

4. 将每批翻译结果追加收集

### 步骤 3：写入翻译结果

将所有翻译结果写入 `{workdir}/translations.json`：
```json
{
  "translations": ["翻译1", "翻译2", ...]
}
```

### 步骤 3.5：扫描页识图翻译（仅 PDF，meta.json 中 scan_pages > 0 时）

1. 逐张查看 `{workdir}/scans/scan_pNNN.png`（多模态读图，可 4 并发）
2. 将内容翻译为中文，连同 `{workdir}/scan_translations.json`：
```json
{
  "scan_p003.png": {
    "title": "页面标题",
    "note": "此页为原件扫描图，以下为译文",
    "chapter_start": true,
    "chapter_title": "第一章 标题",
    "blocks": [
      {"h": "小标题"},
      {"p": "正文段落"},
      {"i": "注释/信头/页脚（斜体样式）"},
      {"i": "［方框内容以 ［ 开头自动渲染为浅灰底框］"}
    ]
  }
}
```
3. **章节重建**（图片型 PDF 几乎必用）：识别到某页是新章节起始页时，
   该页条目加 `"chapter_start": true` + `"chapter_title": "中文章节标题"`，
   build 时自动在此切出新章；封面/版权页/前言等非章节页不标
4. 图中模糊无法辨认的词按字形转录并保留英文，有原图兜底

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

1. **收到翻译请求后先提取，然后按步骤 1.2 预检确认**——这是唯一一次确认点，
   确认后不再中断（翻译中的质量校验回退除外）。文件路径不明确，问一句即可。
2. **翻译过程中展示进度**。每完成一批报告进度。
3. **预估信息统一在预检报告给出**（时间/token/输出），不要等出问题再停。
4. **输出文件路径告知用户**，方便查找。
5. **翻译类型尊重用户选择**。用户没说则默认中英对照，预检报告里再次列出供确认。
6. **不要修改原文件**，翻译结果始终写入新文件。
7. **PDF 章节切分在预检时人工核对**（章节预览行）。切分错误时换策略重跑，
   宁可多核对一次也不要带错翻译。

## 边界

- 只翻译英文到中文，不支持其他语言对
- 处理 EPUB、Markdown、PDF 三种格式
- PDF：文本型直接提取翻译；图片型（扫描版）走整本识图翻译，
  **需当前模型具备视觉能力**（预检时自动探测）；无视觉时可跳过扫描页只译文本
  （扫描块保留原图，不丢内容）或建议用户换多模态模型
- 扫描页识图翻译是模型多模态能力，不是 OCR 流水线；无视觉模型自动降级并告知
- 不做网页抓取翻译
- 翻译质量取决于模型能力，不提供人工审校

### 表格与公式边界

- **Markdown 表格**：需 mistune（默认依赖）；单元格内的行内格式
  （`**粗体**`、`` `代码` ``、链接）在翻译后不保留（译文整体替换单元格文本）；
  嵌套在引用块内的表格不翻译（保持原样）
- **Markdown 公式**：识别 `$...$` / `$$...$$` / `\(...\)` / `\[...\]` 四种形态；
  代码块与行内代码中的 `$` 不误伤（如 shell 变量、价格示例）；
  货币写法（如 "costs $100 and $200"）通过 pandoc 式启发式排除
- **PDF 公式**：数学符号密度检测（∑∫≤≥±√、Greek 字符等）→ verbatim 保留原文，
  **不做公式识别重建**（需专用模型，超出架构定位）；私有字体映射导致的
  乱码公式（密集 `·`）同样 verbatim 兜底；纯字母代数式（如 `P(A|B) = ...`）
  无特殊符号，不做检测，翻译时靠数字保留规则兜底
- **PDF 表格**：只保证表头行进翻译流（数据行 verbatim 时不吞并表头），
  不做完整表格重建（无边框/跨页表格检测不可靠，等真实需求）
- **EPUB**：表格全链路已通（DOM 锚点），公式经图片/媒体节点原样保留，均无需处理
