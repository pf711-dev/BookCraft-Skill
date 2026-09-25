#!/usr/bin/env python3
"""
双语翻译核心脚本

工作流程（配合 Agent 逐批翻译）：
  1. extract — 提取段落到 JSON，供 Agent 用当前对话模型翻译
  2. build   — 将翻译结果组装回最终的双语对照文件

用法：
  # 步骤1：提取段落
  python translate.py extract --input book.epub --workdir /tmp/bilingual_work

  # 步骤2：组装最终文件（Agent 翻译完段落写入 translations.json 后）
  python translate.py build --input book.epub --workdir /tmp/bilingual_work
"""

import argparse
import html as html_module
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

# 抑制 XHTML 被当作 HTML 解析的警告
import warnings
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

try:
    import mistune
    HAS_MISTUNE = True
except ImportError:
    HAS_MISTUNE = False


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ChapterData:
    """章节预提取数据"""
    html_file: Path = None
    soup: object = None
    body_tag: object = None
    texts: list = field(default_factory=list)
    is_heading: list = field(default_factory=list)
    text_start_index: int = 0
    anchors: list = field(default_factory=list)
    anchor_sizes: list = field(default_factory=list)
    head_title: str = None          # <head><title> 文本
    head_title_index: int = -1      # head_title 独立翻译时在 texts 中的位置
    head_title_h1_offset: int = -1  # head_title 与正文首个 h1 相同时，h1 在 texts 中的起始位置


# ============================================================
# EPUB 操作
# ============================================================

def extract_epub(epub_path: str, extract_path: Path):
    """解压 EPUB 文件"""
    extract_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(epub_path, 'r') as zip_ref:
        zip_ref.extractall(extract_path)


def find_html_files(epub_path: Path) -> list[Path]:
    """找到 EPUB 中所有的 HTML/XHTML 文件"""
    html_files = []
    for ext in ['*.html', '*.xhtml', '*.htm']:
        html_files.extend(epub_path.rglob(ext))
    return sorted(html_files, key=lambda x: x.name)


TEXT_CONTAINER_TAGS = ('p', 'div', 'blockquote', 'li', 'td', 'th',
                       'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                       'figcaption', 'caption', 'dt', 'dd')

HEADING_TAGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')

# 需要保留但无可翻译文本的媒体节点（纯图片段等）
MEDIA_TAGS = ('img', 'svg', 'video', 'audio', 'iframe', 'math',
              'object', 'embed', 'picture', 'canvas')


def _tokens_text(nodes: list) -> str:
    """拼接段内节点的纯文本"""
    parts = []
    for node in nodes:
        if hasattr(node, 'name'):
            if node.name == 'br':
                parts.append(' ')
            elif node.find('br') is not None:
                # 嵌套 <br/> 的标签整体保留：用空格分隔文本，避免文字粘连
                parts.append(node.get_text(' '))
            else:
                parts.append(node.get_text())
        else:
            parts.append(str(node))
    return ''.join(parts)


def _collect_leaf_tokens(container, tokens: list) -> bool:
    """把容器内容按文档序展开为 token 流。

    规则：
      - <br/> 作为段落分隔符标记（特殊字符串 'BR'）
      - 不含 <br/> 的后代标签（如 <a>/<b>/<u>/<img>）作为整体节点保留，
        以还原原文富文本格式
      - 含 <br/> 的后代标签（如包裹大段文本的 <font>/<div>）继续递归展开，
        使其内部的 <br/> 能作为段落分隔符生效
    """
    has_br = False
    for child in container.children:
        if hasattr(child, 'name') and child.name:
            if child.name == 'br':
                tokens.append('BR')
                has_br = True
            elif child.find('br') is not None:
                # 后代标签内部含 <br/>：递归展开，让内部 <br/> 拆段生效
                if _collect_leaf_tokens(child, tokens):
                    has_br = True
            else:
                # 不含 <br/> 的后代标签（<a>/<b>/<u>/<img>/<span> 等）整体保留，
                # 避免拆段时丢失包裹标签（链接、图片等）
                tokens.append(child)
        else:
            # 文本节点
            if isinstance(child, str):
                tokens.append(child)
    return has_br


def split_container_segments(container) -> tuple:
    """把文本容器按 <br/> 拆分为多个子段。

    返回 (seg_nodes_list, seg_texts)：
      - seg_nodes_list：每段包含的 DOM 节点列表（保留原富文本标签）
      - seg_texts：每段对应的纯文本（已 strip，过滤空段）
    """
    tokens = []
    _collect_leaf_tokens(container, tokens)

    segments = []
    cur = []
    for token in tokens:
        if token == 'BR':
            if cur:
                segments.append(cur)
                cur = []
        else:
            cur.append(token)
    if cur:
        segments.append(cur)

    seg_nodes = []
    seg_texts = []
    for seg in segments:
        text = _tokens_text(seg).strip()
        if text:
            seg_nodes.append(seg)
            seg_texts.append(text)
        elif _segment_has_media(seg):
            # 纯媒体段（图片/表格/公式等）：无文本可译，但必须保留节点，
            # 否则写回替换段落时这些节点会随原段落一起丢失
            seg_nodes.append(seg)
            seg_texts.append('')
    return seg_nodes, seg_texts


def _segment_has_media(nodes: list) -> bool:
    """判断段内是否包含媒体节点（图片/公式等）"""
    from bs4 import Tag
    for node in nodes:
        if not isinstance(node, Tag):
            continue
        if node.name in MEDIA_TAGS:
            return True
        if node.find(list(MEDIA_TAGS)):
            return True
    return False


def pre_extract_chapter(html_file: Path) -> ChapterData:
    """从 HTML 文件预提取段落（保留 DOM 锚点，支持 <br/> 分段）"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # 统一使用 lxml 解析，避免 XML 解析器对命名空间/未闭合标签的兼容问题
        soup = BeautifulSoup(content, 'lxml')
        body = soup.find('body')
        if not body:
            return None

        texts = []
        is_heading = []
        anchors = []
        anchor_sizes = []
        for elem in body.find_all(TEXT_CONTAINER_TAGS):
            # 只处理最内层文本容器（排除包含其他容器的外层容器，避免重复提取）
            if elem.find(TEXT_CONTAINER_TAGS):
                continue
            text = elem.get_text().strip()
            if not text:
                continue
            is_h = elem.name in HEADING_TAGS
            # 标题与正文统一按 >2 字符提取，避免图注/表格短文本/伪标题被漏译
            min_len = 2
            if len(text) <= min_len:
                continue
            if elem.find_parent('pre'):
                continue
            seg_nodes, seg_texts = split_container_segments(elem)
            if not seg_texts:
                continue
            anchors.append(elem)
            anchor_sizes.append(len(seg_texts))
            texts.extend(seg_texts)
            is_heading.extend([is_h] * len(seg_texts))

        # <head><title>：很多 EPUB 的章节标题只存在于 head title / TOC 中，
        # 正文没有 h1-h6。这里单独提取，与正文首个 h1 相同则复用其翻译，
        # 否则作为独立段落翻译，写回时更新 <title> 标签。
        title_tag = soup.find('title')
        head_title = title_tag.get_text().strip() if title_tag else ''

        if not texts and not head_title:
            return None

        head_title_index = -1
        head_title_h1_offset = -1
        if head_title:
            first_h1_offset = -1
            for idx, anchor in enumerate(anchors):
                if anchor.name in HEADING_TAGS:
                    first_h1_offset = sum(anchor_sizes[:idx])
                    break
            if (first_h1_offset >= 0 and first_h1_offset < len(texts)
                    and _normalize_text(head_title).lower()
                    == _normalize_text(texts[first_h1_offset]).lower()):
                head_title_h1_offset = first_h1_offset
            else:
                head_title_index = len(texts)
                texts.append(head_title)
                is_heading.append(True)

        return ChapterData(
            html_file=html_file,
            soup=soup,
            body_tag=body,
            texts=texts,
            is_heading=is_heading,
            anchors=anchors,
            anchor_sizes=anchor_sizes,
            head_title=head_title,
            head_title_index=head_title_index,
            head_title_h1_offset=head_title_h1_offset,
        )
    except Exception as e:
        print(f"预提取 {html_file.name} 失败: {e}", flush=True)
        return None


def build_dom_english_block(soup, seg_nodes: list):
    """构建英文段落 div，保留段内富文本节点"""
    div = soup.new_tag('div', **{'class': 'english'})
    for node in seg_nodes:
        div.append(node)
    return div


def build_dom_chinese_div(soup, text: str, cls: str = 'chinese'):
    """构建中文翻译 div"""
    div = soup.new_tag('div', **{'class': cls})
    div.string = text
    return div


def build_dom_translated_blocks(soup, seg_nodes_list, seg_texts, translations,
                                translate_type: str = "bilingual") -> list:
    """为每个子段构建双语对照块（保留原 DOM 节点）"""
    blocks = []
    for i, seg_nodes in enumerate(seg_nodes_list):
        zh = translations[i] if i < len(translations) else ''
        if translate_type == 'chinese_only':
            if zh.strip():
                block = soup.new_tag('div', **{'class': 'bilingual-block'})
                block.append(build_dom_chinese_div(soup, zh, 'chinese-solo'))
                blocks.append(block)
            elif _segment_has_media(seg_nodes):
                # 纯媒体段：无可译文本，保留原节点避免丢失图片等
                block = soup.new_tag('div', **{'class': 'bilingual-block'})
                block.append(build_dom_english_block(soup, seg_nodes))
                blocks.append(block)
        else:
            block = soup.new_tag('div', **{'class': 'bilingual-block'})
            block.append(build_dom_english_block(soup, seg_nodes))
            if zh.strip():
                block.append(build_dom_chinese_div(soup, zh))
            blocks.append(block)
    return blocks


def write_back_chapter(cd: ChapterData, translations: list[str],
                       translate_type: str = "bilingual"):
    """将翻译结果写回章节 HTML（保留原文 DOM，仅把文本容器替换为双语块）"""
    soup = cd.soup
    offset = 0
    for anchor, size in zip(cd.anchors, cd.anchor_sizes):
        zh_slice = translations[offset:offset + size]
        offset += size
        seg_nodes, seg_texts = split_container_segments(anchor)
        if not seg_texts:
            continue
        is_h = anchor.name in HEADING_TAGS
        if is_h:
            # 标题：多子段时拼接所有翻译
            zh = ' '.join(z.strip() for z in zh_slice if z and z.strip())
            if zh.strip():
                if translate_type == 'chinese_only':
                    # 仅中文：标题内容直接替换为中文（保留标题标签，沿用原书标题样式）
                    anchor.clear()
                    anchor.string = zh
                else:
                    # 双语：保留英文标题，紧随其后插入中文
                    anchor.insert_after(build_dom_chinese_div(soup, zh, 'chinese-title'))
        elif anchor.name in ('blockquote', 'li', 'td', 'th', 'figcaption', 'caption', 'dt', 'dd'):
            # 引用/列表项/表格单元格/图注等：保留外壳，内部重建为对照块
            blocks = build_dom_translated_blocks(soup, seg_nodes, seg_texts, zh_slice, translate_type)
            if not blocks:
                continue  # 全部翻译失败：保留原内容，避免内容丢失
            anchor.clear()
            for b in blocks:
                anchor.append(b)
        else:
            # 普通段落等：替换为对照块序列
            blocks = build_dom_translated_blocks(soup, seg_nodes, seg_texts, zh_slice, translate_type)
            if not blocks:
                continue  # 全部翻译失败：保留原内容，避免内容丢失
            anchor.replace_with(*blocks)

    # 更新 <head><title>：与正文 h1 相同则复用 h1 翻译，否则用独立翻译
    if cd.head_title:
        zh = ''
        if cd.head_title_index >= 0 and cd.head_title_index < len(translations):
            zh = translations[cd.head_title_index].strip()
        elif cd.head_title_h1_offset >= 0 and cd.head_title_h1_offset < len(translations):
            zh = translations[cd.head_title_h1_offset].strip()
        if zh:
            title_tag = soup.find('title')
            if title_tag:
                title_tag.string = zh

    with open(cd.html_file, 'w', encoding='utf-8') as f:
        f.write(str(cd.soup))
    print(f"  写回完成: {cd.html_file.name}", flush=True)


def _strip_chinese_vertical_bar(css_content: str) -> str:
    """移除中文对照块左侧的竖线样式（border-left / padding-left）"""
    for selector in ('.bilingual-block .chinese', '.chinese-title'):
        pattern = re.compile(re.escape(selector) + r'\s*\{[^}]*\}', re.DOTALL)

        def repl(m):
            block = m.group(0)
            block = re.sub(r'\s*border-left\s*:\s*[^;]+;\s*', '\n', block)
            block = re.sub(r'\s*padding-left\s*:\s*[^;]+;\s*', '\n', block)
            return block

        css_content = pattern.sub(repl, css_content)
    return css_content


def add_styles(epub_path: Path):
    """添加或更新 CSS 样式"""
    css_files = list(epub_path.rglob('*.css'))
    styles = '''
/* 双语翻译样式 */
.bilingual-content {
    margin: 1em 0;
}

.bilingual-block {
    margin-bottom: 1.5em;
}

.bilingual-block .english {
    font-size: 1em;
    line-height: 1.6;
    color: #333;
    margin-bottom: 0.5em;
}

.bilingual-block .chinese {
    font-size: 0.95em;
    line-height: 1.8;
    color: #555;
    margin-top: 0.5em;
}

.bilingual-block.bilingual-heading .english {
    font-weight: bold;
}

.bilingual-block.bilingual-heading .chinese {
    font-weight: bold;
}

.bilingual-block .chinese-solo {
    font-size: 1em;
    line-height: 1.8;
    color: #333;
}

/* 标题后的中文翻译 */
.chinese-title {
    font-size: 1em;
    font-weight: bold;
    color: #555;
    margin: 0 0 1em 0;
}
'''
    if css_files:
        for css_file in css_files:
            try:
                with open(css_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                cleaned = _strip_chinese_vertical_bar(content)
                if cleaned != content:
                    with open(css_file, 'w', encoding='utf-8') as f:
                        f.write(cleaned)
                    print(f"    已清理竖线样式: {css_file.name}", flush=True)
                if '.bilingual-block' not in content:
                    with open(css_file, 'a', encoding='utf-8') as f:
                        f.write('\n' + styles)
                    print(f"    已更新样式: {css_file.name}", flush=True)
            except Exception as e:
                print(f"    更新样式失败: {e}", flush=True)
    else:
        # 无 CSS 文件时创建样式文件
        oebps_path = epub_path / 'OEBPS'
        if not oebps_path.exists():
            oebps_path = epub_path
        css_file = oebps_path / 'styles.css'
        try:
            with open(css_file, 'w', encoding='utf-8') as f:
                f.write(styles.lstrip())
            print(f"    已创建样式文件: {css_file.name}", flush=True)
        except Exception as e:
            print(f"    创建样式文件失败: {e}", flush=True)


def pack_epub(source_path: Path, output_path: str):
    """重新打包 EPUB"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in source_path.rglob('*'):
            if file_path.is_file():
                arcname = file_path.relative_to(source_path)
                zipf.write(file_path, arcname)


# ============================================================
# Markdown 操作
# ============================================================

def extract_markdown_paragraphs(file_path: str) -> dict:
    """从 Markdown 文件提取段落"""
    with open(file_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Markdown → HTML
    if HAS_MISTUNE:
        md = mistune.create_markdown(escape=False, plugins=['table', 'footnotes'])
        html_content = md(md_content)
    else:
        html_content = _md_to_html_fallback(md_content)

    # 提取段落
    soup = BeautifulSoup(f'<html><body>{html_content}</body></html>', 'lxml')
    body = soup.find('body')

    paragraphs = []
    for elem in body.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'li']):
        text = elem.get_text().strip()
        is_h = elem.name in HEADING_TAGS
        # 标题与正文统一按 >2 字符提取，避免短文本漏译（对齐 EPUB 处理）
        min_len = 2
        if len(text) > min_len and not elem.find_parent('pre'):
            paragraphs.append({
                'text': html_module.unescape(text),
                'is_heading': is_h,
                'tag': elem.name,
            })

    return {
        'paragraphs': paragraphs,
        'original_md': md_content,
    }


def build_bilingual_markdown(original_md: str, paragraphs: list[dict],
                              translations: list[str],
                              translate_type: str = "bilingual") -> str:
    """构建翻译后的 Markdown（按逻辑块匹配，鲁棒支持多行段落/列表/链接）

    - bilingual：中英对照，中文以 `>` 引用块附在原文之后
    - chinese_only：整块替换为中文（标题保留 # 前缀，列表项保留列表符号）
    """
    lines = original_md.split('\n')
    result_lines = []
    para_idx = 0
    n = len(lines)
    i = 0

    def emit_translation(idx):
        if idx < len(paragraphs) and not paragraphs[idx]['is_heading']:
            if idx < len(translations) and translations[idx].strip():
                result_lines.append('')
                result_lines.append(f'> 🇨🇳 {translations[idx]}')
                result_lines.append('')

    def emit_block(idx, block_lines):
        """输出一个已匹配的逻辑块"""
        if translate_type != 'chinese_only':
            result_lines.extend(block_lines)
            emit_translation(idx)
            return
        zh = translations[idx].strip() if idx < len(translations) else ''
        if not zh:
            result_lines.extend(block_lines)  # 无译文：保留原文，避免内容丢失
            return
        # 单行标题块：保留 # 前缀；其余块整体替换为中文
        m = re.match(r'^(#{1,6})\s+', block_lines[0]) if block_lines else None
        if (len(block_lines) == 1 and paragraphs[idx]['is_heading'] and m):
            result_lines.append(f"{m.group(1)} {zh}")
        else:
            result_lines.append(zh)

    while i < n:
        line = lines[i]
        # 围栏代码块整体跳过，不参与翻译
        if line.strip().startswith('```'):
            result_lines.append(line)
            i += 1
            while i < n and not lines[i].strip().startswith('```'):
                result_lines.append(lines[i])
                i += 1
            if i < n:
                result_lines.append(lines[i])
                i += 1
            continue

        # 收集一个逻辑块：连续非空行，空行作为块分隔符
        block_lines = []
        while i < n and lines[i].strip() != '':
            block_lines.append(lines[i])
            i += 1
        blank = 0
        while i < n and lines[i].strip() == '':
            blank += 1
            i += 1

        if not block_lines:
            result_lines.extend([''] * blank)
            continue

        # 分隔线块跳过
        if len(block_lines) == 1 and block_lines[0].strip() in ('---', '***', '___'):
            result_lines.append(block_lines[0])
            result_lines.extend([''] * blank)
            continue

        block_text = _strip_inline_markdown('\n'.join(block_lines))
        normalized = _normalize_text(html_module.unescape(block_text))

        # 尝试整块匹配一个段落（覆盖普通段落、多行段落、标题、引用）。
        # 使用完整相等而非前缀匹配，避免多行列表块误匹配单个列表项段落。
        if para_idx < len(paragraphs):
            para_norm = _normalize_text(html_module.unescape(paragraphs[para_idx]['text']))
            if normalized and para_norm and normalized == para_norm:
                emit_block(para_idx, block_lines)
                para_idx += 1
                result_lines.extend([''] * blank)
                continue

        # 整块不匹配时，尝试逐行匹配（列表项等，每行是一个独立段落）
        for bl in block_lines:
            bl_stripped = bl.strip()
            list_prefix = re.match(r'^([\-\*\+]|\d+[\.\)])\s+', bl_stripped)
            if not list_prefix:
                result_lines.append(bl)
                continue
            item_text = _strip_inline_markdown(bl_stripped)
            item_norm = _normalize_text(html_module.unescape(item_text))
            if (para_idx < len(paragraphs)
                    and item_norm
                    and item_norm == _normalize_text(html_module.unescape(paragraphs[para_idx]['text']))):
                zh_item = translations[para_idx].strip() if para_idx < len(translations) else ''
                pm = re.match(r'^(\s*)([\-\*\+]|\d+[\.\)])\s+', bl)
                if translate_type == 'chinese_only' and zh_item and pm:
                    # 仅中文：保留缩进与列表符号，替换为中文
                    result_lines.append(f"{pm.group(1)}{pm.group(2)} {zh_item}")
                else:
                    result_lines.append(bl)
                    emit_translation(para_idx)
                para_idx += 1
            else:
                result_lines.append(bl)
        result_lines.extend([''] * blank)

    return '\n'.join(result_lines)


def _strip_inline_markdown(text: str) -> str:
    """剥离行内 Markdown 语法，得到纯文本"""
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)        # 图片
    text = re.sub(r'`([^`]+)`', r'\1', text)                 # 行内代码
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)     # 链接
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)         # 粗斜体
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)             # 粗体
    text = re.sub(r'\*(.+?)\*', r'\1', text)                 # 斜体
    text = re.sub(r'^(#{1,6})\s+', '', text)                 # 标题前缀
    text = re.sub(r'^\>\s*', '', text)                       # 引用前缀
    text = re.sub(r'^[\-\*\+]\s+', '', text)                 # 无序列表前缀
    text = re.sub(r'^\d+[\.\)]\s+', '', text)                # 有序列表前缀
    return text.strip()


def _normalize_text(text: str) -> str:
    """折叠空白，用于段落比对"""
    return re.sub(r'\s+', ' ', text).strip()


def _md_to_html_fallback(md_content: str) -> str:
    """简易 Markdown → HTML 转换"""
    import re
    lines = md_content.split('\n')
    html_parts = []
    in_code_block = False
    in_list = False

    for line in lines:
        if line.strip().startswith('```'):
            if in_code_block:
                html_parts.append('</code></pre>')
                in_code_block = False
            else:
                html_parts.append('<pre><code>')
                in_code_block = True
            continue
        if in_code_block:
            html_parts.append(line)
            continue

        heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading_match:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            level = len(heading_match.group(1))
            html_parts.append(f'<h{level}>{heading_match.group(2)}</h{level}>')
            continue

        if line.startswith('> '):
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            html_parts.append(f'<blockquote><p>{line[2:]}</p></blockquote>')
            continue

        list_match = re.match(r'^[\-\*]\s+(.+)$', line)
        if list_match:
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            html_parts.append(f'<li>{list_match.group(1)}</li>')
            continue

        if in_list and line.strip() == '':
            html_parts.append('</ul>')
            in_list = False
            continue

        if line.strip() == '':
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            continue

        if in_list:
            html_parts.append('</ul>')
            in_list = False
        html_parts.append(f'<p>{line}</p>')

    if in_list:
        html_parts.append('</ul>')
    return '\n'.join(html_parts)


# ============================================================
# 术语表（Glossary）：保证人名/专有名词全书译名统一
# ============================================================

# 常见功能词/通用词，不作为专有名词候选
GLOSSARY_STOPWORDS = {
    'A', 'An', 'The', 'This', 'That', 'These', 'Those', 'They', 'There',
    'Their', 'Them', 'He', 'She', 'It', 'Its', 'His', 'Her', 'Him',
    'We', 'Our', 'Us', 'You', 'Your', 'I', 'Me', 'My', 'Mine', 'And',
    'But', 'Or', 'So', 'For', 'With', 'From', 'To', 'In', 'On', 'At',
    'By', 'Of', 'As', 'If', 'Then', 'Than', 'When', 'Where', 'What',
    'Which', 'Who', 'Whom', 'Why', 'How', 'Not', 'No', 'Yes', 'All',
    'Some', 'Any', 'One', 'Two', 'Three', 'Four', 'Five', 'Six',
    'Seven', 'Eight', 'Nine', 'Ten', 'First', 'Second', 'Third',
    'Chapter', 'Chapters', 'Part', 'Parts', 'Section', 'Sections',
    'Figure', 'Figures', 'Table', 'Tables', 'Introduction', 'Contents',
    'Preface', 'Epilogue', 'Appendix', 'Index', 'Notes', 'References',
    'Volume', 'Volumes', 'Edition', 'Book', 'Books',
    'January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December',
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
    'Sunday',
}

CANDIDATE_WORD_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b")

USER_GLOSSARY_PATH = Path.home() / '.bookcraft' / 'glossary.json'


def extract_glossary_candidates(paragraphs: list[str], max_terms: int = 300) -> list[str]:
    """从段落文本提取专有名词候选（连续大写单词序列，按出现次数排序）。

    规则：
      - 只提取长度 1~4 的连续大写单词序列
      - 任一词在停用词表中则跳过整条（如 "The Great Gatsby" 含 The 则跳过）
      - 只保留出现次数 >= 2 的词条（只出现一次的名字不存在译名不统一问题）
    """
    freq: dict[str, int] = {}
    for para in paragraphs:
        if not para:
            continue
        for m in CANDIDATE_WORD_RE.finditer(para):
            phrase = m.group(0)
            words = phrase.split()
            if any(w in GLOSSARY_STOPWORDS for w in words):
                continue
            if any(w.isdigit() for w in words):
                continue
            freq[phrase] = freq.get(phrase, 0) + 1
    ranked = sorted(((c, p) for p, c in freq.items() if c >= 2), reverse=True)
    return [p for c, p in ranked[:max_terms]]


def load_user_glossary() -> dict:
    """加载用户手动术语表 ~/.bookcraft/glossary.json（可手动补充/修正译名）"""
    try:
        if USER_GLOSSARY_PATH.exists():
            with open(USER_GLOSSARY_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v).strip() for k, v in data.items() if str(v).strip()}
    except Exception as e:
        print(f"  读取用户术语表失败: {e}", flush=True)
    return {}


def merge_glossary(manual: dict, auto: dict) -> dict:
    """合并术语表：手动优先于自动"""
    merged = dict(auto or {})
    merged.update(manual or {})
    return merged


def format_glossary_block(glossary: dict, max_terms: int = 200) -> str:
    """将术语表格式化为翻译 prompt 注入文本"""
    if not glossary:
        return ''
    lines = [f'- {k} → {v}' for k, v in glossary.items() if k and v]
    if not lines:
        return ''
    lines = lines[:max_terms]
    return '人名、地名、机构名等专有名词必须使用以下对照表中的译名，不得随意改译：\n' + '\n'.join(lines)


# ============================================================
# 主流程
# ============================================================

def detect_file_type(input_path: str) -> str:
    """检测文件类型"""
    ext = Path(input_path).suffix.lower()
    if ext == '.epub':
        return 'epub'
    elif ext == '.pdf':
        return 'pdf'
    elif ext in ('.md', '.markdown'):
        return 'markdown'
    else:
        raise ValueError(f"不支持的文件类型: {ext}，仅支持 .epub / .md / .pdf")


def run_extract(args):
    """提取段落到 JSON（供 Agent 逐批翻译）"""
    file_type = detect_file_type(args.input)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if file_type == 'epub':
        _extract_epub(args, workdir)
    elif file_type == 'pdf':
        _extract_pdf(args, workdir)
    else:
        _extract_markdown(args, workdir)


def _extract_pdf(args, workdir: Path):
    """提取 PDF（结构化提取层：章节/图片/扫描页/verbatim）"""
    try:
        from pdf_extract import extract_pdf
    except ImportError as e:
        raise SystemExit(f"PDF 支持需要 PyMuPDF：pip3 install pymupdf ({e})")
    extract_pdf(
        input_path=args.input, workdir=str(workdir),
        chapters_mode=getattr(args, 'chapters', 'auto'),
        chapter_pattern=getattr(args, 'chapter_pattern', None),
        heading_delta=getattr(args, 'heading_delta', 2.0),
        scan_min_chars=getattr(args, 'scan_min_chars', 20),
        translate_type=args.type,
        output=str(args.output) if args.output else '',
    )


def _extract_epub(args, workdir: Path):
    """提取 EPUB 段落"""
    epub_extract_path = workdir / "epub"
    extract_epub(args.input, epub_extract_path)

    html_files = find_html_files(epub_extract_path)
    all_paragraphs = []
    chapter_meta = []

    for html_file in html_files:
        cd = pre_extract_chapter(html_file)
        if cd and cd.texts:
            start = len(all_paragraphs)
            all_paragraphs.extend(cd.texts)
            chapter_meta.append({
                'file': str(html_file),
                'start': start,
                'count': len(cd.texts),
                'is_heading': cd.is_heading,
            })
        else:
            chapter_meta.append({
                'file': str(html_file),
                'start': len(all_paragraphs),
                'count': 0,
                'is_heading': [],
            })

    # 保存段落和元信息
    with open(workdir / 'paragraphs.json', 'w', encoding='utf-8') as f:
        json.dump({'paragraphs': all_paragraphs}, f, ensure_ascii=False, indent=2)

    # 术语表候选：供 Agent 先翻译术语表，再逐批翻译段落时注入，保证人名/专有名词译名统一
    candidates = extract_glossary_candidates(all_paragraphs)
    with open(workdir / 'glossary_candidates.json', 'w', encoding='utf-8') as f:
        json.dump({'candidates': candidates}, f, ensure_ascii=False, indent=2)

    with open(workdir / 'meta.json', 'w', encoding='utf-8') as f:
        json.dump({
            'type': 'epub',
            'input': str(args.input),
            'output': str(args.output) if args.output else '',
            'translate_type': args.type,
            'chapters': chapter_meta,
            'total_paragraphs': len(all_paragraphs),
        }, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        'status': 'extracted',
        'type': 'epub',
        'total_paragraphs': len(all_paragraphs),
        'workdir': str(workdir),
        'paragraphs_file': str(workdir / 'paragraphs.json'),
        'glossary_candidates': len(candidates),
        'next_step': 'Agent 先翻译术语表写入 glossary.json，再逐批翻译段落，结果写入 translations.json',
    }, ensure_ascii=False))


def _extract_markdown(args, workdir: Path):
    """提取 Markdown 段落"""
    data = extract_markdown_paragraphs(args.input)

    paragraphs = [p['text'] for p in data['paragraphs']]
    is_heading = [p['is_heading'] for p in data['paragraphs']]

    with open(workdir / 'paragraphs.json', 'w', encoding='utf-8') as f:
        json.dump({'paragraphs': paragraphs}, f, ensure_ascii=False, indent=2)

    with open(workdir / 'meta.json', 'w', encoding='utf-8') as f:
        json.dump({
            'type': 'markdown',
            'input': str(args.input),
            'output': str(args.output) if args.output else '',
            'translate_type': args.type,
            'total_paragraphs': len(paragraphs),
            'is_heading': is_heading,
            'original_md_file': str(workdir / 'original.md'),
        }, f, ensure_ascii=False, indent=2)

    # 保存原始 Markdown
    with open(workdir / 'original.md', 'w', encoding='utf-8') as f:
        f.write(data['original_md'])

    # 术语表候选：供 Agent 先翻译术语表，再逐批翻译段落时注入，保证人名/专有名词译名统一
    candidates = extract_glossary_candidates(paragraphs)
    with open(workdir / 'glossary_candidates.json', 'w', encoding='utf-8') as f:
        json.dump({'candidates': candidates}, f, ensure_ascii=False, indent=2)

    # 保存段落详情（包含 is_heading）
    with open(workdir / 'paragraph_details.json', 'w', encoding='utf-8') as f:
        json.dump(data['paragraphs'], f, ensure_ascii=False, indent=2)

    print(json.dumps({
        'status': 'extracted',
        'type': 'markdown',
        'total_paragraphs': len(paragraphs),
        'workdir': str(workdir),
        'paragraphs_file': str(workdir / 'paragraphs.json'),
        'glossary_candidates': len(candidates),
        'next_step': 'Agent 先翻译术语表写入 glossary.json，再逐批翻译段落，结果写入 translations.json',
    }, ensure_ascii=False))


def run_build(args):
    """从翻译结果组装最终文件"""
    workdir = Path(args.workdir)

    with open(workdir / 'meta.json', 'r', encoding='utf-8') as f:
        meta = json.load(f)

    with open(workdir / 'translations.json', 'r', encoding='utf-8') as f:
        translations = json.load(f).get('translations', [])

    file_type = meta['type']
    translate_type = meta.get('translate_type', 'bilingual')

    if file_type == 'epub':
        _build_epub(meta, translations, workdir)
    elif file_type == 'pdf':
        _build_pdf(meta, translations, workdir)
    else:
        _build_markdown(meta, translations, workdir)


def _build_pdf(meta, translations, workdir: Path):
    """组装 PDF 翻译结果（EPUB + Markdown）"""
    try:
        from pdf_build import build_pdf
    except ImportError as e:
        raise SystemExit(f"PDF 组装需要 ebooklib：pip3 install ebooklib ({e})")
    build_pdf(meta, translations, workdir)


def _output_suffix(translate_type: str) -> str:
    """按翻译类型生成输出文件后缀"""
    return '_chinese_only' if translate_type == 'chinese_only' else '_bilingual'


def _build_epub(meta, translations, workdir: Path):
    """组装 EPUB"""
    epub_extract_path = workdir / "epub"
    translate_type = meta.get('translate_type', 'bilingual')

    for chapter in meta['chapters']:
        if chapter['count'] == 0:
            continue
        html_file = Path(chapter['file'])
        cd = pre_extract_chapter(html_file)
        if cd:
            start = chapter['start']
            chapter_translations = translations[start:start + chapter['count']]
            write_back_chapter(cd, chapter_translations, translate_type)

    add_styles(epub_extract_path)
    output_path = meta.get('output', '')
    if not output_path:
        input_path = Path(meta['input'])
        output_path = str(input_path.parent / f"{input_path.stem}{_output_suffix(translate_type)}{input_path.suffix}")
    pack_epub(epub_extract_path, output_path)
    print(f"\n✅ 翻译完成: {output_path}", flush=True)


def _build_markdown(meta, translations, workdir: Path):
    """组装 Markdown"""
    with open(workdir / 'original.md', 'r', encoding='utf-8') as f:
        original_md = f.read()

    with open(workdir / 'paragraph_details.json', 'r', encoding='utf-8') as f:
        paragraphs = json.load(f)

    translate_type = meta.get('translate_type', 'bilingual')
    result_md = build_bilingual_markdown(original_md, paragraphs, translations,
                                         translate_type=translate_type)

    output_path = meta.get('output', '')
    if not output_path:
        input_path = Path(meta['input'])
        output_path = str(input_path.parent / f"{input_path.stem}{_output_suffix(translate_type)}.md")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result_md)
    print(f"\n✅ 翻译完成: {output_path}", flush=True)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='双语翻译工具')
    subparsers = parser.add_subparsers(dest='action', required=True,
                                        help='操作步骤：extract 提取段落 / build 组装文件')

    # extract 子命令
    p_extract = subparsers.add_parser('extract', help='提取段落到 JSON')
    p_extract.add_argument('--input', required=True, help='输入文件路径（.epub / .md / .pdf）')
    p_extract.add_argument('--workdir', help='工作目录（不传则自动创建临时目录）')
    p_extract.add_argument('--output', help='最终输出文件路径（可选）')
    p_extract.add_argument('--type', choices=['bilingual', 'chinese_only'], default='bilingual',
                            help='翻译类型：bilingual（中英对照）或 chinese_only（仅中文），默认 bilingual')
    # PDF 专用参数（其他类型忽略）
    p_extract.add_argument('--chapters', choices=['auto', 'outline', 'heading', 'pattern'],
                           default='auto', help='[PDF] 章节检测策略，默认 auto（有书签用 outline，否则 heading）')
    p_extract.add_argument('--chapter-pattern', help='[PDF] 章节标题正则（--chapters pattern 时必需）')
    p_extract.add_argument('--heading-delta', type=float, default=2.0,
                           help='[PDF] 标题字号超出正文的差值，默认 2.0pt')
    p_extract.add_argument('--scan-min-chars', type=int, default=20,
                           help='[PDF] 整页文本少于该字符数判定为扫描页，默认 20')
    p_extract.add_argument('--no-merge', action='store_true', help='[PDF] 禁用跨页段落合并')

    # build 子命令
    p_build = subparsers.add_parser('build', help='从翻译结果组装最终文件')
    p_build.add_argument('--input', required=True, help='原始输入文件路径（.epub / .md / .pdf）')
    p_build.add_argument('--workdir', required=True, help='工作目录（需与 extract 时相同）')

    args = parser.parse_args()

    if args.action == 'extract':
        if not args.workdir:
            args.workdir = tempfile.mkdtemp(prefix='bilingual_')
        if not args.output:
            suffix = _output_suffix(args.type)
            # PDF 输入输出为 EPUB；EPUB/Markdown 保持原格式后缀
            ext = '.epub' if Path(args.input).suffix.lower() == '.pdf' else Path(args.input).suffix
            args.output = str(Path(args.input).parent / f"{Path(args.input).stem}{suffix}{ext}")
        run_extract(args)
    elif args.action == 'build':
        run_build(args)


if __name__ == '__main__':
    main()
