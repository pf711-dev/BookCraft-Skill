#!/usr/bin/env python3
"""PDF 结构层回归测试：公式 verbatim + 数据表 verbatim + 表头进翻译流

动态生成小 PDF（避免二进制样例入库），跑 extract/build 全链路秒级断言。
运行：python3 tests/test_pdf.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

from pdf_extract import is_verbatim, math_symbol_count


@unittest.skipUnless(HAS_FITZ, "需要 pymupdf")
class VerbatimTests(unittest.TestCase):
    """is_verbatim 判定：公式行 / 数据行 / 乱码兜底 / 正文不误伤"""

    def test_math_symbol_line(self):
        self.assertTrue(is_verbatim('σ = √(Σ(x − μ)² / N)'))
        self.assertTrue(is_verbatim('y = ∑xᵢ + ∫f(x)dx'))
        self.assertTrue(is_verbatim('α + β = γ'))

    def test_math_symbol_density_threshold(self):
        # 正文偶现 1-2 个符号不误伤
        self.assertFalse(is_verbatim('The threshold is ≤ 5 days for approval.'))
        self.assertFalse(is_verbatim('Revenue grew ≥ 10% (± 1%) year on year.'))

    def test_mojibake_fallback(self):
        # 字体映射乱码：公式被提取成密集 · 行
        self.assertTrue(is_verbatim('· = ·(·(x· · ·)² / N)'))
        self.assertTrue(is_verbatim('E[X] = · p·x· where i'))

    def test_data_row_3_tokens(self):
        self.assertTrue(is_verbatim('Acme Corp $1,200 +15%'))
        self.assertTrue(is_verbatim('Linear 12.4 18.7'))

    def test_header_row_not_verbatim(self):
        # 表头行文字为主 → 必须进翻译流
        self.assertFalse(is_verbatim('Name Revenue Growth'))
        self.assertFalse(is_verbatim('Model MAE RMSE'))

    def test_normal_sentence(self):
        self.assertFalse(is_verbatim('Revenue grew by 45% in Q2 compared to last year.'))

    def test_phone_line(self):
        self.assertTrue(is_verbatim('Tel: +1 (555) 123-4567'))

    def test_math_symbol_count(self):
        self.assertEqual(math_symbol_count('no math here'), 0)
        self.assertGreaterEqual(math_symbol_count('αβγ'), 3)


@unittest.skipUnless(HAS_FITZ, "需要 pymupdf")
class PdfPipelineTests(unittest.TestCase):
    """端到端：生成两章 PDF → extract → 模拟翻译 → build"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='bc_pdf_test_')
        cls.pdf_path = str(Path(cls.tmp) / 'book.pdf')
        cls.workdir = Path(cls.tmp) / 'work'
        _make_sample_pdf(cls.pdf_path)
        from pdf_extract import extract_pdf
        cls.meta = extract_pdf(
            input_path=cls.pdf_path, workdir=str(cls.workdir),
            chapters_mode='pattern', chapter_pattern=r'^Chapter \d+',
            translate_type='chinese_only')

    def _chapter(self, cid):
        return json.load(open(self.workdir / 'chapters' / f'{cid}.json', encoding='utf-8'))

    def test_chapter_count(self):
        self.assertEqual(len(self.meta['chapters']), 2)

    def test_formula_lines_verbatim(self):
        ch = self._chapter('chapter_01')
        vtexts = [b['text'] for b in ch['blocks'] if b.get('verbatim')]
        self.assertTrue(any('·' in t or 'σ' in t or '√' in t for t in vtexts),
                        f'公式行应 verbatim，实际: {vtexts}')

    def test_data_rows_verbatim_and_grouped(self):
        ch = self._chapter('chapter_01')
        vtexts = [b['text'] for b in ch['blocks'] if b.get('verbatim')]
        self.assertTrue(any('Acme Corp' in t for t in vtexts))
        self.assertTrue(any('Beta LLC' in t for t in vtexts))

    def test_header_row_in_translation_stream(self):
        """表头行不被数据行吞掉：必须出现在 paragraphs.json（翻译流）"""
        paras = json.load(open(self.workdir / 'paragraphs.json', encoding='utf-8'))['paragraphs']
        self.assertTrue(any('Name Revenue Growth' in p for p in paras))
        self.assertTrue(any('Model MAE RMSE' in p for p in paras))
        # 数据行绝不能出现在翻译流
        self.assertFalse(any('Acme Corp' in p for p in paras))

    def test_verbatim_count_meta(self):
        self.assertGreaterEqual(self.meta['verbatim_paragraphs'], 4)

    def test_build_mapping_rate(self):
        """模拟全量译文 → build → 译文映射率 100%"""
        paras = json.load(open(self.workdir / 'paragraphs.json', encoding='utf-8'))['paragraphs']
        translations = [f'译文{i}：统计与预测内容' for i in range(len(paras))]
        json.dump({'translations': translations},
                  open(self.workdir / 'translations.json', 'w', encoding='utf-8'))
        try:
            from pdf_build import build_pdf
            meta = dict(self.meta)
            meta['output'] = str(Path(self.tmp) / 'out.epub')
            build_pdf(meta, translations, str(self.workdir))
        except ImportError:
            self.skipTest('需要 ebooklib')
        # Markdown 备份可读断言：表头有译文，verbatim 原文保留且连续行聚合
        md = open(Path(self.tmp) / 'out.md', encoding='utf-8').read()
        self.assertIn('```\n', md)                      # verbatim 块存在
        self.assertIn('译文', md)                        # 译文映射成功
        self.assertNotIn('Name Revenue Growth', md)     # 表头已替换为译文
        self.assertIn('Acme Corp $1,200 +15%\nBeta LLC $850 +8%', md)  # 连续数据行聚合一个块
        # 译文映射率 100%
        self.assertEqual(self.meta['total_paragraphs'], 10)


def _make_sample_pdf(path):
    """两章结构化 PDF：正文 + 公式行 + 表头 + 数据行"""
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
        'σ = √(Σ(xᵢ − μ)² / N)',
        '',
        'Name        Revenue    Growth',
        'Acme Corp   $1,200     +15%',
        'Beta LLC    $850       +8%',
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
    doc.save(path)
    doc.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
