import tempfile
import io
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from coach import store as st
from coach.service import Lab
from coach.timing import valid_offset
from coach.http import post


class EventTimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = st.DATA
        st.DATA = Path(self.temp.name)
        self.lab = Lab()
        with patch.object(self.lab.ide, 'prepare', return_value={'ready': True}):
            self.session = self.lab.new_session(dict(problem_ids=['pair'], mode='training', kind='single', duration=40, resources='none', model='', effort=''))
        self.sid = self.session['id']

    def tearDown(self):
        self.lab.close()
        st.DATA = self.old
        self.temp.cleanup()

    def aged(self, hours=40):
        started = st.now() - hours * 3600000
        st.mutate(self.sid, lambda s: s.update(started=started, deadline=started + 2400000, deadline_codes=dict(s['codes'])))
        return st.session(self.sid)

    def event(self, ms, code='// recovery fixture', eid='queued'):
        return dict(id=eid, ms=ms, seq=100, kind='code', pid='pair', payload={'code': code})

    def test_next_day_training_preserves_timestamp_and_recovers_code_before_run(self):
        s = self.aged(); ms = st.now() - s['started']
        event = self.event(ms)
        self.lab.record(self.sid, [event]); self.lab.record(self.sid, [event])
        saved = st.events(self.sid)
        self.assertEqual([(e['ms'], e['payload']['code']) for e in saved if e['id'] == 'queued'], [(ms, '// recovery fixture')])
        self.assertEqual(st.session(self.sid)['codes']['pair'], '// recovery fixture')
        self.lab.job = lambda sid, kind, fn: fn(lambda _: None, threading.Event())
        result = {'status': 'passed', 'passed': 1, 'total': 1, 'output': '', 'error': None}
        with patch('coach.service.run_java', return_value=result) as grader:
            run = self.lab.run(self.sid, 'pair', False)
        self.assertEqual(grader.call_args.args[0], '// recovery fixture')
        self.assertEqual(run['result']['status'], 'passed')
        self.assertGreaterEqual(run['ms'], ms)

    def test_long_offset_media_and_late_upload_are_preserved(self):
        s = self.aged(43); ms = st.now() - s['started']; segment = 'c' * 32
        self.lab.upload(self.sid, 'screen', segment, 0, ms, 'video/webm', b'first')
        self.lab.finish(self.sid)
        self.lab.upload(self.sid, 'screen', segment, 1, ms, 'video/webm', b'tail')
        group = self.lab.media_groups(self.sid)[0]
        self.assertEqual((group['start_ms'], group['chunks'], group['bytes']), (ms, 2, 9))

    def test_delayed_code_after_finish_remains_history(self):
        s = self.aged(); original = s['codes']['pair']; ms = st.now() - s['started']
        self.lab.finish(self.sid); self.lab.record(self.sid, [self.event(ms)])
        self.assertEqual(st.session(self.sid)['codes']['pair'], original)
        event = next(e for e in st.events(self.sid) if e['id'] == 'queued')
        self.assertEqual(event['ms'], ms)
        self.assertTrue(event['payload']['recovered_after_finish'])

    def test_negative_bool_float_epoch_and_far_future_are_rejected(self):
        for value in (-1, True, 1.5, st.now(), 86400001):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '이벤트 시각 오류'):
                self.lab.record(self.sid, [self.event(value)])
        self.aged()
        with self.assertRaisesRegex(ValueError, '이벤트 시각 오류'):
            self.lab.record(self.sid, [self.event(st.now())])

    def test_clock_tolerance_and_completed_session_bounds(self):
        now = 1790000000000
        s = dict(started=now - 40 * 3600000, ended=None)
        self.assertTrue(valid_offset(s, 40 * 3600000, now))
        self.assertTrue(valid_offset(s, 40 * 3600000 + 300000, now))
        self.assertFalse(valid_offset(s, 40 * 3600000 + 300001, now))
        s['ended'] = now + 60000
        self.assertTrue(valid_offset(s, 40 * 3600000 + 60000, now))

    def test_transcript_http_accepts_long_session_offset_and_rejects_epoch(self):
        s = self.aged(); ms = st.now() - s['started']; self.lab.finish(self.sid)
        class Handler:
            def __init__(self, segments):
                raw = json.dumps({'sid': s['id'], 'segments': segments}).encode()
                self.headers = {'Content-Length': str(len(raw)), 'Content-Type': 'application/json'}
                self.rfile = io.BytesIO(raw)
            def send(self, status, body): self.result = status, body
        segments = [{'start_ms': ms - 1000, 'end_ms': ms, 'text': 'fixture'}]
        h = Handler(segments); post(h, self.lab, '/api/v2/transcript')
        self.assertEqual(h.result[0], 200)
        self.assertEqual(st.session(self.sid)['transcripts'], segments)
        h = Handler([{'start_ms': st.now(), 'end_ms': st.now(), 'text': 'invalid epoch'}])
        post(h, self.lab, '/api/v2/transcript'); self.assertEqual(h.result[0], 400)
        self.assertEqual(st.session(self.sid)['transcripts'], segments)


if __name__ == '__main__': unittest.main()
