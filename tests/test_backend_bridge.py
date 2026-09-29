import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('backend_bridge', Path(__file__).resolve().parents[1]/'scripts/ask_backend.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BackendBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)/'repo'
        self.repo.mkdir()
        for args in (['init', '-q'], ['config', 'user.name', 'Bridge Test'], ['config', 'user.email', 'test@example.invalid']):
            bridge.git(self.repo, *args)
        (self.repo/'docs/collaboration').mkdir(parents=True)
        (self.repo/'coach').mkdir()
        (self.repo/'client').mkdir()
        (self.repo/'COLLABORATION.md').write_text('Backend only. Preserve frontend work.')
        (self.repo/'coach/example.py').write_text('VALUE = 1\n')
        (self.repo/'client/example.js').write_text('const original = true;\n')
        bridge.git(self.repo, 'add', '.')
        bridge.git(self.repo, 'commit', '-qm', 'baseline')
        self.binary = Path(self.temp.name)/'fake-codex'
        self.counter = Path(self.temp.name)/'calls'
        self.binary.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
body = sys.stdin.read()
counter = Path('''+repr(str(self.counter))+''')
counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')
if 'TEST_FAIL' in body:
    raise SystemExit(7)
if 'TEST_EDIT' in body:
    Path('coach/example.py').write_text('VALUE = 2\\n')
    Path('coach/added.py').write_text('NEW = True\\n')
if 'TEST_FORBIDDEN' in body:
    Path('client/example.js').write_text('changed by backend')
Path(sys.argv[sys.argv.index('-o')+1]).write_text('응답 테스트 완료. 실제 모델 검사는 별도로 수행한다.')
''')
        self.binary.chmod(0o755)

    def request(self, text, mode='ask', retry=False):
        path = self.repo/'docs/collaboration/002-claude-request.md'
        path.write_text(text)
        return bridge.execute(self.repo, path, mode=mode, retry=retry, binary=str(self.binary))

    def test_question_and_duplicate_reuse(self):
        result = self.request('API를 설명해 주세요.')
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['patch'])
        self.assertTrue(Path(result['response']).is_file())
        again = self.request('API를 설명해 주세요.')
        self.assertTrue(again['cached'])
        self.assertEqual(self.counter.read_text(), '1')
        with self.assertRaises(ValueError):
            self.request('같은 번호로 요청 내용 변경')

    def test_runner_dependency_links_are_not_model_edits(self):
        for name in ('.venv', 'runtime'):
            (self.repo/name).mkdir()
        result = self.request('연결 확인')
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['patch'])

    def test_patch_isolated_from_uncommitted_frontend(self):
        (self.repo/'client/example.js').write_text('Claude is editing this.')
        result = self.request('TEST_EDIT', mode='edit')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual((self.repo/'coach/example.py').read_text(), 'VALUE = 1\n')
        self.assertEqual((self.repo/'client/example.js').read_text(), 'Claude is editing this.')
        self.assertEqual(result['files'], ['coach/added.py', 'coach/example.py'])
        bridge.git(self.repo, 'apply', '--check', result['patch'])
        bridge.git(self.repo, 'apply', result['patch'])
        self.assertEqual((self.repo/'coach/example.py').read_text(), 'VALUE = 2\n')
        self.assertTrue((self.repo/'coach/added.py').is_file())
        self.assertEqual(bridge.git(self.repo, 'worktree', 'list', '--porcelain').decode().count('worktree '), 1)

    def test_forbidden_frontend_changes_do_not_escape(self):
        result = self.request('TEST_FORBIDDEN', mode='edit')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('담당 범위', result['error'])
        self.assertEqual((self.repo/'client/example.js').read_text(), 'const original = true;\n')
        self.assertIsNone(result['patch'])

    def test_question_mode_rejects_edits(self):
        result = self.request('TEST_EDIT')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual((self.repo/'coach/example.py').read_text(), 'VALUE = 1\n')

    def test_failure_requires_explicit_retry(self):
        result = self.request('TEST_FAIL')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.request('TEST_FAIL')['status'], 'failed')
        self.assertEqual(self.counter.read_text(), '1')
        self.assertEqual(self.request('TEST_FAIL', retry=True)['status'], 'failed')
        self.assertEqual(self.counter.read_text(), '2')

    def test_request_must_stay_in_project_mailbox(self):
        outsider = Path(self.temp.name)/'bad-claude-request.md'
        outsider.write_text('test')
        with self.assertRaises(ValueError):
            bridge.execute(self.repo, outsider, binary=str(self.binary))


if __name__ == '__main__':
    unittest.main()
