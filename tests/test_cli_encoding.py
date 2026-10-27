"""Real CLI pipe encoding regression; model execution is stubbed, not live QA."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class CliEncodingTests(unittest.TestCase):
    def test_cp950_pipe_reads_utf8_and_writes_utf8_json(self):
        code = (
            "import json,sys,luna; "
            "luna.router.main=lambda args: print(json.dumps("
            "{'worker_message':sys.stdin.read()},ensure_ascii=False)) or 0; "
            "luna.main(['run'])"
        )
        expected = '\u4e2d\u6587\U0001f600'
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONIOENCODING': 'cp950:strict'},
            input=expected.encode('utf-8'), capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8')), {'worker_message': expected})
