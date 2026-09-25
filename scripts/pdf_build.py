#!/usr/bin/env python3
"""PDF 翻译结果组装模块（BookCraft PDF 支持 · 组装层）

从 chapters/*.json + paragraphs/pids/translations/scan_translations 组装：
  - 中文（chinese_only）或中英对照（bilingual）EPUB
  - 合并版 Markdown 备份

渲染规则：
  - 章标题：<h1>（heading 块用译文；无则用原文 title_en）
  - verbatim 块：浅灰底数据块，保留原文（防数字误译）
  - 图片：按提取锚点位置插入正文（<60px 小图跳过）
  - 扫描页：译文在前（结构化 h/p/i 块）+ 原图附后
  - 未翻译段落（translations 缺失）：保留原文，绝不丢内容

依赖：ebooklib
"""

import json
import re
from pathlib import Path


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS = """
body { font-family: 'PingFang SC', 'Hiragino Sans GB', 'Noto Serif CJK SC', serif;
       line-height: 1.75; margin: 0.5em 1em; }
h1 { font-size: 1.35em; text-align: center; margin: 1.2em 0 0.6em; line-height: 1.4; }
h3 { font-size: 1.05em; margin: 1.1em 0 0.4em; }
p { text-indent: 2em; margin: 0.35em 0; text-align: justify; }
p.en { text-indent: 2em; color: #555; font-size: 0.95em; }
p.zh { text-indent: 2em; }
.table-verbatim { font-size: 0.85em; color: #333; background: #f7f7f5;
                  padding: 0.6em 0.8em; margin: 0.6em 0;
                  white-space: pre-wrap; word-break: break-all; }
.scan-note { text-indent: 0; font-size: 0.85em; color: #666; }
.scan-box { background: #f7f7f5; padding: 0.6em 0.8em; margin: 0.6em 0; }
.scan-box p { text-indent: 0; font-size: 0.9em; margin: 0.3em 0; }
img { max-width: 100%; display: block; margin: 0.8em auto; }
.fig-center { text-align: center; }
hr { border: none; border-top: 1px solid #ccc; margin: 1.2em 0; }
"""


def img_media_type(fn):
    return "image/jpeg" if fn.lower().endswith((".jpg", ".jpeg")) else "image/png"


def render_scan(scan_file, info, md_out):
    """扫描页：译文在前 + 原图附后。返回 (html片段, md片段列表)"""
    parts = ["<hr/>"]
    md = [f"\n### {info.get('title', '原件扫描图')}\n"] if info else []
    if info:
        parts.append(f"<h3>{esc(info['title'])}</h3>")
        parts.append(f"<p class='scan-note'>［{esc(info.get('note', '此页为原件扫描图，以下为译文'))}］</p>")
        md.append(f"*{info.get('note', '此页为原件扫描图，以下为译文')}*\n")
        in_box = False
        for b in info.get("blocks", []):
            box_start = b.get("i", "").startswith("［")
            if box_start and not in_box:
                parts.append('<div class="scan-box">')
                in_box = True
            kind, text = ("h", b["h"]) if "h" in b else (("p", b["p"]) if "p" in b else ("i", b.get("i", "")))
            if kind == "h":
                parts.append(f"<p><strong>{esc(text)}</strong></p>")
                md.append(f"**{text}**\n")
            else:
                cls = "scan-note" if kind == "i" else ""
                cls_attr = f" class='{cls}'" if cls else ""
                parts.append(f"<p{cls_attr}>{esc(text).replace(chr(10), '<br/>')}</p>")
                md.append((f"*{text}*\n" if kind == "i" else f"{text}\n").replace("\n", "\n"))
        if in_box:
            parts.append("</div>")
    else:
        parts.append("<p class='scan-note'>［原件扫描图］</p>")
    parts.append(f"<div class='fig-center'><img src='{scan_file}' alt='原件扫描图'/></div>")
    md.append(f"（原件扫描图：{scan_file}）\n")
    return "\n".join(parts), md


def build_pdf(meta, translations, workdir):
    """入口：组装 EPUB + Markdown。translations 为与 paragraphs.json 对齐的数组。"""
    from ebooklib import epub

    workdir = Path(workdir)
    translate_type = meta.get("translate_type", "chinese_only")
    bilingual = translate_type == "bilingual"

    pids = json.load(open(workdir / "pids.json", encoding="utf-8"))["pids"]
    src_paragraphs = json.load(open(workdir / "paragraphs.json", encoding="utf-8"))["paragraphs"]
    pid2zh = {pid: (translations[i].strip() if i < len(translations) and translations[i] else None)
              for i, pid in enumerate(pids)}
    pid2src = dict(zip(pids, src_paragraphs))

    scan_cn = {}
    st = workdir / "scan_translations.json"
    if st.exists():
        scan_cn = json.load(open(st, encoding="utf-8"))

    # ---------- EPUB ----------
    book = epub.EpubBook()
    book.set_identifier(f"bookcraft-pdf-{Path(meta['input']).stem}")
    book.set_title(Path(meta["input"]).stem)
    book.set_language("zh")
    book.add_item(epub.EpubItem(uid="style", file_name="style/main.css",
                                media_type="text/css", content=CSS))

    for sub in ("images", "scans"):
        d = workdir / sub
        if d.exists():
            for fn in sorted(d.iterdir()):
                if fn.is_file():
                    book.add_item(epub.EpubItem(
                        uid=f"{sub}_{fn.name}", file_name=f"{sub}/{fn.name}",
                        media_type=img_media_type(fn.name), content=fn.read_bytes()))

    spine, toc = ["nav"], []
    md_all = [f"# {Path(meta['input']).stem}\n",
              "> BookCraft PDF 翻译。表格数据/信头地址等保留原文；扫描原件译文在前、原图附后。\n"]
    seq = 0
    for chmeta in meta["chapters"]:
        ch = json.load(open(workdir / "chapters" / f"{chmeta['id']}.json", encoding="utf-8"))
        seq += 1
        title = chmeta["title_en"]
        html = [f"<h1>{esc(title)}</h1>"]
        md = [f"\n---\n\n## {title}\n"]
        for idx, blk in enumerate(ch["blocks"]):
            pid = f"{ch['id']}:{idx}"
            if blk["t"] == "p":
                if blk.get("heading"):
                    zh = pid2zh.get(pid)
                    if zh:
                        html[-1] = f"<h1>{esc(zh)}</h1>"
                        md[-1] = f"\n---\n\n## {zh}\n"
                    continue
                if blk.get("verbatim"):
                    html.append(f"<div class='table-verbatim'>{esc(blk['text'])}</div>")
                    md.append(f"```\n{blk['text']}\n```\n")
                    continue
                zh = pid2zh.get(pid)
                if bilingual:
                    html.append(f"<p class='en'>{esc(blk['text'])}</p>")
                    if zh:
                        html.append(f"<p class='zh'>{esc(zh)}</p>")
                        md.append(f"{blk['text']}\n\n> 🇨🇳 {zh}\n")
                    else:
                        md.append(f"{blk['text']}\n")
                else:
                    html.append(f"<p>{esc(zh or blk['text'])}</p>")
                    md.append(f"{zh or blk['text']}\n")
            elif blk["t"] == "img" and not blk.get("small"):
                html.append(f"<div class='fig-center'><img src='{blk['file']}' alt='插图'/></div>")
                md.append(f"![插图]({blk['file']})\n")
            elif blk["t"] == "scan":
                rendered, md_scan = render_scan(blk["file"], scan_cn.get(Path(blk["file"]).name), md)
                html.append(rendered)
                md.extend(md_scan)

        ecl = epub.EpubHtml(title=title, file_name=f"{ch['id']}.xhtml", lang="zh")
        ecl.content = ("<html><head><link rel='stylesheet' href='style/main.css'/></head><body>"
                       + "\n".join(html) + "</body></html>")
        book.add_item(ecl)
        spine.append(ecl)
        toc.append(ecl)
        md_all.extend(md)

    book.toc = tuple(toc)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    output = meta.get("output") or str(
        Path(meta["input"]).parent /
        f"{Path(meta['input']).stem}{'_bilingual' if bilingual else '_chinese_only'}.epub")
    epub.write_epub(output, book)

    md_path = str(Path(output).with_suffix(".md"))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_all))

    # 完整性对账
    n_translated = sum(1 for p in pids if pid2zh.get(p))
    print(json.dumps({
        "status": "built", "output": output, "markdown": md_path,
        "paragraphs_total": len(pids), "paragraphs_translated": n_translated,
        "untranslated_kept_original": len(pids) - n_translated,
        "chapters": len(meta["chapters"]),
    }, ensure_ascii=False, indent=1))
    return output
