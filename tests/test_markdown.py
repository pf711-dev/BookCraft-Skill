#!/usr/bin/env python3
"""Markdown 结构层回归测试：公式保护 + 表格翻译单元 + 写回组装

纯 Python 断言（不涉及 LLM 翻译层），秒级完成。
运行：python3 tests/test_markdown.py
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from translate import (
    protect_formulas, restore_formulas, _translation_valid,
    _is_table_block, _split_md_row, _join_md_row,
    _extract_table_unit, serialize_table_unit, _parse_table_translation,
    _rebuild_zh_table, _translate_md_table,
    extract_markdown_paragraphs, build_bilingual_markdown,
)

SAMPLE = Path(__file__).parent / 'samples' / 'sample_table_formula.md'


class FormulaProtectTests(unittest.TestCase):
    """公式占位符保护：识别 / 不误伤 / 还原 / 译文校验"""

    def test_inline_math(self):
        p, f = protect_formulas('Euler said $E = mc^2$ in 1905.')
        self.assertEqual(len(f), 1)
        self.assertIn('⟦M1⟧', p)
        self.assertEqual(f[1], '$E = mc^2$')

    def test_display_math_multiline(self):
        p, f = protect_formulas('$$\n\\sigma = \\sum x_i\n$$')
        self.assertEqual(len(f), 1)
        self.assertEqual(p.strip(), '⟦M1⟧')

    def test_latex_delimiters(self):
        p, f = protect_formulas(r'Inline \(a + b\) and \[c^2\] here')
        self.assertEqual(len(f), 2)

    def test_currency_not_matched(self):
        p, f = protect_formulas('Costs were $100 and $200 total.')
        self.assertEqual(len(f), 0)
        p, f = protect_formulas('The fee is $50 for members.')
        self.assertEqual(len(f), 0)

    def test_escaped_dollar_not_matched(self):
        p, f = protect_formulas('price is \\$5 today')
        self.assertEqual(len(f), 0)

    def test_code_fence_protected(self):
        md = '```bash\necho "costs $100 and $HOME"\n```'
        p, f = protect_formulas(md)
        self.assertEqual(len(f), 0)
        self.assertEqual(p, md)  # 代码块原样保留

    def test_inline_code_protected(self):
        p, f = protect_formulas('use `printf "%s" $x` command')
        self.assertEqual(len(f), 0)

    def test_restore_strict(self):
        p, f = protect_formulas('value of $x^2 + y^2$ matters')
        out = restore_formulas('⟦M1⟧ 的值很重要', f)
        self.assertEqual(out, '$x^2 + y^2$ 的值很重要')

    def test_restore_tolerant_variants(self):
        _, f = protect_formulas('A $a+b$ B')
        self.assertEqual(restore_formulas('【 m 1 】结果', f), '$a+b$结果')
        self.assertEqual(restore_formulas('结果 [M1] 完成', f), '结果 $a+b$ 完成')
        self.assertEqual(restore_formulas('⟦ M 1 ⟧好了', f), '$a+b$好了')

    def test_restore_in_quote_prefix(self):
        """引用行内的块级公式还原后续行补 > 前缀"""
        _, f = protect_formulas('$$\nx = 1\n$$')
        out = restore_formulas('> 🇨🇳 ⟦M1⟧', f)
        self.assertEqual(out, '> 🇨🇳 $$\n> x = 1\n> $$')

    def test_translation_valid(self):
        src, _ = protect_formulas('A $a+b$ and $c-d$ B')
        self.assertTrue(_translation_valid(src, '⟦M1⟧ 和 ⟦M2⟧'))
        self.assertTrue(_translation_valid(src, '【m1】和【 M 2 】'))  # 宽容形态
        self.assertFalse(_translation_valid(src, '只有 ⟦M1⟧'))       # 缺失 → 无效
        self.assertTrue(_translation_valid('无公式段落', '任意译文'))


class TableUnitTests(unittest.TestCase):
    """表格翻译单元：识别 / 提取 / 序列化 / 译文解析"""

    def test_is_table_block(self):
        self.assertTrue(_is_table_block(['| A | B |', '|---|---|', '| 1 | 2 |']))
        self.assertFalse(_is_table_block(['just | pipes', 'no sep line']))
        self.assertFalse(_is_table_block(['single line | table']))

    def test_split_join_row_escape(self):
        self.assertEqual(_split_md_row('| a | b |'), ['a', 'b'])
        self.assertEqual(_split_md_row('| x \\| y | z |'), ['x | y', 'z'])
        self.assertEqual(_join_md_row(['a', 'b|c']), '| a | b\\|c |')

    def test_extract_table_unit(self):
        from bs4 import BeautifulSoup
        html = ('<table><thead><tr><th>Metric</th><th>Q1</th></tr></thead>'
                '<tbody><tr><td>Revenue</td><td>$1.2M</td></tr></tbody></table>')
        soup = BeautifulSoup(html, 'lxml')
        unit = _extract_table_unit(soup.find('table'))
        self.assertEqual(unit['header'], ['Metric', 'Q1'])
        self.assertEqual(unit['rows'], [['Revenue', '$1.2M']])

    def test_pure_numeric_table_skipped(self):
        from bs4 import BeautifulSoup
        html = ('<table><thead><tr><th>1</th><th>2</th></tr></thead>'
                '<tbody><tr><td>3</td><td>4</td></tr></tbody></table>')
        soup = BeautifulSoup(html, 'lxml')
        self.assertIsNone(_extract_table_unit(soup.find('table')))

    def test_serialize_unit(self):
        unit = {'header': ['A', 'B|C'], 'rows': [['x', 'y']]}
        s = serialize_table_unit(unit)
        self.assertIn('[TABLE]', s)
        self.assertIn('Header: A | B\\|C', s)
        self.assertIn('Row: x | y', s)

    def test_parse_translation_standard(self):
        zh = 'Header: 指标 | 季度\nRow: 营收 | Q1'
        header, rows = _parse_table_translation(zh, 2)
        self.assertEqual(header, ['指标', '季度'])
        self.assertEqual(rows, [['营收', 'Q1']])

    def test_parse_translation_tolerant(self):
        # Agent 丢失 Header:/Row: 前缀 → 纯 | 行按序解析
        zh = '指标 | 季度\n营收 | Q1'
        header, rows = _parse_table_translation(zh, 2)
        self.assertIsNone(header)  # 无法定位表头 → 表头回退原文
        self.assertEqual(rows, [['指标', '季度'], ['营收', 'Q1']])

    def test_parse_translation_col_mismatch(self):
        zh = 'Header: 只有 | 两 | 列但需要三\nRow: a | b'
        header, rows = _parse_table_translation(zh, 3)
        # Header 3 列匹配成功即可用，不匹配的行被丢弃
        self.assertEqual(len(header), 3)
        self.assertEqual(rows, [])


class BuildFlowTests(unittest.TestCase):
    """端到端：extract → build（模拟译文），断言结构完整性"""

    @classmethod
    def setUpClass(cls):
        cls.data = extract_markdown_paragraphs(str(SAMPLE))
        cls.tmp = tempfile.mkdtemp(prefix='bc_test_')

    def _translations(self):
        """样例的模拟译文（占位符序号按实际公式映射：M1=paren a2+b2、M2=display σ、M3=$R$ 复利、M4=$α+β$、M5=$γ$）"""
        return [
            '年度数据报告',
            '2024 年营收增长。复利公式 ⟦M3⟧ 驱动预测。',
            '展示方差：',
            '⟦M2⟧',
            'LaTeX 定界符也可以：⟦M1⟧ 行内使用。',
            '会员费是 $50（转义美元，非公式）。',
            '差旅成本 $100 and $200 分布在两个城市。',
            '[TABLE]\nHeader: 指标 | 第一季度 | 第二季度 | 备注\n'
            'Row: 营收 | $1.2M | $1.5M | 历史新高\n'
            'Row: 用户 | 10,000 | 12,500 | +25%\n'
            'Row: 流失 \\| 转义 | 2.1% | 1.8% | 改善',
            '区域细分',
            '北区含 ⟦M4⟧ 调整',
            '南区纯文本',
            '西区',
            '引用块含行内公式 ⟦M5⟧。',
            '最终段落提到 E = mc^2 无定界符。',
        ]

    def test_extract_structure(self):
        types = [p['type'] for p in self.data['paragraphs']]
        self.assertEqual(types.count('table'), 1)          # 1 个表格单元
        self.assertGreaterEqual(len(self.data['formulas']), 5)  # >=5 个公式
        texts = [p['text'] for p in self.data['paragraphs']]
        # 货币/转义美元不进公式映射（体现在段落文本里保持原样）
        self.assertTrue(any('$50' in t and '⟦' not in t for t in texts))
        self.assertTrue(any('$100 and $200' in t for t in texts))
        # 表格单元序列化包含转义竖线单元格
        tbl = next(p for p in self.data['paragraphs'] if p['type'] == 'table')
        self.assertIn('Churn \\| escaped', tbl['text'])
        # 引用块只提取一次（blockquote 不与内部 p 重复入流）
        texts_all = [pp['text'] for pp in self.data['paragraphs']]
        self.assertEqual(sum(1 for t in texts_all if 'Quote block' in t), 1)

    def test_build_bilingual(self):
        result = build_bilingual_markdown(
            self.data['original_md'], self.data['paragraphs'],
            self._translations(), translate_type='bilingual',
            formulas=self.data['formulas'])
        # 占位符全部还原（不允许残留）
        self.assertNotIn('⟦', result)
        self.assertNotIn('【M', result)
        self.assertIn('$R = P \\times (1 + r)^n$', result)          # 行内公式
        self.assertIn('\\sigma = \\sqrt', result)                    # 块级公式
        self.assertIn('\\alpha + \\beta$', result)                   # 列表项公式
        self.assertIn('a^2 + b^2 = c^2', result)                     # LaTeX 定界公式
        self.assertIn('\\gamma = \\delta / 2$', result)              # 引用块公式
        # 引用块内块级公式每行带 > 前缀
        self.assertIn('> $$', result)
        # 中文表格副本（bilingual 双表）
        self.assertIn('| 指标 | 第一季度 | 第二季度 | 备注 |', result)
        self.assertIn('| 营收 | $1.2M | $1.5M | 历史新高 |', result)
        self.assertIn('| 流失 \\| 转义 | 2.1% | 1.8% | 改善 |', result)
        # 原英文表格保留
        self.assertIn('| Metric | Q1 | Q2 | Notes |', result)
        # 代码块 $ 不被误还原/误翻译
        self.assertIn('echo "costs $100 and $HOME"', result)
        # 转义美元段落有译文；原表+中文表各一个对齐行；引用块译文无重复
        self.assertIn('会员费是 $50', result)
        # 中文表对齐行（重建格式）恰好一个；原表对齐行仍保留
        self.assertEqual(result.count('\n| --- | --- | --- | --- |\n'), 1)
        self.assertIn('|--------|----|----|-------|', result)
        self.assertEqual(result.count('> 🇨🇳 引用块含行内公式'), 1)

    def test_build_chinese_only(self):
        result = build_bilingual_markdown(
            self.data['original_md'], self.data['paragraphs'],
            self._translations(), translate_type='chinese_only',
            formulas=self.data['formulas'])
        self.assertIn('# 年度数据报告', result)                    # 标题替换
        self.assertNotIn('| Metric |', result)                    # 原表被替换
        self.assertIn('| 指标 | 第一季度 | 第二季度 | 备注 |', result)
        self.assertNotIn('⟦', result)
        self.assertIn('echo "costs $100 and $HOME"', result)      # 代码块保留

    def test_placeholder_loss_falls_back(self):
        """译文丢失占位符 → 该段回退英文，公式绝不因翻译而丢失"""
        bad = self._translations()
        bad[1] = '复利公式被模型吞掉了'  # 丢失 ⟦M1⟧
        result = build_bilingual_markdown(
            self.data['original_md'], self.data['paragraphs'],
            bad, translate_type='bilingual', formulas=self.data['formulas'])
        self.assertIn('Revenue grew in 2024. The compound formula $R = P \\times (1 + r)^n$ drives', result)  # 原文保留
        self.assertNotIn('复利公式被模型吞掉了', result)                          # 坏译文不采用
        # 其余段落不受影响（标题段 bilingual 不附译文为既有行为，用正文段验证）
        self.assertIn('展示方差', result)
        self.assertIn('| 指标 | 第一季度 | 第二季度 | 备注 |', result)

    def test_table_translation_parse_failure_keeps_original(self):
        """表格译文列数全错 → 整表回退英文"""
        bad = self._translations()
        bad[7] = '[TABLE]\nHeader: 一 | 二\nRow: 错 | 位'
        result = build_bilingual_markdown(
            self.data['original_md'], self.data['paragraphs'],
            bad, translate_type='bilingual', formulas=self.data['formulas'])
        self.assertIn('| Metric | Q1 | Q2 | Notes |', result)
        self.assertNotIn('| 指标 |', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
