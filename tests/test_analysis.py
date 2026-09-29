import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from coach import store as st
from coach.service import Lab
from coach.codex import validate_report


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=st.DATA;st.DATA=Path(self.temp.name)
        self.lab=Lab()
        with patch.object(self.lab.ide,'prepare'):
            self.s=self.lab.new_session({'problem_ids':['brackets','pair'],'kind':'mock','mode':'training',
                                       'duration':20,'resources':'none','model':'chosen','effort':'high'})
        self.sid=self.s['id']
        self.lab.job=lambda sid,kind,fn:fn(lambda text:None,threading.Event())

    def tearDown(self):
        self.lab.close();st.DATA=self.old;self.temp.cleanup()

    def edit(self,ms,code,pid='brackets'):
        st.event(self.sid,'code',{'code':code},pid,ms=ms)

    def audio(self,segment='a'*32):
        self.lab.upload(self.sid,'screen',segment,0,500,'video/webm',b'fake audio')
        self.lab.finish(self.sid)

    def report(self,prompt):
        evidence=json.loads(prompt)['evidence'];start=next(e for e in evidence['timeline'] if e['kind']=='start')
        return {'version':2,'summary':'비교 전에 꺼낼 원소를 검증하기','strengths':[],
                'process_review':[{'stage':'접근','start_ms':0,'end_ms':0,'observation':'기록 확인',
                                   'interpretation':'추가 확인','evidence_ids':[start['id']]}],
                'weaknesses':[],'practice_plan':[{'target':'검증','exercise':'중첩 입력 추적','minutes':5,
                'success_criteria':'스택 상태를 정확히 예측','transfer_check':'다른 괄호 문제'}],
                'progress_comparison':'추가 기록 필요','limitations':[],'next_practice':[]}

    def fake_chat(self,thread,model,effort,prompt,progress,cancel,save_thread,images=None):
        save_thread('review-test');return {'message':'완료','questions':[],'report':self.report(prompt)}, {}, 'review-test'

    def test_code_changes_reverts_problem_boundaries_and_hint_links(self):
        self.edit(1000,'class Solution { int value; }')
        self.edit(16000,'class Solution { String quote; }')
        self.edit(19000,'class Solution { int value; }')
        self.edit(21000,'class Solution { long other; }','pair')
        m={'id':'hint-1','kind':'hint','pid':'brackets','at':self.s['started']+17000,
           'question':'비교?','answer':{'message':'짝을 비교','questions':[]}}
        st.mutate(self.sid,lambda s:s['messages'].append(m));st.event(self.sid,'hint',{'message_id':'hint-1'},'brackets',ms=17000)
        data=self.lab.evidence(self.sid)
        diffs='\n'.join(e['diff'] for e in data['code_evolution'])
        self.assertIn('+class Solution { String quote; }',diffs)
        self.assertIn('-class Solution { String quote; }',diffs)
        pair=next(e for e in data['code_evolution'] if e['pid']=='pair')
        self.assertNotIn('String quote',pair['diff']);self.assertIn('long other',pair['diff'])
        self.assertEqual(data['hint_followups'][0]['first_edit_after_ms'],19000)
        self.assertEqual(data['conversations'][0]['answer'],'짝을 비교')

    def test_transcription_precedes_model_and_is_cached_with_audit_and_archive(self):
        self.audio()
        segments=[{'start_ms':100,'end_ms':300,'text':'맨 뒤를 봐야 하나'}]
        previous={'summary':'이전 보고서'};st.mutate(self.sid,lambda s:s.update(report=previous,thread='hint-thread'))
        prompts=[]
        def chat(*args,**kwargs):
            data=json.loads(args[3]);prompts.append(data)
            self.assertEqual(data['evidence']['transcripts'][0]['start_ms'],600)
            self.assertNotIn('report',data['evidence'])
            self.assertEqual(args[1:3],('chosen','high'))
            self.assertNotEqual(args[0],'hint-thread')
            return self.fake_chat(*args,**kwargs)
        with patch.object(self.lab,'media_file',return_value=(Path('mock.webm'),'video/webm')),patch.object(self.lab.speech,'transcribe',return_value=segments) as speech,patch.object(self.lab.codex,'chat',side_effect=chat):
            for _ in range(2):self.lab.chat(self.sid,{'kind':'report','model':'chosen','effort':'high'})
        speech.assert_called_once();self.assertEqual(len(prompts),2)
        exported=self.lab.export_evidence(self.sid)
        self.assertEqual(exported['report']['version'],2)
        self.assertEqual(exported['report_history'][0]['report'],previous)
        self.assertEqual(len(exported['messages']),2)
        self.assertEqual(exported['analysis_inputs'][0]['input'],prompts[0])
        self.assertEqual(exported['analysis_inputs'][0]['effort'],'high')
        self.assertTrue(exported['events'])

    def test_empty_transcript_is_completed_work_and_not_repeated(self):
        self.audio()
        with patch.object(self.lab,'media_file',return_value=(Path('mock.webm'),'video/webm')),patch.object(self.lab.speech,'transcribe',return_value=[]) as speech:
            for _ in range(2):self.lab.prepare_transcripts(self.sid,lambda _:None,threading.Event())
        speech.assert_called_once();self.assertEqual(st.session(self.sid)['transcription_state']['status'],'no_speech')

    def test_user_corrected_transcript_is_preserved(self):
        self.audio();segments=[{'start_ms':1,'end_ms':2,'text':'수정한 문장'}]
        self.lab.save_transcripts(self.sid,segments)
        with patch.object(self.lab.speech,'transcribe') as speech:
            self.assertEqual(self.lab.prepare_transcripts(self.sid,lambda _:None,threading.Event()),segments)
        speech.assert_not_called()

    def test_failure_is_in_model_input_and_does_not_prevent_code_review(self):
        self.audio();seen=[]
        def chat(*args,**kwargs):seen.append(json.loads(args[3]));return self.fake_chat(*args,**kwargs)
        with patch.object(self.lab,'media_file',side_effect=ValueError('오디오 없음')),patch.object(self.lab.codex,'chat',side_effect=chat):
            self.lab.chat(self.sid,{'kind':'report','model':'chosen','effort':'high'})
        self.assertEqual(seen[0]['evidence']['transcription_state']['status'],'failed')
        self.assertIn('오디오 없음',seen[0]['evidence']['transcription_state']['detail'])

    def test_recording_gap_is_not_reported_as_silence(self):
        self.audio();self.lab.upload(self.sid,'screen','a'*32,2,500,'video/webm',b'end')
        with patch.object(self.lab.speech,'transcribe') as speech:self.lab.prepare_transcripts(self.sid,lambda _:None,threading.Event())
        speech.assert_not_called();self.assertEqual(st.session(self.sid)['transcription_state']['status'],'incomplete')

    def test_new_uploaded_audio_invalidates_cache(self):
        self.audio()
        with patch.object(self.lab,'media_file',return_value=(Path('mock.webm'),'video/webm')),patch.object(self.lab.speech,'transcribe',return_value=[]) as speech:
            self.lab.prepare_transcripts(self.sid,lambda _:None,threading.Event())
            self.lab.upload(self.sid,'screen','a'*32,1,500,'video/webm',b'more')
            self.lab.prepare_transcripts(self.sid,lambda _:None,threading.Event())
        self.assertEqual(speech.call_count,2)

    def test_speech_references_and_uncertain_timing_are_preserved(self):
        self.lab.finish(self.sid)
        self.lab.save_transcripts(self.sid,[{'start_ms':7000,'end_ms':165000,'text':'한번 해보자'}])
        evidence=self.lab.evidence(self.sid);speech=evidence['transcripts'][0]
        self.assertIn('timing_note',speech)
        report=self.report(json.dumps({'evidence':evidence}))
        report['process_review'][0].update(start_ms=7000,end_ms=165000,evidence_ids=[speech['id']])
        validate_report(report,evidence)

    def test_cancel_prevents_transcription_and_model_call(self):
        self.audio();cancel=threading.Event();cancel.set()
        self.lab.job=lambda sid,kind,fn:fn(lambda _:None,cancel)
        with patch.object(self.lab.codex,'chat') as chat,self.assertRaises(InterruptedError):
            self.lab.chat(self.sid,{'kind':'report','model':'chosen','effort':'high'})
        chat.assert_not_called()

    def test_unknown_references_and_missing_report_do_not_replace_old_report(self):
        self.lab.finish(self.sid);previous={'summary':'보존'}
        st.mutate(self.sid,lambda s:s.update(report=previous))
        def bad(*args,**kwargs):
            answer,usage,thread=self.fake_chat(*args,**kwargs)
            answer['report']['process_review'][0]['evidence_ids']=['invented']
            return answer,usage,thread
        with patch.object(self.lab.codex,'chat',side_effect=bad),self.assertRaises(ValueError):
            self.lab.chat(self.sid,{'kind':'report','model':'chosen','effort':'high'})
        self.assertEqual(st.session(self.sid)['report'],previous)
        with self.assertRaises(ValueError):validate_report(None,self.lab.evidence(self.sid))
        report=self.report(json.dumps({'evidence':self.lab.evidence(self.sid)}))
        report['process_review'][0]['end_ms']=123456789
        with self.assertRaises(ValueError):validate_report(report,self.lab.evidence(self.sid))

    def test_followup_can_discuss_report_without_an_existing_thread(self):
        self.lab.finish(self.sid);report={'summary':'현재 보고서'}
        st.mutate(self.sid,lambda s:s.update(report=report))
        with patch.object(self.lab.codex,'chat',side_effect=self.fake_chat) as chat:
            self.lab.chat(self.sid,{'kind':'reply','message':'첫 항목을 설명해줘','model':'chosen','effort':'high'})
        prompt=json.loads(chat.call_args.args[3]);self.assertEqual(prompt['current_report'],report)
        self.assertEqual(st.session(self.sid)['report'],report)


if __name__=='__main__':unittest.main()
