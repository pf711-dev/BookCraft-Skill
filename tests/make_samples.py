#!/usr/bin/env python3
"""生成人工测试样例文件（PDF / EPUB），输出到 tests/samples/

用法：python3 tests/make_samples.py
产物：
  tests/samples/sample_formula_book.pdf   —— 两章 PDF（正文 + 公式行 + 表头 + 数据行）
  tests/samples/sample_book.epub          —— 含表格的最小 EPUB（回归测试用）
"""
from pathlib import Path

SAMPLES = Path(__file__).parent / 'samples'


def make_pdf(path: Path):
    import fitz
    doc = fitz.open()

    def add_page(title, lines):
        page = doc.new_page()
        y = 72

        def put(text, size=11):
            nonlocal y
            if text == '':
                y += 12
                return
            page.insert_text((72, y), text, fontsize=size)
            y += size * 1.8

        put(title, 16)
        for ln in lines:
            put(ln)

    add_page('Chapter 3: Statistical Methods', [
        'This chapter covers variance and regression analysis methods.',
        'The sample variance formula is widely used in practice.',
        'σ = √(Σ(xᵢ − μ)² / N)',          # 公式行 → verbatim（默认字体无字形时会提取为 · 乱码，同样 verbatim）
        '',
        'Name        Revenue    Growth',   # 表头 → 进翻译流
        'Acme Corp   $1,200     +15%',     # 数据行 → verbatim
        'Beta LLC    $850       +8%',
        'Gamma Inc   $2,300     +22%',
        '',
        'The results demonstrate consistent growth across all segments.',
    ])
    add_page('Chapter 4: Forecasting', [
        'Forecasting combines historical data with statistical models.',
        'The expected value calculation follows.',
        'E[X] = Σ pᵢxᵢ over all outcomes',
        '',
        'Model        MAE      RMSE',
        'Linear       12.4     18.7',
        'Ensemble     9.1      14.2',
        '',
        'Future work will explore deep learning approaches.',
    ])
    doc.save(str(path))
    doc.close()
    print(f'✓ {path}')


def make_epub(path: Path):
    import zipfile
    container = '''<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
    opf = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">sample-book</dc:identifier><dc:title>Sample Book</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2024-01-01T00:00:00Z</meta></metadata>
<manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest>
<spine><itemref idref="c1"/></spine></package>'''
    chapter = '''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter 1</title></head>
<body><h1>The Data Table Chapter</h1>
<p>This chapter demonstrates table rendering in EPUB format.</p>
<table><thead><tr><th>Region</th><th>Sales</th><th>Change</th></tr></thead>
<tbody><tr><td>North</td><td>$1.2M</td><td>+15%</td></tr>
<tr><td>South</td><td>$850K</td><td>+8%</td></tr></tbody></table>
<p>Tables in EPUB are translated cell by cell with DOM anchors.</p>
<h2>Section Two</h2>
<p>Media tags like images keep formulas safe as pictures.</p></body></html>'''
    nav = '''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Nav</title></head>
<body><nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Chapter 1</a></li></ol></nav></body></html>'''
    with zipfile.ZipFile(str(path), 'w') as z:
        z.writestr('META-INF/container.xml', container)
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/chapter1.xhtml', chapter)
        z.writestr('OEBPS/nav.xhtml', nav)
    print(f'✓ {path}')


if __name__ == '__main__':
    SAMPLES.mkdir(exist_ok=True)
    make_pdf(SAMPLES / 'sample_formula_book.pdf')
    make_epub(SAMPLES / 'sample_book.epub')
