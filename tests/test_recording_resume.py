import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from coach import store as st
from coach.recordings import groups, gaps
from coach.service import Lab


class RecordingResumeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.old = st.DATA; st.DATA = Path(self.temp.name)
        self.lab = Lab()
        with patch.object(self.lab.ide, 'prepare'):
            self.s = self.lab.new_session(dict(problem_ids=['pair'], kind='single', mode='training', duration=30, resources='none'))
        self.sid = self.s['id']; self.a = 'a' * 32; self.b = 'b' * 32
        st.mutate(self.sid, lambda s: s.update(started=st.now()-60000, deadline=st.now()+1800000))

    def tearDown(self):
        self.lab.close(); st.DATA = self.old; self.temp.cleanup()

    def mark(self, kind, segment=None, ms=0, **payload):
        if segment: payload.update(segment=segment, kind='screen')
        st.event(self.sid, kind, payload, ms=ms)

    def segment(self, segment, start, end, complete=True):
        self.mark('recording_start', segment, start, offset_ms=start)
        self.lab.upload(self.sid, 'screen', segment, 0, start, 'video/webm', segment.encode(), end=end)
        if complete: self.mark('recording_stop', segment, end, chunks=1, end_ms=end)

    def finish(self, end=20000):
        st.mutate(self.sid, lambda s: s.update(status='completed', ended=s['started']+end))

    def transcribe(self, side_effect):
        with patch.object(self.lab, 'media_file', side_effect=lambda sid, kind, segment: (Path(segment), 'video/webm')), patch.object(self.lab.speech, 'transcribe', side_effect=side_effect) as speech:
            result = self.lab.prepare_transcripts(self.sid, lambda _: None, threading.Event())
        return result, speech

    def test_refresh_marks_old_segment_uncertain_before_new_capture(self):
        self.segment(self.a, 1000, 3000, complete=False)
        self.assertEqual(self.lab.media_groups(self.sid)[0]['status'], 'recording')
        self.mark('recording_gap', ms=5000, reason='reentry')
        self.assertEqual(self.lab.media_groups(self.sid)[0]['status'], 'unconfirmed')
        self.segment(self.b, 8000, 10000)
        self.finish(12000)
        view = self.lab.view(self.sid)
        self.assertEqual([g['start_ms'] for g in view['media']], [1000, 8000])
        self.assertEqual(view['recording_gaps'], [dict(start_ms=0,end_ms=1000,kind='not_recorded'),dict(start_ms=3000,end_ms=8000,kind='unconfirmed'),dict(start_ms=10000,end_ms=12000,kind='not_recorded')])

    def test_missing_tail_and_late_upload_preserve_other_segments_and_raw_bytes(self):
        self.segment(self.a, 0, 2000, complete=False)
        self.mark('recording_stop', self.a, 4000, chunks=2, end_ms=4000)
        self.segment(self.b, 7000, 9000)
        self.finish()
        before = self.lab.media_groups(self.sid)
        self.assertEqual([g['status'] for g in before], ['missing','complete'])
        self.lab.upload(self.sid,'screen',self.a,1,0,'video/webm',b'tail',end=4000)
        self.lab.upload(self.sid,'screen',self.a,1,0,'video/webm',b'tail',end=4000)
        after = self.lab.media_groups(self.sid)
        self.assertEqual(after[1], before[1]); self.assertEqual(after[0]['status'],'complete')
        self.assertEqual(after[0]['end_ms'],4000)
        self.assertEqual(len([e for e in st.events(self.sid) if e['kind']=='recording_chunk']),3)
        self.assertEqual((st.DATA/'sessions'/self.sid/'media'/'screen'/self.a/'000001.part').read_bytes(),b'tail')

    def test_duplicate_stop_uses_largest_chunk_count(self):
        self.segment(self.a,0,2000)
        self.mark('recording_stop', self.a, 4000, chunks=2, end_ms=4000)
        self.mark('recording_stop', self.a, 5000, chunks=1, end_ms=5000)
        group = self.lab.media_groups(self.sid)[0]
        self.assertEqual((group['expected_chunks'],group['capture_end_ms'],group['end_ms']), (2,4000,2000))

    def test_transcript_offsets_partial_report_evidence_and_late_retry(self):
        self.segment(self.a, 1000, 3000)
        self.segment(self.b, 7000, 9000, complete=False)
        self.mark('recording_stop',self.b,11000,chunks=2,end_ms=11000)
        self.finish()
        output,speech=self.transcribe(lambda _: [dict(start_ms=100,end_ms=500,text='fixture speech')])
        self.assertEqual([x['start_ms'] for x in output],[1100,7100]);self.assertEqual(speech.call_count,2)
        evidence=self.lab.evidence(self.sid)
        self.assertEqual(evidence['transcription_state']['status'],'partial')
        self.assertEqual(evidence['transcription_state']['skipped_segments'],[])
        self.assertEqual(evidence['transcription_state']['partial_segments'],[self.b])
        self.assertIn('앞부분만 전사',evidence['transcription_state']['detail'])
        self.assertTrue(evidence['recording_gaps']);self.assertFalse(any(e['kind']=='recording_chunk' for e in evidence['timeline']))
        self.lab.upload(self.sid,'screen',self.b,1,7000,'video/webm',b'tail',end=11000)
        output,speech=self.transcribe(lambda _: [dict(start_ms=100,end_ms=500,text='fixture speech')])
        self.assertEqual([x['start_ms'] for x in output],[1100,7100]);self.assertEqual(speech.call_count,2)
        self.assertEqual(st.session(self.sid)['transcription_state']['status'],'completed')

    def test_one_decode_error_does_not_drop_successful_segment_and_is_retried(self):
        self.segment(self.a,0,2000);self.segment(self.b,5000,7000);self.finish()
        def decode(path):
            if path.name==self.a:raise ValueError('fixture invalid container')
            return [dict(start_ms=0,end_ms=500,text='second')]
        output,_=self.transcribe(decode);self.assertEqual(output[0]['start_ms'],5000)
        self.assertEqual(st.session(self.sid)['transcription_state']['status'],'partial')
        output,speech=self.transcribe(lambda _: [dict(start_ms=0,end_ms=500,text='recovered')])
        self.assertEqual(speech.call_count,2);self.assertEqual(len(output),2)

    def test_failed_retranscription_clears_stale_automatic_text_but_preserves_user_text(self):
        self.segment(self.a,0,2000);self.finish()
        self.transcribe(lambda _: [dict(start_ms=0,end_ms=500,text='old automatic')])
        self.segment(self.b,5000,7000)
        self.transcribe(ValueError('fixture failure'))
        self.assertEqual(st.session(self.sid)['transcripts'],[])
        manual=[dict(start_ms=0,end_ms=500,text='user correction')];self.lab.save_transcripts(self.sid,manual)
        output,speech=self.transcribe(ValueError('must not run'))
        self.assertEqual(output,manual);speech.assert_not_called()

    def test_close_before_first_chunk_is_visible(self):
        self.mark('recording_start',self.a,1000,offset_ms=1000);self.mark('recording_gap',ms=4000)
        group=self.lab.media_groups(self.sid)[0]
        self.assertEqual((group['status'],group['chunks'],group['playable']),('missing',0,False))

    def test_long_session_chunk_end_keeps_full_offset(self):
        st.mutate(self.sid,lambda s:s.update(started=st.now()-144000000,deadline_codes=s['codes']))
        self.segment(self.a,143000000,143002000)
        self.assertEqual(self.lab.media_groups(self.sid)[0]['end_ms'],143002000)
        with self.assertRaises(ValueError):self.lab.upload(self.sid,'screen',self.a,1,143000000,'video/webm',b'x',end=st.now())

    def test_gap_frame_does_not_extract_previous_segment(self):
        self.segment(self.a,0,2000);self.segment(self.b,5000,7000);self.finish()
        with patch.object(self.lab,'media_file') as media, self.assertRaisesRegex(ValueError,'녹화가 없습니다'):
            self.lab.frame(self.sid,3000)
        media.assert_not_called()

if __name__ == '__main__':unittest.main()
