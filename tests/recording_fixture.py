"""Disposable recording fixture. No real browser profile, model calls or STT downloads."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temp = tempfile.TemporaryDirectory(prefix='cote-recording-resume-')
os.environ['COACH_DATA_DIR'] = temp.name
import server
from coach.service import Lab
from coach.speech import ffmpeg

server.LAB = Lab()
server.LAB.health = lambda: dict(java=True, ide=True, speech=True, codex=True)
server.LAB.ide.prepare = lambda *args: None
server.LAB.codex.info = lambda: dict(auth='chatgpt', models=[dict(model='test-model', displayName='테스트 모델', isDefault=True, defaultReasoningEffort='low', supportedReasoningEfforts=[dict(reasoningEffort='low')])], limits=None)

def transcribe(path):
    # Real independent media containers must decode; recognized text is synthetic.
    r = subprocess.run([ffmpeg(), '-v', 'error', '-i', str(path), '-f', 'null', '-'], capture_output=True, timeout=20)
    if r.returncode: raise ValueError('Fixture container decode failed')
    return [dict(start_ms=0, end_ms=100, text='격리 테스트 발화')]

server.LAB.speech.transcribe = transcribe

def chat(thread, model, effort, prompt, progress, cancel, save_thread, images=None):
    data=json.loads(prompt); evidence=data['evidence']; report=None
    assert len(evidence['recording']) >= 2
    assert evidence['recording_gaps']
    assert evidence['transcription_state']['processed_segments']
    if data['task'].startswith('복기 답변을 반영하여 최종 보고서'):
        event=next(e for e in evidence['timeline'] if e['kind']=='start')
        report=dict(version=2,summary='격리 테스트: 여러 녹화 구간과 공백 확인',strengths=[],
            process_review=[dict(stage='기록 확인',start_ms=0,end_ms=0,observation='세션 시작',interpretation='녹화 공백을 별도 확인',evidence_ids=[event['id']])],
            weaknesses=[],practice_plan=[],progress_comparison='격리 테스트',limitations=['녹화되지 않은 시간은 관찰할 수 없습니다.'],next_practice=[])
    return dict(message='격리 테스트 복기',questions=[] if report else [dict(question='중단 전후 접근을 설명해 주세요.',ms=0)],report=report),{},'fixture'

server.LAB.codex.chat=chat
port=int(sys.argv[1]) if len(sys.argv)>1 else 8881
http=server.ThreadingHTTPServer(('127.0.0.1',port),server.Handler)
print(json.dumps(dict(port=port,data=temp.name)),flush=True)
try:http.serve_forever()
except KeyboardInterrupt:pass
finally:http.server_close();server.LAB.close();temp.cleanup()
