"""Disposable browser test backend; mocks only Codex inference (no paid requests)."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
temp=tempfile.TemporaryDirectory(prefix='cote-lab-ui-');os.environ['COACH_DATA_DIR']=temp.name
import server
from coach.service import Lab
server.LAB=Lab()
server.LAB.codex.info=lambda:{'auth':'chatgpt','models':[{'model':'test-model','displayName':'테스트 모델','isDefault':True,'defaultReasoningEffort':'low','supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'high'}],'inputModalities':['text','image']}],'limits':None}
def chat(thread,model,effort,prompt,progress,cancel,save_thread,images=None):
 assert model=='test-model';assert effort in {'low','high'}
 save_thread('fixture-thread');data=__import__('json').loads(prompt);task=data['task'];report=None
 if task.startswith('복기 답변을 반영하여 최종 보고서'):
  first=next(e for e in data['evidence']['timeline'] if e['kind']=='start');refs=[first['id']]
  report={'version':2,'summary':'테스트용 보고서: 중복 조건을 다시 확인하세요.','strengths':['풀이를 실행하고 검증했어요.'],
   'process_review':[{'stage':'접근과 검증','start_ms':0,'end_ms':0,'observation':'테스트용 풀이 흐름','interpretation':'반례 검증을 연습하세요.','evidence_ids':refs}],
   'weaknesses':[{'priority':1,'skill':'반례 검증','tag':'해시','observation':'중복 조건 확인','hypothesis':'테스트용 가설','evidence_ms':0,'evidence_ids':refs,'confidence':'낮음','next_action':'중복 원소 반례 만들기'}],
   'practice_plan':[{'target':'중복 처리','exercise':'중복 원소 세 가지 추적','minutes':5,'success_criteria':'세 입력의 결과를 정확히 예측','transfer_check':'다른 두 수 합 문제'}],
   'progress_comparison':'이전 기록과 비교할 자료가 부족합니다.','limitations':['테스트용 추론 응답입니다.'],'next_practice':['두 수의 목표 합']}

 return {'message':'테스트용 코치 응답입니다. 접근법을 설명해 주세요.','questions':[] if report else [{'question':'처음 어떤 접근을 생각했나요?','ms':1000}],'report':report},{'total':{'totalTokens':42}},'fixture-thread'
server.LAB.codex.chat=chat
http=server.ThreadingHTTPServer(('127.0.0.1',8876),server.Handler)
server.LAB.ide.warmup()
print('Fixture ready: 8876',flush=True)
try:http.serve_forever()
except KeyboardInterrupt:pass
finally:http.server_close();server.LAB.close();temp.cleanup()
