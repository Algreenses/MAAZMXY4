"""The native JSON fast path keeps the JSONC fallback and fresh-read semantics."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from task_catalog import read_document


class TaskDocumentParserTests(unittest.TestCase):
    def test_json_and_jsonc_preserve_urls_escapes_and_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'resource.jsonc'
            value={'url':'https://example.test/a//b', 'literal':'/* kept */', 'quote':'a"b\\c'}
            for content in [json.dumps(value), '/* header */\n'+json.dumps(value)+' // tail']:
                path.write_text(content,encoding='utf8')
                self.assertEqual(read_document(path),value)
            value['url']='updated'
            path.write_text(json.dumps(value),encoding='utf8')
            self.assertEqual(read_document(path),value)

    def test_invalid_documents_remain_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'resource.json'
            for content in ['[]','null','123','{bad}', '{"x":1} /* unterminated']:
                with self.subTest(content=content):
                    path.write_text(content,encoding='utf8')
                    with self.assertRaises(ValueError):read_document(path)


if __name__=='__main__':unittest.main()
