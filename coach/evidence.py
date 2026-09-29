"""Readable code checkpoints with stable links back to the unabridged event log."""
import difflib


def code_evolution(session, events):
    result = []
    for problem in session['problems']:
        pid = problem['id']
        edits = [e for e in events if e['kind'] == 'code' and e['pid'] == pid
                 and not e['payload'].get('recovered_after_finish')]
        if not edits:
            continue
        selected = {}
        def select(index, reason):
            selected.setdefault(index, set()).add(reason)
        select(0, 'first_edit'); select(len(edits)-1, 'last_edit')
        for i, e in enumerate(edits):
            if i == len(edits)-1 or edits[i+1]['ms'] - e['ms'] >= 2000:
                select(i, 'editing_pause')
            if i == len(edits)-1 or edits[i+1]['ms']//15000 != e['ms']//15000:
                select(i, 'interval_end')
        for anchor in events:
            if anchor['pid'] != pid or anchor['kind'] not in {'run', 'submit', 'hint', 'note'}:
                continue
            before = [i for i, e in enumerate(edits) if e['ms'] <= anchor['ms']]
            after = [i for i, e in enumerate(edits) if e['ms'] > anchor['ms']]
            if before: select(before[-1], 'before_'+anchor['kind'])
            if after: select(after[0], 'after_'+anchor['kind'])
        previous = problem['starter']; previous_ms = 0
        for index in sorted(selected):
            e = edits[index]; code = e['payload']['code']
            if code == previous:
                continue
            diff = '\n'.join(difflib.unified_diff(previous.splitlines(), code.splitlines(),
                             fromfile=f'{pid}@{previous_ms}ms', tofile=f'{pid}@{e["ms"]}ms', n=2, lineterm=''))
            result.append({'id':e['id'], 'pid':pid, 'ms':e['ms'], 'from_ms':previous_ms,
                           'reasons':sorted(selected[index]), 'diff':diff})
            previous = code; previous_ms = e['ms']
    return sorted(result, key=lambda e:e['ms'])


def conversations(session):
    return [{'id':m['id'], 'kind':m['kind'], 'pid':m.get('pid'),
             'ms':max(0,m['at']-session['started']), 'question':m.get('question',''),
             'answer':m['answer'].get('message',''), 'questions':m['answer'].get('questions',[])}
            for m in session['messages'] if m['kind'] in {'hint','analyze','reply'}]


def transcript_evidence(session):
    result=[]
    for i,t in enumerate(session['transcripts']):
        item=t|{'id':f'speech-{i}-{t["start_ms"]}'}
        if t['end_ms']-t['start_ms'] > 15000 and len(t['text']) < 30:
            item['timing_note']='짧은 발언에 긴 구간이 배정된 자동 전사입니다. 발언 지속 시간이나 그 동안의 사고 내용을 추론하지 마세요.'
        result.append(item)
    return result


def hint_followups(session, events):
    links = []
    for m in session['messages']:
        if m['kind'] != 'hint': continue
        ms = max(0,m['at']-session['started']); pid = m.get('pid')
        edits = [e for e in events if e['kind']=='code' and e['pid']==pid
                 and not e['payload'].get('recovered_after_finish')]
        before = [e for e in edits if e['ms'] <= ms]
        after = next((e for e in edits if e['ms'] > ms), None)
        run = next((r for r in sorted(session['runs'],key=lambda r:r['ms'])
                    if r['pid']==pid and r['ms'] > ms), None)
        links.append({'hint_id':m['id'], 'pid':pid, 'ms':ms,
                      'before_code':before[-1]['payload']['code'] if before else
                          next((p['starter'] for p in session['problems'] if p['id']==pid),''),
                      'first_edit_after_ms':after['ms'] if after else None,
                      'next_run_id':run['id'] if run else None,
                      'note':'시간상 전후 관계만 나타냄. 이해 여부나 인과관계를 단정하지 말 것.'})
    return links
