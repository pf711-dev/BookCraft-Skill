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
import copy
import html as html_module
import json
import re
import sys
import tempfile
import zipfile
from collections import defaultdict
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


# 纯页码/罗马数字条目（如 "17"、"xvii"、"125-26"）：无需翻译，导航写回时跳过
PURE_PAGE_RE = re.compile(r'^[\sivxlcdmIVXLCDM0-9,.;:\-–—]*$')


def _nav_text_ok(text: str) -> bool:
    """导航条目文本是否值得翻译：非空、长度>2、非纯页码"""
    t = text.strip()
    return bool(t) and len(t) > 2 and not PURE_PAGE_RE.match(t)


def _text_without_links(elem) -> str:
    """返回 elem 去除 <a> 子树后的剩余文本（浅拷贝，不改原 DOM）"""
    clone = copy.copy(elem)
    for a in clone.find_all('a'):
        a.decompose()
    return clone.get_text()


def extract_nav_entries(epub_extract_path: Path) -> list[dict]:
    """提取 EPUB 导航条目文本（nav.xhtml 的 toc/landmarks/page-list <a> + toc.ncx navLabel）。

    这些条目是阅读器目录面板显示的标题来源，常游离于正文容器结构之外
    （如 <li><a>Part One</a><ol>...</ol></li> 中的 <a> 不属于任何最内层容器），
    通用段落提取会遗漏；专门的导航机制保证中英目录一致。

    返回 [{file, seq, text}]，seq 为该文件内同规则筛选列表中的序号，
    写回时按同一规则重新定位，确定性成立（extract 与 build 之间文件不变）。
    """
    entries = []
    for f in find_html_files(epub_extract_path):
        try:
            soup = BeautifulSoup(f.read_text(encoding='utf-8'), 'lxml')
        except Exception:
            continue
        nav_links = [a for a in soup.find_all('a') if a.find_parent('nav') and _nav_text_ok(a.get_text())]
        for seq, a in enumerate(nav_links):
            entries.append({'file': str(f), 'seq': seq, 'text': a.get_text(strip=True)})
    for f in sorted(epub_extract_path.rglob('*.ncx')):
        try:
            soup = BeautifulSoup(f.read_text(encoding='utf-8'), 'xml')
        except Exception:
            continue
        labels = [nl for nl in soup.find_all('navLabel') if _nav_text_ok(nl.get_text())]
        for seq, nl in enumerate(labels):
            entries.append({'file': str(f), 'seq': seq, 'text': nl.get_text(strip=True)})
    return entries


def write_back_nav(meta: dict, translations: list[str]):
    """将导航条目译文写回 nav.xhtml / toc.ncx（保留 href 等属性）"""
    nav_entries = meta.get('nav_entries') or []
    nav_start = meta.get('nav_start', -1)
    if not nav_entries or nav_start < 0:
        return
    by_file = defaultdict(list)
    for i, e in enumerate(nav_entries):
        by_file[e['file']].append((i, e))
    written = 0
    for fname, items in by_file.items():
        f = Path(fname)
        if not f.exists():
            continue
        is_ncx = f.suffix == '.ncx'
        parser = 'xml' if is_ncx else 'lxml'
        try:
            soup = BeautifulSoup(f.read_text(encoding='utf-8'), parser)
        except Exception:
            continue
        if is_ncx:
            nodes = [nl for nl in soup.find_all('navLabel') if _nav_text_ok(nl.get_text())]
        else:
            nodes = [a for a in soup.find_all('a') if a.find_parent('nav') and _nav_text_ok(a.get_text())]
        for i, e in items:
            zh_idx = nav_start + i
            if zh_idx >= len(translations) or e['seq'] >= len(nodes):
                continue
            zh = translations[zh_idx].strip()
            if not zh:
                continue
            node = nodes[e['seq']]
            if is_ncx:
                target = node.find('text') or node
                target.string = zh
            else:
                href = node.get('href')
                node.clear()
                node.string = zh
                if href:
                    node['href'] = href
            written += 1
        f.write_text(str(soup), encoding='utf-8')
    print(f'  导航条目写回: {written} 条', flush=True)


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
            # <nav> 内的链接型锚点（阅读器目录条目 <li><a>xx</a></li> 等）：文本几乎全部来自 <a> 时
            # 不做 DOM 替换（会破坏 href 导致目录跳转失效），交由 nav_entries 机制翻译写回。
            # 非导航区域（如书内目录页、正文脚注链接）不受影响，保持原机制。
            if elem.find_parent('nav') and (elem.name == 'a' or (elem.find('a') and _text_without_links(elem).strip() == '')):
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
# Markdown 公式保护：$...$ / $$...$$ / \(...\) / \[...\] → ⟦Mn⟧ 占位符
#
# 数学公式不该翻译（模型改写极易破坏表达式），必须在 MD 原文层
# 替换为占位符穿越翻译批次，写回前还原。
# 注意：mistune 无 math 插件，HTML 层公式只是普通文本，所以保护
# 必须在 mistune 转换之前做。
# ============================================================

PLACEHOLDER_FMT = '⟦M{n}⟧'
# 严格占位符（我们生成的形态）
PLACEHOLDER_RE = re.compile(r'⟦\s*[Mm]\s*(\d+)\s*⟧')
# 宽容还原：容忍翻译模型把括号写成全角/方括号、增删空白、改大小写
PLACEHOLDER_TOLERANT_RE = re.compile(r'[⟦【\[]\s*[Mm]\s*(\d+)\s*[⟧】\]]')

# 代码围栏 / 行内代码先挖出保管，防止其中的 $（如 shell 变量、价格示例）被误当公式
CODE_FENCE_RE = re.compile(r'^([ \t]*)(`{3,}|~{3,})[^\n]*\n(?:.*?\n)?^\1\2[^\n]*$', re.M)
INLINE_CODE_RE = re.compile(r'`[^`\n]+`')

# LaTeX 定界符（最明确，优先识别）
PAREN_MATH_RE = re.compile(r'\\\((.+?)\\\)', re.S)
BRACKET_MATH_RE = re.compile(r'\\\[(.+?)\\\]', re.S)
DISPLAY_MATH_RE = re.compile(r'\$\$(.+?)\$\$', re.S)
# 行内 $...$ 采用类 pandoc 启发式避免货币误伤：
#   开 $ 前不紧贴字母/数字/$，开 $ 后非空白；闭 $ 前非空白，闭 $ 后不紧贴字母/数字。
#   "costs $100 and $200" 不匹配（闭 $ 前是空格）；"$E = mc^2$" 匹配。
INLINE_MATH_RE = re.compile(r'(?<!\\)(?<![\w$])\$([^\s$](?:[^$\n]*?[^\s$])?)\$(?![\w$])')


def protect_formulas(md_content: str) -> tuple[str, dict]:
    """MD 原文层公式保护：识别数学公式并替换为 ⟦Mn⟧ 占位符。

    返回 (保护后 MD, {序号: 公式原文含定界符})。保护后的 MD 用于
    mistune 转换、段落提取与写回匹配；公式原文存 formulas.json，
    build 时由 restore_formulas 统一还原。
    """
    formulas: dict[int, str] = {}
    stash: list[str] = []

    def _keep(m):
        stash.append(m.group(0))
        return f'\x00K{len(stash) - 1}\x00'

    protected = CODE_FENCE_RE.sub(_keep, md_content)
    protected = INLINE_CODE_RE.sub(_keep, protected)

    def _make_repl(pattern):
        def _repl(m):
            idx = len(formulas) + 1
            formulas[idx] = m.group(0)
            return PLACEHOLDER_FMT.format(n=idx)
        return lambda s: pattern.sub(_repl, s)

    for pattern in (PAREN_MATH_RE, BRACKET_MATH_RE, DISPLAY_MATH_RE, INLINE_MATH_RE):
        protected = _make_repl(pattern)(protected)

    # 还原代码块/行内代码
    def _unkeep(m):
        i = int(m.group(1))
        return stash[i] if i < len(stash) else m.group(0)

    protected = re.sub(r'\x00K(\d+)\x00', _unkeep, protected)
    return protected, formulas


def restore_formulas(text: str, formulas: dict) -> str:
    """宽容还原 ⟦Mn⟧ 占位符为公式原文（容忍括号变体/空白/大小写差异）。

    引用行（> 前缀）内的块级公式还原为多行时，续行自动补引用前缀，
    避免公式撑破 bilingual 译文引用块结构。
    """
    if not formulas:
        return text

    def _repl(m):
        # JSON 读回后 key 为字符串，protect 时为 int，双形态兼容
        formula = formulas.get(m.group(1)) or formulas.get(int(m.group(1)))
        if not formula:
            return m.group(0)
        # 替换点是否位于引用行：看行首到替换点之间的前缀
        line_start = text.rfind('\n', 0, m.start()) + 1
        if text[line_start:m.start()].lstrip().startswith('>'):
            formula = formula.replace('\n', '\n> ')
        return formula

    return PLACEHOLDER_TOLERANT_RE.sub(_repl, text)


def _translation_valid(src_text: str, translation: str) -> bool:
    """公式占位符完整性校验：原文含 ⟦Mn⟧，译文必须同样齐全（容忍形态差异）。

    缺失视为译文无效（模型吞掉/改写了公式）——调用方回退英文原文，防公式丢失。
    """
    need = {int(n) for n in PLACEHOLDER_RE.findall(src_text or '')}
    if not need:
        return True
    got = {int(n) for n in PLACEHOLDER_TOLERANT_RE.findall(translation or '')}
    return need <= got


# ============================================================
# Markdown 表格：整表作为一个翻译单元（表头 + 各行）
#
# 整表粒度保证列语义（如 "Q1" 知道是季度列）；写回时按单元格
# 重建 | 结构，解析失败 / 列数不匹配 / 占位符缺失 → 整表回退英文。
# ============================================================

MD_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$')
HAS_LETTER_RE = re.compile(r'[A-Za-z]')


def _is_table_block(block_lines: list[str]) -> bool:
    """逻辑块是否为 Markdown 表格：含对齐行（|---|---|）且多数行含 |"""
    nonempty = [l for l in block_lines if l.strip()]
    if len(nonempty) < 2:
        return False
    has_sep = any(MD_TABLE_SEP_RE.match(l) for l in nonempty)
    pipes = sum(1 for l in nonempty if '|' in l)
    return has_sep and pipes >= len(nonempty) - 1


def _split_md_row(line: str) -> list[str]:
    """表格行 → 单元格列表（处理反斜杠转义的竖线）"""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|') and not s.endswith('\\|'):
        s = s[:-1]
    s = s.replace('\\|', '\x00')
    return [c.strip().replace('\x00', '|') for c in s.split('|')]


def _join_md_row(cells: list[str]) -> str:
    """单元格列表 → 表格行（单元格内竖线转义为反斜杠竖线）"""
    escaped = [c.replace('|', '\\|') for c in cells]
    return '| ' + ' | '.join(escaped) + ' |'


def _extract_table_unit(table_elem) -> dict | None:
    """HTML <table> → 结构化翻译单元；无可译文字（全数字/空表）返回 None。"""
    header: list[str] = []
    rows: list[list[str]] = []
    thead = table_elem.find('thead')
    if thead:
        for tr in thead.find_all('tr'):
            header = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
    tbody = table_elem.find('tbody') or table_elem
    first_body_row_is_header = False
    for tr in tbody.find_all('tr'):
        if thead and tr.find_parent('thead'):
            continue
        cells = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
        if not cells:
            continue
        if not thead and not rows and not header and all(c.name == 'th' for c in tr.find_all(['th', 'td'])):
            header = cells  # 无 thead 时首行全 th 视为表头
            continue
        rows.append(cells)
    if not header and not rows:
        return None
    texts = list(header) + [c for row in rows for c in row]
    if not any(HAS_LETTER_RE.search(t) for t in texts):
        return None  # 纯数字/符号表：翻译无增益，跳过
    return {'header': header, 'rows': rows}


def serialize_table_unit(unit: dict) -> str:
    """表格单元 → 翻译文本（结构化多行，Agent 按格式返回逐格译文）"""
    esc = lambda s: s.replace('|', '\\|')
    lines = ['[TABLE]']
    if unit['header']:
        lines.append('Header: ' + ' | '.join(esc(c) for c in unit['header']))
    for row in unit['rows']:
        lines.append('Row: ' + ' | '.join(esc(c) for c in row))
    return '\n'.join(lines)


def _parse_table_translation(text: str, n_cols: int):
    """宽容解析表格译文：Header:/Row: 前缀可缺省（退化为纯 | 行）。

    返回 (header|None, rows|None)。整体解析失败（无任何行列数匹配）
    返回 (None, None)，调用方整表回退英文。
    """
    header, rows = None, []
    raw = [l.strip() for l in (text or '').strip().splitlines() if l.strip()]
    pending: list[list[str]] = []
    for l in raw:
        if l.upper().startswith('[TABLE'):
            continue

        def _cells(s):
            # 先保护 \| 转义再按 | 切分，否则含转义竖线的单元格被错位拆开
            return [c.strip().replace('\x00', '|') for c in s.replace('\\|', '\x00').split('|')]

        m = re.match(r'^(?:Header|表头)\s*[::]\s*(.+)$', l, re.I)
        if m:
            header = _cells(m.group(1))
            continue
        m = re.match(r'^(?:Row|数据行|行)\s*[::]\s*(.+)$', l, re.I)
        if m:
            pending.append(_cells(m.group(1)))
            continue
        pending.append(_cells(l))

    def _cols_ok(cells):
        return len(cells) == n_cols

    if header is not None and _cols_ok(header):
        good_rows = [r for r in pending if _cols_ok(r)]
        return header, good_rows
    # 无有效 Header：行全部匹配也可用（表头回退原文）
    if pending and all(_cols_ok(r) for r in pending):
        return None, pending
    return None, None


def _rebuild_zh_table(header, rows) -> list[str]:
    """用译文单元格重建 Markdown 表格（含对齐行）"""
    n_cols = len(header) if header else (len(rows[0]) if rows else 0)
    lines = []
    if header:
        lines.append(_join_md_row(header))
        lines.append('|' + ' --- |' * n_cols)
    lines.extend(_join_md_row(r) for r in rows)
    return lines


def _translate_md_table(block_lines: list[str], unit: dict, zh_translation: str,
                        translate_type: str):
    """表格块写回：单元格替换为译文，重建保持 | 结构。

    返回输出行列表；译文不可用（解析失败/占位符缺失）时整表回退英文。
    bilingual 模式输出原表 + 中文表；chinese_only 模式仅输出中文表。
    """
    unit_table = unit.get('table') or {}
    unit_header = unit_table.get('header') or []
    unit_rows = unit_table.get('rows') or []
    n_cols = len(unit_header) if unit_header else (len(unit_rows[0]) if unit_rows else 0)
    if n_cols == 0:
        return None

    zh_header, zh_rows = None, None
    if zh_translation:
        if not _translation_valid(unit.get('text', ''), zh_translation):
            zh_header, zh_rows = None, None  # 占位符缺失 → 回退英文
        else:
            zh_header, zh_rows = _parse_table_translation(zh_translation, n_cols)

    # chinese_only：译文不可用 → None（调用方保留原表）；bilingual：至少输出原表
    if translate_type == 'chinese_only' and not zh_header and not zh_rows:
        return None

    out: list[str] = []
    if translate_type != 'chinese_only':
        out.extend(block_lines)  # 原表
        out.append('')
        if zh_header or zh_rows:
            out.extend(_rebuild_zh_table(zh_header, zh_rows))
        else:
            out.append(f'> 🇨🇳 （表格译文解析失败，保留原文）')
        return out

    # chinese_only：逐行替换单元格；行级不匹配保留该行原文
    header_done = not unit_header
    row_i = 0
    for line in block_lines:
        if MD_TABLE_SEP_RE.match(line):
            out.append(line)
            continue
        if zh_rows is None and zh_header is None:
            out.append(line)
            continue
        if not header_done and zh_header:
            out.append(_join_md_row(zh_header) if len(zh_header) == n_cols else line)
            header_done = True
            continue
        header_done = True
        cur = zh_rows[row_i] if zh_rows and row_i < len(zh_rows) else None
        row_i += 1
        out.append(_join_md_row(cur) if (cur and len(cur) == n_cols) else line)
    return out


def extract_markdown_paragraphs(file_path: str) -> dict:
    """从 Markdown 文件提取段落（含公式保护与表格翻译单元）"""
    with open(file_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # 公式保护：mistune 转换前在原文层替换 $...$ 等为 ⟦Mn⟧ 占位符
    md_content, formulas = protect_formulas(md_content)

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
    for elem in body.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'li', 'table']):
        if elem.name == 'table':
            # 整表作为一个翻译单元（表头+各行，保证列语义）
            if elem.find_parent('table'):
                continue  # 嵌套表防御：只取最外层
            unit = _extract_table_unit(elem)
            if unit:
                paragraphs.append({
                    'text': serialize_table_unit(unit),
                    'is_heading': False,
                    'tag': 'table',
                    'type': 'table',
                    'table': unit,
                })
            continue
        if elem.find_parent('table'):
            continue  # 表格内残存 p/li 不重复提取
        if elem.name != 'blockquote' and elem.find_parent('blockquote'):
            continue  # 引用块整体作为单元，内部 p/li 不重复提取（否则同文本双份入流）
        text = elem.get_text().strip()
        is_h = elem.name in HEADING_TAGS
        # 标题与正文统一按 >2 字符提取，避免短文本漏译（对齐 EPUB 处理）
        min_len = 2
        if len(text) > min_len and not elem.find_parent('pre'):
            paragraphs.append({
                'text': html_module.unescape(text),
                'is_heading': is_h,
                'tag': elem.name,
                'type': 'text',
            })

    return {
        'paragraphs': paragraphs,
        'original_md': md_content,
        'formulas': formulas,
    }


def build_bilingual_markdown(original_md: str, paragraphs: list[dict],
                              translations: list[str],
                              translate_type: str = "bilingual",
                              formulas: dict | None = None) -> str:
    """构建翻译后的 Markdown（按逻辑块匹配，鲁棒支持多行段落/列表/链接/表格）

    - bilingual：中英对照，中文以 `>` 引用块附在原文之后；表格附中文表副本
    - chinese_only：整块替换为中文（标题保留 # 前缀，列表项保留列表符号）
    - formulas：⟦Mn⟧ → 公式原文映射；译文缺失占位符时该段回退英文，
      输出前统一还原占位符为公式原文
    """
    lines = original_md.split('\n')
    result_lines = []
    para_idx = 0
    n = len(lines)
    i = 0

    def safe_translation(idx):
        """译文有效性检查：非空 + 公式占位符齐全（缺失→返回空，回退原文）"""
        if idx >= len(translations):
            return ''
        zh = translations[idx].strip()
        if not zh:
            return ''
        if not _translation_valid(paragraphs[idx]['text'], zh):
            return ''
        return zh

    def emit_translation(idx):
        if idx < len(paragraphs) and not paragraphs[idx]['is_heading']:
            zh = safe_translation(idx)
            if zh:
                result_lines.append('')
                # 多行译文（如含还原后的块级公式）逐行加引用前缀，保持 Markdown 结构
                result_lines.extend(f'> 🇨🇳 {ln}' if j == 0 else f'> {ln}'
                                    for j, ln in enumerate(zh.split('\n')))
                result_lines.append('')

    def emit_block(idx, block_lines):
        """输出一个已匹配的逻辑块"""
        if translate_type != 'chinese_only':
            result_lines.extend(block_lines)
            emit_translation(idx)
            return
        zh = safe_translation(idx)
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

        # 表格块：整表为一个翻译单元，单元格级替换（列数不匹配的行保留原文）
        if _is_table_block(block_lines):
            matched = False
            if para_idx < len(paragraphs) and paragraphs[para_idx].get('type') == 'table':
                zh = safe_translation(para_idx)
                rebuilt = _translate_md_table(block_lines, paragraphs[para_idx], zh,
                                              translate_type)
                if rebuilt is not None:
                    result_lines.extend(rebuilt)
                    matched = True
                para_idx += 1  # 无论译文可用与否，表格单元已消费，防止后续错位
            if not matched:
                result_lines.extend(block_lines)
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
                zh_item = safe_translation(para_idx)
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

    result = '\n'.join(result_lines)
    if formulas:
        result = restore_formulas(result, formulas)
    return result


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
    """折叠空白，用于段落比对（同时统一 mistune 对 \\$ 的 unescape 差异）"""
    return re.sub(r'\s+', ' ', text.replace('\\$', '$')).strip()


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

    # 导航条目（nav.xhtml / toc.ncx）：阅读器目录面板显示的标题，
    # 通用容器提取遗漏带链接条目，这里专门提取并追加到段落末尾参与翻译
    nav_entries = extract_nav_entries(epub_extract_path)
    nav_start = len(all_paragraphs)
    all_paragraphs.extend(e['text'] for e in nav_entries)
    if nav_entries:
        print(f'  导航条目待译: {len(nav_entries)} 条', flush=True)

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
            'nav_entries': nav_entries,
            'nav_start': nav_start,
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

    with open(workdir / 'original.md', 'w', encoding='utf-8') as f:
        f.write(data['original_md'])

    # 公式映射：⟦Mn⟧ → 公式原文（build 时还原）
    with open(workdir / 'formulas.json', 'w', encoding='utf-8') as f:
        json.dump({'formulas': data.get('formulas', {})}, f, ensure_ascii=False, indent=2)

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
        'formulas_protected': len(data.get('formulas', {})),
        'table_units': sum(1 for p in data['paragraphs'] if p.get('type') == 'table'),
        'workdir': str(workdir),
        'paragraphs_file': str(workdir / 'paragraphs.json'),
        'glossary_candidates': len(candidates),
        'next_step': 'Agent 先翻译术语表写入 glossary.json，再逐批翻译段落，结果写入 translations.json；'
                     '⟦Mn⟧ 公式占位符必须原样保留；[TABLE] 段落按 Header:/Row: 格式逐格翻译',
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

    # 导航条目译文写回（nav.xhtml / toc.ncx，阅读器目录面板标题）
    write_back_nav(meta, translations)

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

    # 公式映射（旧版 workdir 无此文件则视为无公式）
    formulas = {}
    formulas_file = workdir / 'formulas.json'
    if formulas_file.exists():
        with open(formulas_file, 'r', encoding='utf-8') as f:
            formulas = json.load(f).get('formulas', {})

    translate_type = meta.get('translate_type', 'bilingual')
    result_md = build_bilingual_markdown(original_md, paragraphs, translations,
                                         translate_type=translate_type,
                                         formulas=formulas)

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
