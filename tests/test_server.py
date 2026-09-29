import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


def sample(**changes):
    return dict(title='완주하지 못한 선수', url='https://school.programmers.co.kr/learn/courses/30/lessons/42576?language=java',
                description='참가자와 완주자의 목록에서 미완주자 한 명을 반환. 동명이인이 있을 수 있음.',
                code='class Solution { String solution(String[] a, String[] b) { return a[0]; } }',
                notes='중복 이름 처리에서 막힘', language='Java', level='Lv.1', date=date.today().isoformat(),
                minutes=30, hints=1, status='stuck', tags=['해시'], blockers=['경계 조건']) | changes


class CoachTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old_path = server.DB_PATH
        server.DB_PATH = Path(self.directory.name) / 'test.sqlite3'
        server.init_db()

    def tearDown(self):
        server.DB_PATH = self.old_path
        self.directory.cleanup()

    def test_persistence_and_java_unicode(self):
        a = server.save_attempt(sample())
        self.assertEqual(server.get_attempt(a['id'])['title'], '완주하지 못한 선수')
        self.assertEqual(server.get_attempt(a['id'])['language'], 'Java')
        self.assertEqual(server.summary(server.all_attempts())['total'], 1)

    def test_retries_do_not_inflate_first_attempt_metrics(self):
        server.save_attempt(sample())
        server.save_attempt(sample(status='solved', hints=0, minutes=5))
        s = server.summary(server.all_attempts())
        self.assertEqual((s['total'], s['problems'], s['independent_rate']), (2, 1, 0))
        self.assertEqual(s['weaknesses'][0]['count'], 1)
        self.assertEqual(s['cohorts'][0]['minutes'], 30)

    def test_programmers_tracking_and_language_query_normalized(self):
        a = server.validate(sample())
        b = server.validate(sample(url='https://school.programmers.co.kr/learn/courses/30/lessons/42576/?language=python3#x'))
        self.assertEqual(server.problem_key(a), server.problem_key(b))

    def test_review_schedule_respects_distinct_days_and_failure_reset(self):
        today = date.today()
        server.save_attempt(sample(date=(today-timedelta(days=8)).isoformat(), status='solved', hints=0))
        self.assertEqual(server.reviews(server.all_attempts())[0]['interval'], 3)
        server.save_attempt(sample(date=(today-timedelta(days=1)).isoformat(), status='solved', hints=0))
        self.assertEqual(server.reviews(server.all_attempts())[0]['interval'], 7)
        server.save_attempt(sample(date=(today-timedelta(days=1)).isoformat(), status='solved', hints=0))
        self.assertEqual(server.reviews(server.all_attempts())[0]['interval'], 7)
        server.save_attempt(sample(status='solved', hints=1))
        self.assertEqual(server.reviews(server.all_attempts())[0]['interval'], 1)

    def test_backdated_attempt_sorted_by_practice_date(self):
        server.save_attempt(sample(status='solved', hints=0))
        server.save_attempt(sample(date=(date.today()-timedelta(days=3)).isoformat()))
        self.assertEqual(server.summary(server.all_attempts())['independent_rate'], 0)
        self.assertEqual(server.reviews(server.all_attempts())[0]['interval'], 3)

    def test_validation_rejects_invalid_inputs(self):
        invalid = [dict(url='javascript:alert(1)'), dict(minutes=-1), dict(minutes=True), dict(hints=1.2),
                   dict(tags=[]), dict(tags=['unknown']), dict(code=' '), dict(language='invalid'),
                   dict(date=(date.today()+timedelta(days=1)).isoformat()), dict(status='wrong')]
        for delta in invalid:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                server.save_attempt(sample(**delta))
        self.assertEqual(server.all_attempts(), [])

    def test_backup_roundtrip_duplicate_and_atomic_validation(self):
        a = server.save_attempt(sample())
        backup = {'version': 1, 'attempts': server.all_attempts()}
        self.assertEqual(server.restore_backup(backup), 0)
        with server.connect() as con:
            con.execute('DELETE FROM attempts')
        invalid = copy.deepcopy(backup)
        invalid['attempts'].append(sample(id='invalid'))
        with self.assertRaises(ValueError):
            server.restore_backup(invalid)
        self.assertEqual(server.all_attempts(), [])
        self.assertEqual(server.restore_backup(backup), 1)
        self.assertEqual(server.get_attempt(a['id'])['code'], a['code'])

    def test_update_clears_stale_feedback_and_keeps_id(self):
        a = server.save_attempt(sample())
        with server.connect() as con:
            con.execute("UPDATE attempts SET feedback='old' WHERE id=?", (a['id'],))
        changed = server.update_attempt(sample(edit_id=a['id'], code='class Solution {}'))
        self.assertEqual(changed['feedback'], '')
        self.assertEqual(changed['code'], 'class Solution {}')
        self.assertEqual(len(server.all_attempts()), 1)

    def test_prompt_history_excludes_current_and_caps_at_twenty(self):
        for n in range(23):
            server.save_attempt(sample(title=str(n), url=''))
        a = server.all_attempts()[-1]
        prompt = json.loads(server.prompt_for(a))
        self.assertEqual(len(prompt['최근_풀이_기록']), 20)
        self.assertNotIn(a['title'], [h['title'] for h in prompt['최근_풀이_기록']])
        self.assertNotIn('code', prompt['최근_풀이_기록'][0])

    def test_ai_adapter_parses_all_output_messages_and_uses_server_key(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'status':'completed','output':[
                {'type':'reasoning'}, {'type':'message','content':[{'type':'output_text','text':'반례를 생각하세요.'}]},
                {'type':'message','content':[{'type':'output_text','text':'동명이인이 있나요?'}]}]}).encode()
        a = server.save_attempt(sample())
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test-only','OPENAI_MODEL':'test-model'}), patch.object(server,'urlopen',return_value=FakeResponse()) as mock:
            result = server.ai_feedback(a)
        self.assertIn('동명이인', result)
        request = mock.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only')
        self.assertFalse(json.loads(request.data)['store'])
        self.assertIn('정답 코드', json.loads(request.data)['instructions'])

    def test_ai_missing_key_and_incomplete_response(self):
        a = server.save_attempt(sample())
        with patch.dict(os.environ, {'OPENAI_API_KEY':''}), self.assertRaises(ValueError):
            server.ai_feedback(a)
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"status":"incomplete","output":[]}'
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'}), patch.object(server,'urlopen',return_value=FakeResponse()), self.assertRaises(ValueError):
            server.ai_feedback(a)

    def test_http_crud_and_cross_origin_protection(self):
        http = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread = threading.Thread(target=http.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        def req(path, data=None, origin=None):
            headers = {'Content-Type':'application/json'}
            if origin: headers['Origin'] = origin
            with urlopen(Request(base+path, None if data is None else json.dumps(data).encode(), headers)) as r:
                return json.load(r)
        try:
            a = req('/api/attempts',sample())
            self.assertEqual(req('/api/state')['summary']['total'], 1)
            req('/api/feedback',{'id':a['id'],'feedback':'중복 이름을 확인하세요.'})
            self.assertEqual(req('/api/export')['attempts'][0]['feedback_source'],'manual')
            with self.assertRaises(HTTPError) as exc:
                req('/api/delete',{'id':a['id']},'https://example.com')
            self.assertEqual(exc.exception.code,403)
            exc.exception.close()
            req('/api/delete',{'id':a['id']})
            self.assertEqual(req('/api/state')['summary']['total'],0)
            with self.assertRaises(HTTPError) as exc:
                req('/.env')
            self.assertEqual(exc.exception.code,404)
            exc.exception.close()
        finally:
            http.shutdown(); http.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
