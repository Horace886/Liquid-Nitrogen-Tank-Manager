import ast
import copy
import json
from pathlib import Path
import string
from tempfile import TemporaryDirectory
import unittest

from liquid_nitrogen_tank_i18n import ENGLISH, LanguageState, error_message, join_text, msg, render


class LanguageTests(unittest.TestCase):
    def test_data_is_never_translated(self):
        value = msg('细胞位置 · {0}', '入库')
        self.assertEqual(render('入库', 'en'), '入库')
        self.assertIn('入库', render(value, 'en'))
        self.assertEqual(str(value), '细胞位置 · 入库')
        self.assertEqual(json.loads(json.dumps({'name': value})), {'name': str(value)})
        self.assertEqual(render(copy.deepcopy(value), 'en'), render(value, 'en'))
        self.assertEqual(render(join_text(' / ', [msg('入库'), msg('出库')]), 'en'), 'Stock In / Stock Out')

    def test_catalog_preserves_placeholders(self):
        formatter = string.Formatter()
        for source, english in ENGLISH.items():
            fields = lambda value: sorted(field for _, field, _, _ in formatter.parse(value) if field is not None)
            self.assertEqual(fields(source), fields(english), source)

    def test_all_marked_ui_literals_have_translations(self):
        source = Path(__file__).resolve().parents[1] / 'liquid_nitrogen_tank_manager.py'
        for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'msg' and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and any('\u4e00' <= c <= '\u9fff' for c in arg.value):
                    self.assertIn(arg.value, ENGLISH, f'line {node.lineno}')

    def test_errors_keep_dynamic_user_names(self):
        self.assertEqual(render(error_message('入库人不能为空。'), 'en'), 'Stock-in operator is required.')
        self.assertIn('入库', render(error_message('入库 的 A1 未填写入库人。'), 'en'))
        self.assertEqual(render(error_message('unrecognized system error'), 'en'), 'unrecognized system error')

    def test_language_settings_are_separate_and_persistent(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            settings = LanguageState(path)
            self.assertEqual(settings.language, 'zh')
            settings.set_language('en')
            self.assertEqual(LanguageState(path).language, 'en')
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(LanguageState(path).language, 'zh')
            path.write_text('{"language":"unknown"}', encoding='utf-8')
            self.assertEqual(LanguageState(path).language, 'zh')
            with self.assertRaises(ValueError):
                settings.set_language('unknown')


if __name__ == '__main__':
    unittest.main()
