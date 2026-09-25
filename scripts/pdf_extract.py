#!/usr/bin/env python3
"""PDF 结构化提取模块（BookCraft PDF 支持 · 提取层）

将文本型 PDF 提取为章节块序列（段落 + 图片锚点 + 扫描页标记），
输出与 BookCraft 现有翻译流程兼容的 paragraphs.json。

章节检测策略（--chapters）：
  auto     默认。有 PDF 书签用 outline，否则用 heading 自动字号检测
  outline  按 PDF 书签目录（get_toc）分章，最可靠
  heading  自动检测：字号明显大于正文（>= 正文字号 + heading_delta）的短文本视为章节标题
  pattern  按用户正则匹配每页开头若干行（配合 --chapter-pattern）

提取规则（来自 Nomad 信件全集翻译实践）：
  - 文本块按 y 坐标排序保阅读顺序；跨页段落自动合并（上段无句末标点 + 下段小写开头）
  - 纯数字行 = 页码，剔除；高频页首/页尾短文本 = 页眉/页脚，剔除
  - 电话/传真行、数字占比 > 45% 的数据表行 → verbatim（保留原文，不进翻译批次）
  - 整页文本 < scan-min-chars → 判定扫描页，200dpi 截图存 scans/（由 Agent 识图翻译，
    译文写 scan_translations.json，组装时"译文在前 + 原图附后"）
  - 图片块按页面位置锚定进段落流；任一边 < 60px 的小图视为装饰，组装时不插入正文
  - 断词修复 "exam- ple" → "example"
  - 年份正则注意用 20\\d\\d 而非 200\\d（2000-2009 之外的年份会漏检）

依赖：PyMuPDF（fitz）
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PAGE_NUM_RE = re.compile(r'^\s*\d{1,4}\s*$')
PHONE_RE = re.compile(r'[TtF]:\s*\+\d+|tel[:.].{0,20}\d{6,}|fax[:.].{0,20}\d{6,}', re.I)
HEADING_TEXT_RE = re.compile(r'^\s*[\dIVXLC]+[\.\)]?\s+\S+|^第.章|^Chapter\b|^[A-Z\u4e00-\u9fff]', re.I)


def block_text(block):
    parts = []
    for line in block.get("lines", []):
        parts.append("".join(s["text"] for s in line["spans"]))
    return " ".join(parts)


def normalize(text):
    t = re.sub(r'\s+', ' ', text).strip()
    t = re.sub(r'(\w)- (\w)', r'\1\2', t)  # 断词修复
    return t


# ============================================================
# 章节/版式探测
# ============================================================

def detect_body_size(doc):
    """统计正文字号（按字符量加权）"""
    size_chars = Counter()
    for pno in range(doc.page_count):
        for block in doc[pno].get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    size_chars[round(span["size"], 1)] += len(span["text"])
    if not size_chars:
        return 12.0
    return size_chars.most_common(1)[0][0]


def collect_heading_candidates(doc, body_size, delta, head_lines=6):
    """heading 策略：每页开头 head_lines 块内，字号 >= body+delta 的短文本视为章节标题"""
    titles = {}  # pno -> title text
    for pno in range(doc.page_count):
        d = doc[pno].get_text("dict")
        blocks = [b for b in d["blocks"] if b["type"] == 0]
        blocks.sort(key=lambda b: b["bbox"][1])
        for b in blocks[:head_lines]:
            txt = normalize(block_text(b))
            if not txt or PAGE_NUM_RE.match(txt) or len(txt) > 100:
                continue
            max_size = 0
            for line in b["lines"]:
                for span in line["spans"]:
                    max_size = max(max_size, span["size"])
            if max_size >= body_size + delta and HEADING_TEXT_RE.match(txt):
                titles[pno] = txt
                break
    return titles


def collect_pattern_matches(doc, pattern, head_lines=10):
    """pattern 策略：正则匹配每页开头（去页码）若干行的拼接文本。
    支持多行标题（如 "Interim Letter\nFor the period ended ..."）：
    以命中所在的起始行作为章节标题锚点。"""
    rx = re.compile(pattern, re.I | re.M)
    titles = {}
    for pno in range(doc.page_count):
        raw = doc[pno].get_text("text")
        lines = [l.strip() for l in raw.split('\n')
                 if l.strip() and not PAGE_NUM_RE.match(l.strip())][:head_lines]
        joined = '\n'.join(lines)
        m = rx.search(joined)
        if m:
            # 命中起始行 = 标题锚点行
            start_line = joined[:m.start()].count('\n')
            title = lines[start_line] if start_line < len(lines) else m.group(0)
            titles[pno] = title[:100]
    return titles


def outline_toc(doc):
    """outline 策略：PDF 书签一级条目 -> {pno: title}"""
    toc = doc.get_toc()
    if not toc:
        return {}
    min_level = min(t[0] for t in toc)
    titles = {}
    for level, title, pno in toc:
        if level == min_level and pno - 1 not in titles:
            titles[pno - 1] = title.strip()
    return titles


def detect_headers_footers(doc, sample_pages=200):
    """高频页首/页尾短文本 → 页眉/页脚黑名单（仅过滤对应位置的匹配）"""
    n = doc.page_count
    step = max(1, n // sample_pages)
    top_c, bot_c = Counter(), Counter()
    sampled = 0
    for pno in range(0, n, step):
        d = doc[pno].get_text("dict")
        blocks = [b for b in d["blocks"] if b["type"] == 0]
        blocks.sort(key=lambda b: b["bbox"][1])
        texts = [normalize(block_text(b)) for b in blocks]
        texts = [t for t in texts if t and len(t) <= 80 and not PAGE_NUM_RE.match(t)]
        if texts:
            top_c[texts[0]] += 1
            if len(texts) > 1:
                bot_c[texts[-1]] += 1
        sampled += 1
    if sampled == 0:
        return set(), set()
    thresh = max(2, sampled * 0.3)
    return ({t for t, c in top_c.items() if c >= thresh},
            {t for t, c in bot_c.items() if c >= thresh})


# ============================================================
# 主提取
# ============================================================

def is_verbatim(text):
    """信头地址/电话/纯数据表行 → 保留原文不翻译"""
    t = text.strip()
    if len(t) <= 2:
        return True
    if PHONE_RE.search(t) and len(t) < 150:
        return True
    toks = t.split()
    if len(toks) > 4:
        numish = sum(1 for x in toks if re.fullmatch(r'[\d.,%()$+-]+|[AU]?\$[\d.,bnm]*', x))
        if numish / len(toks) > 0.45:
            return True
    return False


def extract_pdf(input_path: str, workdir: str, chapters_mode: str = 'auto',
                chapter_pattern: str = None, heading_delta: float = 2.0,
                scan_min_chars: int = 20, no_merge: bool = False,
                translate_type: str = 'chinese_only', output: str = ''):
    """入口：提取 PDF → workdir（chapters/ + paragraphs.json + meta.json 等）"""
    import fitz

    doc = fitz.open(input_path)
    workdir = Path(workdir)
    for sub in ('chapters', 'images', 'scans'):
        (workdir / sub).mkdir(parents=True, exist_ok=True)

    body_size = detect_body_size(doc)

    # 1. 章节标题定位
    if chapters_mode == 'auto':
        chapters_mode = 'outline' if doc.get_toc() else 'heading'
    if chapters_mode == 'outline':
        titles = outline_toc(doc)
        if not titles:
            print("⚠️  PDF 无书签目录，回退到 heading 自动检测", file=sys.stderr)
            titles = collect_heading_candidates(doc, body_size, heading_delta)
            chapters_mode = 'heading'
    elif chapters_mode == 'pattern':
        if not chapter_pattern:
            raise ValueError("--chapters pattern 需要 --chapter-pattern 正则")
        titles = collect_pattern_matches(doc, chapter_pattern)
    else:
        titles = collect_heading_candidates(doc, body_size, heading_delta)

    if len(titles) < 2:
        raise SystemExit(
            f"仅检测到 {len(titles)} 个章节标题，章节切分不可靠。\n"
            f"建议：1) 用 --chapters pattern --chapter-pattern '...' 提供章节标题正则；\n"
            f"      2) 或 --chapters outline（需要 PDF 书签）；\n"
            f"      3) 或调大 --heading-delta（当前 {heading_delta}）。")

    # 2. 组章节：front（首个标题前的页）+ 各标题页区间
    starts = sorted(titles.keys())
    headers, footers = detect_headers_footers(doc)
    if headers or footers:
        print(f"页眉黑名单: {len(headers)} 条，页脚黑名单: {len(footers)} 条")

    chapters = []
    if starts[0] > 0:
        chapters.append({"id": "front", "title_en": "Front Matter",
                         "pages": [1, starts[0]], "blocks": []})
    for i, pno in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else doc.page_count
        chapters.append({"id": f"chapter_{i+1:02d}", "title_en": titles[pno],
                         "pages": [pno + 1, end], "blocks": [],
                         "heading_page": pno})

    # 3. 逐页提取块
    img_count = scan_count = 0
    for ch in chapters:
        p0, p1 = ch["pages"]
        for pno in range(p0 - 1, p1):
            page = doc[pno]
            page_h = page.rect.height
            raw = page.get_text("text")
            if len(raw.strip()) < scan_min_chars:
                pix = page.get_pixmap(dpi=200)
                fn = f"scans/scan_p{pno+1:03d}.png"
                pix.save(str(workdir / fn))
                ch["blocks"].append({"t": "scan", "file": fn, "page": pno + 1})
                scan_count += 1
                continue
            d = page.get_text("dict")
            blocks = sorted(d["blocks"], key=lambda b: (b["bbox"][1], b["bbox"][0]))
            page_first, page_last = True, True  # 首个/末个文本块（页眉页脚判定用）
            for b in blocks:
                y0, y1 = b["bbox"][1], b["bbox"][3]
                if b["type"] == 1:  # 图片
                    img_count += 1
                    try:
                        fn = f"images/img_{img_count:03d}_p{pno+1:03d}.{b.get('ext', 'png')}"
                        (workdir / fn).write_bytes(b["image"])
                        small = min(b.get("width", 0), b.get("height", 0)) < 60
                        ch["blocks"].append({"t": "img", "file": fn, "y": round(y0),
                                             **({"small": True} if small else {})})
                    except Exception as e:
                        print(f"  !! 图片提取失败 p{pno+1}: {e}", file=sys.stderr)
                    continue
                txt = normalize(block_text(b))
                if not txt or PAGE_NUM_RE.match(txt):
                    continue
                if page_first and txt in headers and y0 < page_h * 0.2:
                    page_first = False
                    continue
                page_first = False
                if page_last and txt in footers and y1 > page_h * 0.8:
                    continue
                page_last = False
                if len(txt) <= 1:
                    continue
                heading = (pno == ch.get("heading_page")
                           and txt == ch["title_en"] and not no_merge)
                ch["blocks"].append({"t": "p", "text": txt, "y": round(y0),
                                     **({"heading": True} if heading else {})})
            # 标记末个文本块已消费（简化：循环内按顺序处理即可）
    # 扫描版警告
    if scan_count > doc.page_count * 0.6:
        print(f"⚠️  {scan_count}/{doc.page_count} 页为扫描页——这是扫描版书籍。\n"
              f"   整本识图翻译不现实，建议先用 OCR 工具转文本后再运行本流程。", file=sys.stderr)

    # 4. 跨页段落合并
    if not no_merge:
        for ch in chapters:
            merged = []
            for blk in ch["blocks"]:
                if (blk["t"] == "p" and merged and merged[-1]["t"] == "p"
                        and not blk.get("heading") and not merged[-1].get("heading")):
                    prev = merged[-1]["text"]
                    if prev and prev[-1] not in '.!?:;”"\')' and blk["text"][:1].islower():
                        merged[-1]["text"] = prev + " " + blk["text"]
                        continue
                merged.append(blk)
            ch["blocks"] = merged

    # 5. 落盘 + 兼容 BookCraft 翻译流程的 paragraphs.json
    paragraphs, pids, verbatim_count = [], [], 0
    for ch in chapters:
        for idx, blk in enumerate(ch["blocks"]):
            if blk["t"] != "p":
                continue
            if is_verbatim(blk["text"]):
                blk["verbatim"] = True
                verbatim_count += 1
                continue
            pid = f"{ch['id']}:{idx}"
            paragraphs.append(blk["text"])
            pids.append(pid)
        ch.pop("heading_page", None)
        ch["words"] = sum(len(b.get("text", "").split()) for b in ch["blocks"])
        with open(workdir / "chapters" / f"{ch['id']}.json", "w", encoding="utf-8") as f:
            json.dump(ch, f, ensure_ascii=False, indent=1)

    with open(workdir / "paragraphs.json", "w", encoding="utf-8") as f:
        json.dump({"paragraphs": paragraphs}, f, ensure_ascii=False, indent=2)
    with open(workdir / "pids.json", "w", encoding="utf-8") as f:
        json.dump({"pids": pids}, f, ensure_ascii=False, indent=2)

    # 术语表候选（复用 BookCraft 主脚本逻辑）
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from translate import extract_glossary_candidates
        candidates = extract_glossary_candidates(paragraphs)
    except Exception:
        from collections import Counter as _C
        freq = _C()
        for p in paragraphs:
            for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b", p):
                freq[m.group(0)] += 1
        candidates = [t for t, c in freq.most_common(300) if c >= 2]
    with open(workdir / "glossary_candidates.json", "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates}, f, ensure_ascii=False, indent=2)

    meta = {
        "type": "pdf", "input": str(input_path), "output": output,
        "translate_type": translate_type,
        "chapter_strategy": chapters_mode, "chapter_pattern": chapter_pattern,
        "body_font_size": body_size,
        "chapters": [{"id": c["id"], "title_en": c["title_en"], "pages": c["pages"],
                      "words": c["words"]} for c in chapters],
        "total_paragraphs": len(paragraphs),
        "verbatim_paragraphs": verbatim_count,
        "content_images": img_count, "scan_pages": scan_count,
    }
    with open(workdir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "extracted", "type": "pdf",
        "chapter_strategy": chapters_mode,
        "chapters": len(chapters),
        "total_paragraphs": len(paragraphs),
        "verbatim_paragraphs": verbatim_count,
        "content_images": img_count, "scan_pages": scan_count,
        "workdir": str(workdir),
        "next_step": "Agent 先翻译术语表写入 glossary.json，再分批翻译段落写入 translations.json"
                     "（数组与 paragraphs.json 一一对应）；扫描页识图翻译写 scan_translations.json",
    }, ensure_ascii=False, indent=1))
    return meta


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='BookCraft PDF 提取层')
    ap.add_argument('--input', required=True)
    ap.add_argument('--workdir', required=True)
    ap.add_argument('--chapters', choices=['auto', 'outline', 'heading', 'pattern'],
                    default='auto')
    ap.add_argument('--chapter-pattern', help='章节标题正则（--chapters pattern 时必需）')
    ap.add_argument('--heading-delta', type=float, default=2.0,
                    help='标题字号超出正文的差值，默认 2.0pt')
    ap.add_argument('--scan-min-chars', type=int, default=20,
                    help='整页文本少于该字符数判定为扫描页，默认 20')
    ap.add_argument('--no-merge', action='store_true', help='禁用跨页段落合并')
    ap.add_argument('--type', choices=['bilingual', 'chinese_only'],
                    default='chinese_only')
    a = ap.parse_args()
    extract_pdf(a.input, a.workdir, a.chapters, a.chapter_pattern, a.heading_delta,
                a.scan_min_chars, a.no_merge, a.type)
