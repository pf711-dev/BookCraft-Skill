# BookCraft v1.3.0 人工完整测试用例

> 目标：对表格与公式支持做一次**全链路人工验收**。
> 标注 🔒 的用例自动化已覆盖（`python3 tests/test_markdown.py` / `test_pdf.py`），
> 人工测试重点看**真实使用路径下的端到端效果**与自动化覆盖不到的翻译层行为。

---

## 0. 测试前准备

### 0.1 确认测试的是生产目录版本（重要）

对 AI 助手说「翻译」时触发的是 `~/.agents/skills/BookCraft/`（生产目录）。
确认生产目录已是 v1.3.0：

```bash
grep "^version:" ~/.agents/skills/BookCraft/SKILL.md   # 应输出 version: 1.3.0
grep -c "protect_formulas" ~/.agents/skills/BookCraft/scripts/translate.py  # 应 > 0
```

- [ ] 生产目录版本 = 1.3.0

### 0.2 依赖检查

```bash
python3 -c "from bs4 import BeautifulSoup; import lxml, mistune, fitz, ebooklib; print('依赖 OK')"
```

- [ ] 依赖 OK（mistune 缺失会导致 MD 表格/公式全部失效，务必确认）

### 0.3 生成测试样例

```bash
cd ~/project_bookcraft_skill
python3 tests/make_samples.py
```

样例清单：

| 文件 | 内容 |
|------|------|
| `tests/samples/sample_table_formula.md` | 4 种公式定界符 + 转义美元 + 货币 + 表格（含 `\|` 转义单元格）+ 代码块 + 引用块 + 列表 |
| `tests/samples/sample_formula_book.pdf` | 两章 PDF：正文 + 公式行 + 表头行 + 数据行 |
| `tests/samples/sample_book.epub` | 含表格 + 标题 + 导航的最小 EPUB（回归用） |

- [ ] 三个样例文件已生成

---

## A. Markdown 表格翻译（核心路径）

### A1 表格双语翻译（主路径）

**操作**：对 AI 助手说：

> 翻译 ~/project_bookcraft_skill/tests/samples/sample_table_formula.md

**预期**：
1. 提取阶段输出含 `"table_units": 1`、`"formulas_protected": 5`
2. 输出文件 `sample_table_formula_bilingual.md` 中：
   - 原英文表格保留，紧随其后出现中文表副本（`| 指标 | 第一季度 | 第二季度 | 备注 |`）
   - 中文表含 3 行数据（营收 / 用户 / 流失 \| 转义），数值列原样（`$1.2M`、`10,000`）
   - 中文表对齐行为 `| --- | --- | --- | --- |` 格式
3. 表格外的段落正常双语（英文原文 + `> 🇨🇳 中文`）

- [ ] 中文表副本出现且 3 行数据齐全
- [ ] 数值单元格未被翻译改写（$1.2M、10,000、+25% 原样）
- [ ] 转义竖线单元格「流失 \| 转义」正常渲染为一格（不是错位成两格）

### A2 表格纯中文模式

**操作**：对 AI 助手说：

> 翻译 ~/project_bookcraft_skill/tests/samples/sample_table_formula.md，只要中文

**预期**：
1. 输出 `sample_table_formula_chinese_only.md`
2. 原英文表格**消失**，原位出现中文表格（表头和数据行全中文，数值保留）
3. 标题变纯中文（`# 年度数据报告`），保留 `#` 前缀

- [ ] 原表被中文表替换（不是追加）
- [ ] 数值原样、文字全中文

### A3 纯数字表格跳过（不进翻译流）

**操作**：新建 `/tmp/test_pure_num.md`：

```markdown
# Numeric Table

| 1 | 2 | 3 |
|---|---|---|
| 4 | 5 | 6 |

Some text after the table.
```

对 AI 助手说：`翻译 /tmp/test_pure_num.md`

**预期**：提取输出 `table_units: 0`（纯数字表无文字，跳过不翻译），表格在输出中原样保留。

- [ ] `table_units: 0`，纯数字表原样保留

---

## B. Markdown 公式保护（核心路径）

### B1 四种公式形态（双语）

用 A1 的输出文件检查：

**预期**（`sample_table_formula_bilingual.md`）：
1. `$R = P \times (1 + r)^n$`、`\sigma = \sqrt{...}`（三行块级）、`\(a^2 + b^2 = c^2\)`、`$\alpha + \beta$`、`$\gamma = \delta / 2$` 全部以**原始 LaTeX 形态**出现（未被翻译改写）
2. 中文译文行里公式同样原样嵌入
3. 全文无 `⟦` 残留（占位符全部还原）
4. 引用块内的公式行每行带 `> ` 前缀（`> $$` → `> \sigma ...` → `> $$`）

- [ ] 4 种定界符公式全部原样保留在英文侧
- [ ] 中文侧公式原样嵌入
- [ ] 无 ⟦ 残留
- [ ] 引用块内块级公式每行带 `> ` 前缀

### B2 货币不误伤

用 A1 的输出文件检查：

**预期**：
1. `The membership fee is \$50 ...` 段落**有中文译文**（说明该段匹配成功；\$ 未被当公式）
2. `Travel costs were $100 and $200 ...` 段落有译文，`$100 and $200` 中的数字未被当作公式对改写

- [ ] 两段均有译文（回退英文则说明误伤）

### B3 代码块保护

用 A1 的输出文件检查：

**预期**：bash 代码块原样保留（`echo "costs $100 and $HOME"` 未被改动、无译文附行）。

- [ ] 代码块原样，内部 $ 未被处理

### B4 占位符丢失回退（🔒 自动化已覆盖，人工抽查一遍）

**操作**（手动控制流程，精确验证）：

```bash
cd /tmp && rm -rf bc_manual && python3 ~/.agents/skills/BookCraft/scripts/translate.py \
  extract --input ~/project_bookcraft_skill/tests/samples/sample_table_formula.md \
  --workdir /tmp/bc_manual --type bilingual
# 查看含占位符的段落
grep -l "⟦" /tmp/bc_manual/paragraphs.json && echo "占位符已入流"
```

然后**故意制造坏译文**：编辑 `/tmp/bc_manual/translations.json`，找到含 `⟦M3⟧` 的段落译文，把 `⟦M3⟧` 删掉（模拟模型吞占位符），再 build：

```bash
python3 ~/.agents/skills/BookCraft/scripts/translate.py \
  build --input ~/project_bookcraft_skill/tests/samples/sample_table_formula.md --workdir /tmp/bc_manual
```

**预期**：被删占位符的段落**回退英文原文**（无 `> 🇨🇳` 译文行），公式 `$R = P \times (1 + r)^n$` 仍在原文中；其他段落不受影响。

- [ ] 坏译文段落回退英文，公式保留
- [ ] 其余段落正常翻译

---

## C. PDF 公式与数据表（核心路径）

### C1 提取：公式 verbatim + 表头进翻译流

**操作**：

```bash
python3 ~/.agents/skills/BookCraft/scripts/translate.py \
  extract --input ~/project_bookcraft_skill/tests/samples/sample_formula_book.pdf \
  --workdir /tmp/bc_pdf_manual --type chinese_only \
  --chapters pattern --chapter-pattern '^Chapter \d+'
```

**检查**（`/tmp/bc_pdf_manual/`）：

```bash
python3 -c "
import json
ps = json.load(open('/tmp/bc_pdf_manual/paragraphs.json'))['paragraphs']
for i, p in enumerate(ps): print(i, repr(p)[:70])"
```

**预期**：
1. 翻译流（paragraphs.json）**只含正文句和表头行**（`Name Revenue Growth`、`Model MAE RMSE`）
2. 公式行（σ... 或乱码 `·` 形态）、数据行（`Acme Corp $1,200 +15%` 等）**不在**翻译流
3. extract 输出 `"verbatim_paragraphs": 7`（第一章 1 公式 + 3 数据行，第二章 1 公式 + 2 数据行）
4. ⚠️ 公式行显示为 `·` 乱码属**正常现象**：PyMuPDF 内置字体无 σ/Σ 字形，提取即乱码，verbatim 兜底保留——真实 PDF（嵌入字体）会正常提取符号

- [ ] 表头行在翻译流中
- [ ] 公式行/数据行不在翻译流
- [ ] verbatim_paragraphs ≥ 4

### C2 组装：表头译文 + 数据行聚合

**操作**：翻译（对 AI 说「继续翻译 /tmp/bc_pdf_manual」走 skill 流程，或手动写 translations.json 后）：

```bash
python3 ~/.agents/skills/BookCraft/scripts/translate.py \
  build --input ~/project_bookcraft_skill/tests/samples/sample_formula_book.pdf \
  --workdir /tmp/bc_pdf_manual
```

**预期**（检查输出 `~/project_bookcraft_skill/tests/samples/sample_formula_book_chinese_only.epub` + 同名 `.md`——
extract 未指定 --output 时产物跟输入文件同目录）：
1. Markdown 备份中：表头行变中文（如 `名称 营收 增长`），**位于** verbatim 数据块上方
2. 连续数据行聚合为**一个**代码块（`Acme Corp...` / `Beta LLC...` / `Gamma Inc...` 在同一个 ``` 块内，不是多个）
3. 公式行单独一个 verbatim 块（保留原文）
4. 正文全中文

- [ ] 表头已翻译且在数据块上方
- [ ] 连续数据行聚合成单块
- [ ] build 输出 `paragraphs_translated == paragraphs_total`（映射率 100%）

### C3 真实 PDF 章节策略（如有现成书籍）

用一本真实文本型 PDF（如之前 Nomad 类书籍）跑：

> 翻译 xxx.pdf

**预期**：流程与 v1.2.0 一致（章节策略、扫描页检测不受本次改动影响）。

- [ ] 真实 PDF 流程正常（选测）

---

## D. EPUB 回归（确认本次改动未破坏既有能力）

### D1 EPUB 基础翻译 + 表格

**操作**：

> 翻译 ~/project_bookcraft_skill/tests/samples/sample_book.epub

**预期**：
1. 输出 `sample_book_bilingual.epub`，能正常打开（Apple Books / klint 等）
2. 表格逐格翻译（`Region` → `区域` 等，td/th 在 EPUB 链路一直支持）
3. 标题双语、导航目录「Chapter 1」已翻译
4. 正文段落双语渲染（CSS 样式正常）

- [ ] EPUB 可打开、表格逐格翻译、导航已译、样式正常

### D2 EPUB 纯中文模式（可选）

> 翻译 ~/project_bookcraft_skill/tests/samples/sample_book.epub，只要中文

- [ ] 正文与标题仅中文，样式保留

---

## E. 边界与容错

### E1 旧 workdir 兼容（无 formulas.json）

v1.2.0 的旧工作目录没有 `formulas.json`，build 不应报错：

```bash
mkdir -p /tmp/bc_legacy && cd /tmp/bc_legacy
printf '# Old doc\n\nJust a sentence here.\n' > original.md
echo '{"paragraphs": ["Old doc", "Just a sentence here."]}' > paragraphs.json
cat > paragraph_details.json << 'EOF'
[
 {"text": "Old doc", "is_heading": true, "tag": "h1", "type": "text"},
 {"text": "Just a sentence here.", "is_heading": false, "tag": "p", "type": "text"}
]
EOF
echo '{"type": "markdown", "input": "book.md", "output": "", "translate_type": "bilingual"}' > meta.json
echo '{"translations": ["旧文档", "只是一句话。"]}' > translations.json
python3 ~/.agents/skills/BookCraft/scripts/translate.py build --input book.md --workdir .
```

**预期**：正常输出（无 formulas.json 视为无公式，不报错）。

- [ ] 旧结构 build 不报错

### E2 翻译 Prompt 遵循度（翻译层，人工观察）

A1 流程中翻看 AI 的翻译批处理输出（translations.json）：

**预期**：
1. 含 `⟦Mn⟧` 的段落译文占位符齐全（漏了会被 build 兜底回退，但穿越率高说明 prompt 起作用）
2. `[TABLE]` 段落译文保持 `Header:`/`Row:` 格式、单元格数量一致
3. 若出现回退（输出里冒出英文段），数一下占比——超过 20% 说明模型遵循度差，考虑换模型重跑

- [ ] 占位符穿越率高（回退段落 ≤ 1）
- [ ] 表格译文格式正确

---

## F. 真实书籍验证（重点，v1.3.0 最终验收）

自动化样例都是小片段，**真实书籍是唯一能暴露翻译层问题的手段**。

### F1 MD 技术书（LaTeX 公式密集）

找一本真实的技术类/学术类 Markdown 书（公式多、表格多），对 AI 助手说：

> 翻译 <书.md 路径>

**观察点**：
1. 提取输出 `formulas_protected` 数量是否与书中公式量级相符
2. 抽查 5-10 处公式：原文与译文中的 LaTeX 是否完全一致
3. 抽查 2-3 张表格：列对齐、语义正确（"Q1" 类列头语境正确）
4. 全文搜 `⟦`：不应有残留
5. 出现英文回退的段落：确认都是占位符丢失导致（可接受，但记录数量）

- [ ] 公式穿越率 ≈ 100%（回退段落数 / 含公式段落数 ≤ 5%）
- [ ] 表格语义正确
- [ ] 无 ⟦ 残留

### F2 PDF 学术书

找一本文本型学术 PDF（公式 + 数据表），对 AI 助手说：

> 翻译 <书.pdf 路径>

**观察点**：
1. meta.json 的 `verbatim_paragraphs` 数量合理（公式/数据行被保护，正文不被误伤——抽查 3 处 verbatim 确认是公式或数据行而非正文）
2. 表头行被翻译（找一张数据表：数据保留原文、上方表头有中文）
3. 译文映射率 ≥ 94%（历史基准）

- [ ] verbatim 误伤抽查通过（正文没被误标）
- [ ] 表头翻译正常
- [ ] 映射率 ≥ 94%

---

## G. 安装脚本（可选，需 PEP 668 环境）

### G1 依赖已装跳过

```bash
cd ~/project_bookcraft_skill && ./install.sh
```

**预期**：输出「依赖已全部安装，跳过」，安装正常完成不报错。

- [ ] 跳过逻辑正常

### G2 PEP 668 环境不 abort（需 Homebrew Python 环境）

```bash
/opt/homebrew/bin/python3 -m pip install -r <(echo "some-new-pkg>=1.0")  # 验证会报 externally-managed
```

**预期**：install.sh 在受管环境下 pip 失败时输出提示但脚本不中断（exit 0）。

- [ ] 不 abort（无该环境可跳过）

---

## 测试结果汇总

| 分组 | 用例 | 结果 |
|------|------|------|
| 准备 | 0.1-0.3 | ☐ |
| A 表格 | A1 A2 A3 | ☐ ☐ ☐ |
| B 公式 | B1 B2 B3 B4 | ☐ ☐ ☐ ☐ |
| C PDF | C1 C2 C3 | ☐ ☐ ☐ |
| D EPUB | D1 D2 | ☐ ☐ |
| E 边界 | E1 E2 | ☐ ☐ |
| F 真实书 | F1 F2 | ☐ ☐ |
| G 安装 | G1 G2 | ☐ ☐ |

发现问题记录（用例编号 / 现象 / workdir 路径保留现场）：

```
（在此记录）
```
