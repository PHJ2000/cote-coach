import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from coach import store as st
from coach.service import Lab
from coach.problems import BUILTINS,validate_problem
from coach.java import run_java

class LabTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.old=st.DATA;st.DATA=Path(self.temp.name);self.lab=Lab()
  preparing=patch.object(self.lab.ide,'prepare',return_value={'ready':True});self.ide_prepare=preparing.start();self.addCleanup(preparing.stop)
 def tearDown(self):
  self.lab.close();st.DATA=self.old;self.temp.cleanup()
 def start(self,mode='training',ids=None,kind='single'):
  return self.lab.new_session({'problem_ids':ids or ['pair'],'mode':mode,'kind':kind,'duration':40,'resources':'none','model':'test','effort':'low'})
 def event(self,s,code,eid='a'):
  return self.lab.record(s['id'],[{'id':eid,'ms':1000,'kind':'code','pid':'pair','payload':{'code':code}}])
 def immediate_jobs(self):
  self.lab.job=lambda sid,kind,fn:fn(lambda _:None,threading.Event())
 def test_persistence_and_hidden_tests(self):
  s=self.start();self.assertEqual(len(s['problems'][0]['tests']),2);self.assertEqual(len(st.session(s['id'])['problems'][0]['tests']),6)
  self.event(s,'class Solution {}');self.assertEqual(self.lab.view(s['id'])['codes']['pair'],'class Solution {}')
 def test_preparation_precedes_clock_and_exam_skips_it(self):
  clock=[st.now()]
  self.ide_prepare.side_effect=lambda *args:clock.__setitem__(0,clock[0]+5000)
  with patch('coach.service.st.now',side_effect=lambda:clock[0]):
   s=self.start(ids=['pair','brackets'],kind='mock')
  self.assertEqual(s['started'],clock[0]);self.assertEqual(s['deadline']-s['started'],40*60000)
  self.assertEqual(self.ide_prepare.call_count,2)
  self.ide_prepare.reset_mock();self.start('exam');self.ide_prepare.assert_not_called()
 def test_preparation_failure_preserves_basic_practice(self):
  self.ide_prepare.side_effect=RuntimeError('Java 준비 실패')
  s=self.start();self.assertEqual(s['status'],'active');self.assertEqual(s['ide_warning'],'Java 준비 실패')
  self.event(s,'class Solution {}');self.assertEqual(self.lab.view(s['id'])['codes']['pair'],'class Solution {}')
 def test_events_idempotent(self):
  s=self.start();self.event(s,'first');self.event(s,'second')
  self.assertEqual(st.session(s['id'])['codes']['pair'],'first');self.assertEqual(len([e for e in st.events(s['id']) if e['kind']=='code']),1)
 def test_exam_deadline_freezes_all_codes_and_submits(self):
  s=self.start('exam',['pair','supply'],'mock');self.event(s,'deadline code');self.immediate_jobs()
  with patch('coach.service.run_java',return_value={'status':'passed','passed':1,'total':1,'output':'','error':None}):
   st.mutate(s['id'],lambda s:s.update(deadline=st.now()-1));self.lab.expire(s['id'])
  result=st.session(s['id']);self.assertEqual(result['status'],'completed');self.assertEqual(result['deadline_codes']['pair'],'deadline code');self.assertEqual(len(result['runs']),2)
  self.event(s,'late code','late');self.assertEqual(st.session(s['id'])['codes']['pair'],'deadline code');self.assertTrue(st.events(s['id'])[-1]['payload'].get('automatic',True))
  with self.assertRaises(ValueError):self.lab.run(s['id'],'pair',True)
 def test_training_overtime_keeps_deadline_snapshot(self):
  s=self.start();self.event(s,'before');st.mutate(s['id'],lambda s:s.update(deadline=st.now()-1));self.lab.expire(s['id']);self.event(s,'after','b')
  result=st.session(s['id']);self.assertEqual(result['status'],'active');self.assertEqual(result['deadline_codes']['pair'],'before');self.assertEqual(result['codes']['pair'],'after')
 def test_hint_forbidden_in_exam_and_analysis_requires_finish(self):
  s=self.start('exam')
  for kind in ['hint','analyze','reply','report']:
   with self.subTest(kind=kind),self.assertRaises(ValueError):self.lab.chat(s['id'],{'kind':kind,'model':'test','effort':'low'})
 def test_mock_preserves_problem_states(self):
  s=self.start(ids=['pair','brackets'],kind='mock');self.event(s,'changed');self.assertEqual(st.session(s['id'])['codes']['brackets'],BUILTINS[2]['starter'])
 def test_import_validation(self):
  p=copy.deepcopy(BUILTINS[1]);p['title']='가져온 문제';imported=self.lab.import_problem(p);self.assertEqual(imported['source'],'사용자 등록 · 앱 테스트 기준')
  for key,value in [('returns','System.exit(0)'),('method','bad;'),('tests',[{'args':[['wrong'],1],'expected':True}])]:
   bad=copy.deepcopy(p);bad[key]=value
   with self.assertRaises(ValueError):validate_problem(bad)
 def test_media_idempotent_and_gap_detection(self):
  s=self.start();seg='a'*32;self.lab.upload(s['id'],'screen',seg,0,0,'video/webm',b'hello');self.lab.upload(s['id'],'screen',seg,0,0,'video/webm',b'hello')
  with self.assertRaises(ValueError):self.lab.upload(s['id'],'screen',seg,0,0,'video/webm',b'other')
  self.lab.upload(s['id'],'screen',seg,2,0,'video/webm',b'end');self.assertFalse(self.lab.media_groups(s['id'])[0]['complete_sequence'])
  with self.assertRaises(ValueError):self.lab.media_file(s['id'],'screen',seg)
  self.lab.upload(s['id'],'screen',seg,1,0,'video/webm',b' middle ');file,_=self.lab.media_file(s['id'],'screen',seg);self.assertEqual(file.read_bytes(),b'hello middle end')
 def test_reports_saved_and_model_selection_forwarded(self):
  s=self.start();self.lab.finish(s['id']);self.immediate_jobs();report={'summary':'확인','strengths':[],'weaknesses':[{'tag':'해시','observation':'조건 확인','hypothesis':'잠정','evidence_ms':0,'confidence':'낮음','next_action':'반례 적기'}],'next_practice':['새 문제']}
  with patch.object(self.lab.codex,'chat',return_value=({'message':'완료','questions':[],'report':report},{'total':1},'test-thread')) as call:
   self.lab.chat(s['id'],{'kind':'report','model':'chosen-model','effort':'high'})
   self.assertEqual(call.call_args.args[1:3],('chosen-model','high'))
  self.assertEqual(st.session(s['id'])['report'],report);self.assertEqual(self.lab.dashboard()['weaknesses'],{'해시':1})
 def test_jobs_fail_visibly(self):
  s=self.start();done=threading.Event()
  def task(p,c):done.set();raise ValueError('명시적 실패')
  result=self.lab.job(s['id'],'test',task);done.wait(2)
  # Wait on the worker termination event by joining its thread rather than polling.
  for t in threading.enumerate():
   if t.name.endswith('(work)'):t.join(2)
  self.assertEqual(self.lab.get_job(result['job_id'])['status'],'failed')
 def test_repeat_problem_not_counted_as_new(self):
  for _ in range(2):self.lab.finish(self.start()['id'])
  self.assertEqual(self.lab.dashboard()['stats']['problems'],1)

class JavaIntegrationTest(unittest.TestCase):
 def test_real_compile_and_duplicate_edge_cases(self):
  code='class Solution { public boolean solution(int[] a,int t){ java.util.Set<Integer> s=new java.util.HashSet<>(); for(int x:a){if(s.contains(t-x))return true;s.add(x);}return false;} }'
  r=run_java(code,BUILTINS[1],True);self.assertEqual((r['status'],r['passed']),('passed',6))
 def test_compiler_errors_are_preserved(self):
  r=run_java('class Solution { broken }',BUILTINS[1],False);self.assertEqual(r['status'],'failed');self.assertIn('error',r['output'])
 def test_sandbox_cannot_see_host_home(self):
  p=copy.deepcopy(BUILTINS[1]);p['tests']=[{'args':[[1,2],1],'expected':False,'public':True}]
  r=run_java('class Solution { public boolean solution(int[] a,int t){return new java.io.File('+json.dumps(str(Path.home()))+').exists();}}',p,True);self.assertEqual(r['status'],'passed')

if __name__=='__main__':unittest.main()
