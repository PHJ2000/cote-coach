import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from coach import store as st
from coach.service import Lab
from coach.problems import BUILTINS,PERF_BUDGET_MS,PERF_LIMIT_MS,expand_test,validate_problem
from coach.java import harness_source,run_java,stream

PAIR=next(p for p in BUILTINS if p['id']=='pair')
LINEAR_PAIR='class Solution { public boolean solution(int[] a,int t){ java.util.Set<Integer> s=new java.util.HashSet<>(); for(int x:a){if(s.contains(t-x))return true;s.add(x);}return false;} }'
QUADRATIC_PAIR='class Solution { public boolean solution(int[] a,int t){ for(int i=0;i<a.length;i++)for(int j=i+1;j<a.length;j++)if(a[i]+a[j]==t)return true; return false;} }'

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
  s=self.start();self.assertEqual(len(s['problems'][0]['tests']),2);self.assertEqual(len(st.session(s['id'])['problems'][0]['tests']),16)
  self.assertEqual(s['problems'][0]['test_count'],16)
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

 # ── Code event ordering ──
 def code(self,s,eid,ms,code,seq=None):
  e={'id':eid,'ms':ms,'kind':'code','pid':'pair','payload':{'code':code}}
  if seq is not None:e['seq']=seq
  self.lab.record(s['id'],[e])
 def test_older_code_event_does_not_overwrite_newer(self):
  s=self.start();self.code(s,'n',2000,'newer');self.code(s,'o',1000,'older')
  self.assertEqual(st.session(s['id'])['codes']['pair'],'newer')
  stored={e['id']:e for e in st.events(s['id']) if e['kind']=='code'}
  self.assertEqual(stored['o']['payload']['code'],'older');self.assertTrue(stored['o']['payload']['superseded_on_arrival'])
  self.assertNotIn('superseded_on_arrival',stored['n']['payload'])
 def test_same_ms_is_ordered_by_client_sequence(self):
  s=self.start();self.code(s,'b',3000,'b',seq=5);self.code(s,'a',3000,'a',seq=4)
  self.assertEqual(st.session(s['id'])['codes']['pair'],'b')
  self.code(s,'c',3000,'c',seq=6);self.assertEqual(st.session(s['id'])['codes']['pair'],'c')
  self.code(s,'d',3000,'d');self.assertEqual(st.session(s['id'])['codes']['pair'],'c') # No sequence ranks below any sequence.
 def test_versions_survive_restart_and_legacy_sessions_use_stored_events(self):
  s=self.start();self.code(s,'n',5000,'newer',seq=1)
  other=Lab()
  try:
   other.record(s['id'],[{'id':'o','ms':4000,'kind':'code','pid':'pair','payload':{'code':'older'}}])
   self.assertEqual(st.session(s['id'])['codes']['pair'],'newer')
   # A session saved before versioning has no code_versions: fall back to stored events.
   st.mutate(s['id'],lambda x:x.pop('code_versions'))
   other.record(s['id'],[{'id':'o2','ms':4500,'kind':'code','pid':'pair','payload':{'code':'older2'}}])
   self.assertEqual(st.session(s['id'])['codes']['pair'],'newer')
   other.record(s['id'],[{'id':'n2','ms':6000,'kind':'code','pid':'pair','payload':{'code':'newest'}}])
   self.assertEqual(st.session(s['id'])['codes']['pair'],'newest')
  finally:other.close()
 def test_late_old_event_after_deadline_keeps_deadline_and_current_code(self):
  s=self.start();self.code(s,'n',2000,'deadline code');st.mutate(s['id'],lambda x:x.update(deadline=st.now()-1));self.lab.expire(s['id'])
  self.code(s,'o',1000,'stale');result=st.session(s['id'])
  self.assertEqual((result['codes']['pair'],result['deadline_codes']['pair']),('deadline code','deadline code'))
  self.code(s,'x',2500,'overtime');result=st.session(s['id'])
  self.assertEqual((result['codes']['pair'],result['deadline_codes']['pair']),('overtime','deadline code'))
 def test_invalid_sequence_rejected(self):
  s=self.start()
  for bad in [-1,'1',1.5,2**53]:
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.code(s,'bad',1000,'x',seq=bad)
 def replay(self,events,ms,pid='pair'):
  # Mirrors client seek(): the last code-bearing event at or before ms wins.
  code=None
  for e in events:
   if e['ms']>ms:break
   if e['pid']==pid and e['kind'] in {'code','run','submit'} and 'code' in e['payload']:code=e['payload']['code']
  return code
 def test_same_ms_reverse_arrival_orders_replay_and_evidence_like_current_code(self):
  s=self.start();self.code(s,'new',1000,'newer',seq=2)
  self.lab.record(s['id'],[{'id':'note','ms':1000,'kind':'note','pid':'pair','payload':{'text':'메모'}}])
  self.code(s,'old',1000,'older',seq=1);self.code(s,'early',500,'earliest',seq=3) # Delayed lower-ms edit.
  view=self.lab.view(s['id']);self.assertEqual(view['codes']['pair'],'newer')
  order=[e['id'] for e in view['events'] if e['id'] in {'new','note','old','early'}]
  self.assertEqual(order,['early','old','note','new']) # Code slots re-ordered by seq; the note keeps its slot.
  self.assertEqual(self.replay(view['events'],1000),'newer');self.assertEqual(self.replay(view['events'],700),'earliest')
  evolution=[e for e in self.lab.evidence(s['id'])['code_evolution'] if e['pid']=='pair']
  self.assertEqual(evolution[-1]['id'],'new');self.assertIn('+newer',evolution[-1]['diff'])
  self.assertEqual(view['code_versions']['pair'],[1000,2])
  # Legacy fallback must pick the same latest event (seq 2), not the later arrival (seq 1).
  st.mutate(s['id'],lambda x:x.pop('code_versions'))
  self.assertEqual(self.lab.view(s['id'])['code_versions']['pair'],[1000,2])
  self.code(s,'tie',1000,'lower seq',seq=1);self.assertEqual(st.session(s['id'])['codes']['pair'],'newer')
  self.code(s,'next',1000,'higher seq',seq=7);self.assertEqual(st.session(s['id'])['codes']['pair'],'higher seq')
 def test_legacy_same_ms_without_sequence_keeps_arrival_order(self):
  s=self.start();self.code(s,'a',1000,'first');self.code(s,'b',1000,'second')
  view=self.lab.view(s['id']);self.assertEqual(view['codes']['pair'],'second');self.assertEqual(self.replay(view['events'],1000),'second')
 def test_legacy_session_view_infers_code_versions_without_writing(self):
  s=self.start()
  st.event(s['id'],'code',{'code':'legacy 1'},'pair',ms=1000,eid='l1');st.event(s['id'],'code',{'code':'legacy 3'},'pair',ms=3000,eid='l3')
  st.mutate(s['id'],lambda x:(x.pop('code_versions',None),x['codes'].update(pair='legacy 3')))
  view=self.lab.view(s['id']);self.assertEqual(view['code_versions'],{'pair':[3000,-1]}) # brackets-free single session; no events -> absent
  self.assertNotIn('code_versions',st.session(s['id'])) # Inference is not a migration.
  self.code(s,'stale',2000,'stale local');self.assertEqual(st.session(s['id'])['codes']['pair'],'legacy 3')

 # ── Recording completeness ──
 SEG='b'*32
 def stop(self,s,chunks,eid='stop',state=None):
  payload={'segment':self.SEG,'kind':'screen','chunks':chunks}|({'state':state} if state else {})
  self.lab.record(s['id'],[{'id':eid,'ms':5000,'kind':'recording_stop','pid':'pair','payload':payload}])
 def group(self,s):
  return next(g for g in self.lab.media_groups(s['id']) if g['segment']==self.SEG)
 def test_missing_tail_is_not_complete_and_recovers_after_late_upload(self):
  s=self.start();self.lab.upload(s['id'],'screen',self.SEG,0,0,'video/webm',b'a');self.stop(s,3)
  g=self.group(s);self.assertFalse(g['complete_sequence']);self.assertEqual((g['status'],g['missing'],g['expected_chunks']),('pending',[1,2],3))
  self.lab.finish(s['id']);g=self.group(s);self.assertEqual(g['status'],'missing');self.assertTrue(g['playable'])
  for seq in (1,2):self.lab.upload(s['id'],'screen',self.SEG,seq,0,'video/webm',b'x')
  g=self.group(s);self.assertTrue(g['complete_sequence']);self.assertEqual((g['status'],g['missing_count'],g['ended']),('complete',0,'normal'))
 def test_stop_before_uploads_and_middle_gap(self):
  s=self.start();self.stop(s,3)
  g=self.group(s);self.assertEqual((g['chunks'],g['status'],g['playable'],g['url']),(0,'pending',False,None))
  self.lab.upload(s['id'],'screen',self.SEG,0,0,'video/webm',b'a');self.lab.upload(s['id'],'screen',self.SEG,2,0,'video/webm',b'c')
  g=self.group(s);self.assertEqual((g['status'],g['missing'],g['playable']),('pending',[1],False))
  self.lab.upload(s['id'],'screen',self.SEG,1,0,'video/webm',b'b');self.assertEqual(self.group(s)['status'],'complete')
 def test_missing_stop_is_unconfirmed_and_interrupted_stop_is_known(self):
  s=self.start();self.lab.upload(s['id'],'screen',self.SEG,0,0,'video/webm',b'a')
  self.assertEqual(self.group(s)['status'],'recording')
  self.lab.finish(s['id']);g=self.group(s);self.assertEqual(g['status'],'unconfirmed');self.assertFalse(g['complete_sequence'])
  self.stop(s,1,state='interrupted');g=self.group(s);self.assertEqual((g['status'],g['ended']),('complete','interrupted'))
 def test_declared_but_never_uploaded_segment_is_visible(self):
  s=self.start();self.stop(s,2);self.lab.finish(s['id'])
  g=self.group(s);self.assertEqual((g['status'],g['missing_count']),('missing',2))
  self.assertEqual(self.lab.view(s['id'])['media'][0]['status'],'missing')
  self.assertEqual(self.lab.evidence(s['id'])['recording'][0]['status'],'missing')
 def test_transcription_uses_prefix_marks_missing_tail_and_reports_unconfirmed(self):
  s=self.start();self.lab.upload(s['id'],'screen',self.SEG,0,0,'video/webm',b'a');self.stop(s,2);self.lab.finish(s['id'])
  with patch.object(self.lab,'media_file',return_value=(Path('prefix.webm'),'video/webm')),patch.object(self.lab.speech,'transcribe',return_value=[]) as speech:self.lab.prepare_transcripts(s['id'],lambda _:None,threading.Event())
  speech.assert_called_once();state=st.session(s['id'])['transcription_state'];self.assertEqual((state['status'],state['recording']),('partial','missing'));self.assertEqual(state['partial_segments'],[self.SEG])
  self.lab.upload(s['id'],'screen',self.SEG,1,0,'video/webm',b'b')
  with patch.object(self.lab,'media_file',return_value=(Path('mock.webm'),'video/webm')),patch.object(self.lab.speech,'transcribe',return_value=[]) as speech:
   self.lab.prepare_transcripts(s['id'],lambda _:None,threading.Event())
  speech.assert_called_once();state=st.session(s['id'])['transcription_state'];self.assertEqual((state['status'],state['recording']),('no_speech','complete'))
  s2=self.start();self.lab.upload(s2['id'],'screen',self.SEG,0,0,'video/webm',b'a');self.lab.finish(s2['id'])
  with patch.object(self.lab,'media_file',return_value=(Path('mock.webm'),'video/webm')),patch.object(self.lab.speech,'transcribe',return_value=[]):
   self.lab.prepare_transcripts(s2['id'],lambda _:None,threading.Event())
  state=st.session(s2['id'])['transcription_state'];self.assertEqual(state['recording'],'unconfirmed');self.assertIn('종료 확인이 없어',state['detail'])
 def test_media_file_serves_contiguous_prefix_only(self):
  s=self.start();self.lab.upload(s['id'],'screen',self.SEG,0,0,'video/webm',b'ab');self.stop(s,2)
  file,_=self.lab.media_file(s['id'],'screen',self.SEG);self.assertEqual(file.read_bytes(),b'ab')

 # ── Generated hidden tests ──
 def test_generated_tests_are_hidden_compact_and_validated(self):
  s=self.start();self.assertTrue(all(t['public'] for t in self.lab.view(s['id'])['problems'][0]['tests']))
  self.assertLess(len(st.dump(st.session(s['id']))),60000) # Specs, not 100k-element arrays, are stored.
  args,expected=expand_test(PAIR['tests'][8]);self.assertEqual((args[0],args[1],expected),([0]*100000,1,False))
  imported=validate_problem(copy.deepcopy(PAIR)|{'title':'복사'});self.assertEqual(sum('generate' in t for t in imported['tests']),8)
  self.assertEqual(sum(bool(t.get('performance')) for t in imported['tests']),8) # Shared budget survives import.
  bad=[{'name':'pair','shape':'zeros','n':200000,'seed':0},{'name':'nope'},{'name':'pair','shape':'zeros','n':5}]
  for spec in bad:
   with self.subTest(spec=spec),self.assertRaises(ValueError):validate_problem(copy.deepcopy(PAIR)|{'tests':PAIR['tests'][:2]+[{'generate':spec}]})
  with self.assertRaises(ValueError):validate_problem(copy.deepcopy(PAIR)|{'tests':PAIR['tests'][:2]+[{'generate':{'name':'window','n':10,'k':3,'seed':1}}]}) # long result for boolean problem
  forced=validate_problem(copy.deepcopy(PAIR)|{'tests':PAIR['tests'][:2]+[{'generate':PAIR['tests'][8]['generate'],'public':True}]})
  self.assertFalse(forced['tests'][2]['public'])
 def test_generated_expectations_match_independent_reference(self):
  def pair(a,t):
   seen=set()
   for x in a:
    if t-x in seen:return True
    seen.add(x)
   return False
  def supply(req,dl):
   from collections import Counter
   return next(iter(Counter(req)-Counter(dl)))
  def brackets(text):
   stack=[];close={')':'(',']':'[','}':'{'}
   for c in text:
    if c in close:
     if not stack or stack.pop()!=close[c]:return False
    else:stack.append(c)
   return not stack
  def window(v,k):
   prefix=[0]
   for x in v:prefix.append(prefix[-1]+x)
   return max(prefix[i+k]-prefix[i] for i in range(len(v)-k+1))
  def stairs(n):
   ways=[1,1]
   for i in range(2,n+1):ways.append((ways[-1]+ways[-2])%1000000007)
   return ways[n]
  refs={'pair':pair,'supply':supply,'brackets':brackets,'window':window,'stairs':stairs}
  seen=0
  for p in BUILTINS:
   for t in p['tests']:
    if 'generate' not in t:continue
    args,expected=expand_test(t);seen+=1
    with self.subTest(problem=p['id'],spec=t['generate']):
     self.assertEqual(refs[p['id']](*args),expected)
     self.assertFalse(t['public']);self.assertTrue(t['label'])
  self.assertEqual(seen,13)
  self.assertEqual(expand_test({'generate':{'name':'stairs','n':50}})[1],365010934)

class HarnessUnitTest(unittest.TestCase):
 def test_harness_source_has_no_test_data_and_constant_size(self):
  src=harness_source(PAIR);self.assertNotIn('RESULT_',src);self.assertLess(len(src),6000)
  # The source depends only on the signature: different/huge test data yields the same text,
  # and a distinctive test value never appears in it.
  for tests in ([{'args':[[987654321]*20000,-123456789],'expected':False,'public':True}],[{'args':[[0]*100000,1],'expected':False,'public':True}],[]):
   with self.subTest(n=len(tests)):
    self.assertEqual(harness_source(copy.deepcopy(PAIR)|{'tests':tests}),src)
  self.assertNotIn('987654321',src);self.assertNotIn('123456789',src)
  self.assertNotEqual(harness_source(next(p for p in BUILTINS if p['id']=='brackets')),src)
 def test_binary_stream_layout(self):
  data=stream('M',[([[1,-2],-3],1000,True)],PAIR['params'],budget=2000)
  self.assertEqual(data,b'\x00\x00\x00\x01\x00M'+b'\x00\x00\x00\x01'+b'\x00\x00\x07\xd0'+b'\x00\x00\x03\xe8\x01'+b'\x00\x00\x00\x02\x00\x00\x00\x01\xff\xff\xff\xfe'+b'\xff\xff\xff\xfd')
  text=stream('M',[(['\ud800😀',],0,False)],[{'type':'String','name':'s'}])
  self.assertEqual(text[-15:],b'\x00\x00\x00\x00\x00'+b'\x00\x00\x00\x03\xd8\x00\xd8\x3d\xde\x00')
 def test_pair_performance_cases_share_the_budget(self):
  perf=[t for t in PAIR['tests'] if t.get('performance')]
  self.assertEqual(len(perf),8);self.assertTrue(all(t['time_limit_ms']==PERF_LIMIT_MS and not t['public'] for t in perf))
  self.assertTrue(all(expand_test(t)[0][0].__len__()==100000 for t in perf)) # Every case is at the documented maximum n.
  self.assertLess(PERF_BUDGET_MS,len(perf)*PERF_LIMIT_MS) # The shared budget is the binding limit.
 def test_docker_failure_is_grader_error_not_wrong_answer(self):
  with patch('coach.java.subprocess.Popen',side_effect=FileNotFoundError('docker')):
   r=run_java(LINEAR_PAIR,PAIR,True)
  self.assertEqual((r['status'],r['passed'],r['total']),('error',0,16));self.assertIn('Docker',r['grader_error'])
  self.assertTrue(all(not t['passed'] and t['error']=='채점기 오류' for t in r['tests']))
  self.assertNotIn('args',r['tests'][8]) # Hidden inputs never reach the result.

class JavaIntegrationTest(unittest.TestCase):
 def test_real_compile_and_duplicate_edge_cases(self):
  r=run_java(LINEAR_PAIR,PAIR,True);self.assertEqual((r['status'],r['passed'],r['total']),('passed',16,16),r)
  self.assertIsNone(r['grader_error'])
  for t in r['tests'][8:]:self.assertNotIn('args',t);self.assertTrue(t['label'].startswith('대입력'));self.assertTrue(t['performance'])
  # Margin check, not a speed benchmark: a linear answer should use well under half the shared budget.
  spent=sum(t['time_ms'] for t in r['tests'][8:]);self.assertLess(spent,PERF_BUDGET_MS/2,[t['time_ms'] for t in r['tests'][8:]])
 def test_large_input_no_longer_breaks_compilation(self):
  p=copy.deepcopy(PAIR)|{'tests':[{'args':[[0]*n,1],'expected':False,'public':True} for n in (20000,100000)]}
  r=run_java(LINEAR_PAIR,p,True);self.assertEqual((r['status'],r['passed']),('passed',2),r)
  self.assertNotIn('code too large',r['output']);self.assertLess(len(r['output']),1000)
 def test_quadratic_solution_passes_examples_but_fails_performance(self):
  r=run_java(QUADRATIC_PAIR,PAIR,True)
  self.assertEqual(r['status'],'failed',r);self.assertIsNone(r['grader_error'])
  self.assertTrue(all(t['passed'] for t in r['tests'][:8]),r['tests'][:8])
  # A real O(n^2) scan (~0.5-0.7s per case on a fast machine) runs out of the shared
  # budget part-way through the eight max-size cases; the remaining cases are not run.
  perf=r['tests'][8:];stopped=next((t for t in perf if t.get('timeout')),None);self.assertIsNotNone(stopped,perf)
  self.assertTrue(f'합계 {PERF_BUDGET_MS/1000:g}초' in stopped['error'] or f'테스트당 {PERF_LIMIT_MS/1000:g}초' in stopped['error'],stopped)
  self.assertTrue(all(not t['passed'] for t in perf[perf.index(stopped):]))
  if stopped.get('budget_exceeded'):self.assertGreaterEqual(sum(t['time_ms'] or 0 for t in perf),PERF_BUDGET_MS*0.9)
 def test_types_unicode_escapes_and_long_bounds_round_trip(self):
  text='한글 "따옴표" \\ 역슬래시\n개행\t탭 😀 \ud800 \x01'
  p={'method':'solution','returns':'String','params':[{'type':'String','name':'s'},{'type':'long','name':'v'},{'type':'String[]','name':'parts'}],
     'tests':[{'args':[text,v,['a','"b"','']],'expected':text+str(v)+'|'.join(['a','"b"','']),'public':True} for v in (9223372036854775807,-9223372036854775808)]}
  code='class Solution { public String solution(String s,long v,String[] parts){ return s+v+String.join("|",parts);} }'
  r=run_java(code,p,True);self.assertEqual((r['status'],r['passed']),('passed',2),r)
  p={'method':'solution','returns':'long[]','params':[{'type':'int[][]','name':'g'},{'type':'boolean[]','name':'f'},{'type':'long[]','name':'xs'},{'type':'double','name':'d'},{'type':'int','name':'n'},{'type':'boolean','name':'b'}],
     'tests':[{'args':[[[1,2],[3],[]],[True,False],[-9223372036854775808,9223372036854775807],2.5,-2147483648,True],'expected':[3,1,9223372036854775807,25,-2147483648,1],'public':True}]}
  code='class Solution { public long[] solution(int[][] g,boolean[] f,long[] xs,double d,int n,boolean b){ return new long[]{g.length,f[0]?1:0,xs[1],Math.round(d*10),n,b?1:0};} }'
  r=run_java(code,p,False);self.assertEqual((r['status'],r['passed']),('passed',1),r)
 def test_learner_output_without_newline_does_not_hide_result(self):
  p=copy.deepcopy(PAIR)|{'tests':PAIR['tests'][:2]}
  code='class Solution { public boolean solution(int[] a,int t){ System.out.print("debug"); return t==9;} }'
  r=run_java(code,p,False);self.assertEqual(r['passed'],2,r);self.assertIn('debug',r['output'])
 def test_compiler_errors_are_preserved(self):
  r=run_java('class Solution { broken }',BUILTINS[1],False);self.assertEqual(r['status'],'failed');self.assertIn('error',r['output'])
 def test_sandbox_cannot_see_host_home(self):
  p=copy.deepcopy(BUILTINS[1]);p['tests']=[{'args':[[1,2],1],'expected':False,'public':True}]
  r=run_java('class Solution { public boolean solution(int[] a,int t){return new java.io.File('+json.dumps(str(Path.home()))+').exists();}}',p,True);self.assertEqual(r['status'],'passed')

if __name__=='__main__':unittest.main()
