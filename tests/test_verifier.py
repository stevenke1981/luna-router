import json
from pathlib import Path
import sys
import tempfile
import unittest
from verifier import read_check, run_check


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / 'verifier.json'

    def check(self, argv):
        self.file.write_text(json.dumps({'argv': argv, 'timeout_seconds': 10}))
        return read_check(self.file)

    def test_pass_is_an_executable_test_result(self):
        r=run_check(self.check([sys.executable, '-c', 'print("success")']), self.root)
        self.assertEqual(r['outcome'], 'passed')
        self.assertEqual(r['exit_code'], 0)
        self.assertNotIn('success', str(r))

    def test_failing_test_is_failure_proof(self):
        r=run_check(self.check([sys.executable, '-c', 'raise SystemExit(1)']), self.root)
        self.assertEqual((r['outcome'], r['exit_code']), ('failed', 1))

    def test_absent_checker_is_environment_block(self):
        r=run_check(self.check(['__executable_does_not_exist_112233']), self.root)
        self.assertEqual(r['outcome'], 'blocked')

    def test_timeout_is_unknown(self):
        self.file.write_text(json.dumps({'argv': [sys.executable, '-c', 'import time;time.sleep(8)'], 'timeout_seconds': 1}))
        self.assertEqual(run_check(read_check(self.file), self.root)['outcome'], 'unknown')

    def test_shell_not_allowed_and_strict_contract(self):
        for obj in ({'argv': 'echo x'}, {'argv': ['echo', 'x'], 'evil':'yes'},
                    {'argv': []}, {'argv': ['echo', '\x00']}, {'argv': ['echo'], 'timeout_seconds': 0}):
            self.file.write_text(json.dumps(obj))
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                read_check(self.file)


if __name__ == '__main__':
    unittest.main()
