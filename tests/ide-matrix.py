"""Real JDT LS integration matrix. Run against tests/lab_fixture.py (no Codex calls)."""
import json
import re
import urllib.request

BASE='http://localhost:8876'
def post(path,data):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req,timeout=100))
s=post('/api/v2/start',{'problem_ids':['brackets'],'kind':'single','mode':'training','resources':'none','duration':40,'model':'test-model','effort':'high'})

def ide(marked,op='completion',**extra):
    before,after=marked.split('|',1)
    return post('/api/v2/ide',{'sid':s['id'],'pid':'brackets','op':op,'code':before+after,
        'line':before.count('\n'),'character':len(before.rsplit('\n',1)[-1].encode('utf-16-le'))//2,**extra})

def items(r):return (r or {}).get('items',[])
def names(r):return [x['label'] for x in items(r)]

def includes(expected):return lambda r:any(expected in x for x in names(r))
cases=[
 ('class-keywords','class Solution { p| }',lambda r:all(k in names(r) for k in ['private','protected','public'])),
 ('multiline-keywords','class Solution {\n    p|\n}',lambda r:all(k in names(r) for k in ['private','protected','public'])),
 ('local-variable','class Solution { void run(int target) { int total=1; tot| } }',includes('total')),
 ('parameter','class Solution { void run(String text) { tex| } }',includes('text')),
 ('string-members','class Solution { void run(String text) { text.sub| } }',includes('substring')),
 ('string-chain','class Solution { void run(String text) { text.substring(1).| } }',includes('length')),
 ('array-members','class Solution { void run(int[] values) { values.| } }',lambda r:includes('length')(r) and not includes('add(')(r)),
 ('generic-list','import java.util.*; class Solution { void run(List<String> names) { names.get(0).| } }',includes('substring')),
 ('nested-generics','import java.util.*; class Solution { void run(Map<String,List<Integer>> groups) { groups.get("x").| } }',includes('add')),
 ('method-overloads','class Solution { void work(int a) {} void work(String a) {} void run(){this.wor|} }',lambda r:sum(x.startswith('work') for x in names(r))>=2),
 ('private-filter','class Parent { private void secret() {} protected void visible() {} } class Solution extends Parent { void run(){ this.| } }',lambda r:includes('visible')(r) and not includes('secret')(r)),
 ('nested-class','class Solution { static class Inner { int count; } void run(Inner inner){inner.|} }',includes('count')),
 ('static-member','class Solution { void run(){ Math.ma| } }',includes('max')),
 ('enum','class Solution { enum Color { RED, BLUE } void run(){Color.|} }',includes('RED')),
 ('unicode-position','class Solution { void run(String text){String note="😀";text.|} }',includes('substring')),
 ('lambda-inference','import java.util.*; class Solution { void run(List<String> names){ names.stream().map(name -> name.|); } }',includes('substring')),
 ('for-snippet','class Solution { void run(){ for| } }',lambda r:any(x['kind']==15 and x.get('insertTextFormat')==2 for x in items(r))),
 ('try-snippet','class Solution { void run(){ try| } }',lambda r:any(x['kind']==15 for x in items(r))),
 ('import-completion','import java.ut|\nclass Solution {}',includes('util')),
 ('auto-import','class Solution { ArrayL| names; }',lambda r:any('ArrayList' in x['label'] and any('import java.util.ArrayList;' in e['newText'] for e in x.get('additionalTextEdits',[])) for x in items(r))),
 ('constructor','class Solution { void run(){ new ArrayL|; } }',includes('ArrayList')),
 ('infix-replace','class Solution { void run(String text){text.sub|string(1);} }',lambda r:any(x.get('textEdit',{}).get('replace',{}).get('end',{}).get('character',0)>x.get('textEdit',{}).get('insert',{}).get('end',{}).get('character',0) for x in items(r))),
 ('method-argument-snippet','import java.util.*; class Solution { void run(List<String> names){names.ad|} }',lambda r:any('add' in x['label'] and x.get('insertTextFormat')==2 and '${' in x.get('textEdit',{}).get('newText','') for x in items(r))),
 ('no-member-modifier-in-method','class Solution { void run(){p|} }',lambda r:not any(x in names(r) for x in ['public','private','protected'])),
 ('no-keywords-in-string','class Solution { String value="p|"; }',lambda r:not any(x in names(r) for x in ['public','private','protected'])),
]
failed=[]
for name,code,check in cases:
    try:
        result=ide(code)
        if not check(result):raise AssertionError(str(names(result)[:12]))
        print('PASS',name,flush=True)
    except Exception as e:failed.append(name);print('FAIL',name,str(e)[:250],flush=True)
other=post('/api/v2/start',{'problem_ids':['brackets'],'kind':'single','mode':'training','resources':'none','duration':40,'model':'test-model','effort':'high'})
for name,code,extra,check in [
 ('new-project-isolation','class Solution { void run(String other){other.sub|} }',{'sid':other['id']},includes('substring')),
 ('return-to-original-project','class Solution { void run(int[] values){values.|} }',{},lambda r:includes('length')(r) and not includes('substring')(r))]:
    try:assert check(ide(code,**extra));print('PASS',name,flush=True)
    except Exception as e:failed.append(name);print('FAIL',name,str(e)[:120],flush=True)
# Documentation resolve is tested immediately because JDT expires its completion cache on each new list.
marked='class Solution { void run(String text){text.sub|} }'
r=ide(marked);candidate=next((x for x in items(r) if 'substring' in x['label']),None)
try:
    assert candidate
    resolved=ide(marked,'resolve',resolve_id=candidate['_resolve_id'])
    assert resolved and resolved.get('documentation'),resolved
    print('PASS completion-documentation',flush=True)
except Exception as e:failed.append('completion-documentation');print('FAIL completion-documentation',str(e)[:120],flush=True)
for name,marked,op,check in [
 ('signature-help','class Solution { void run(String text){text.substring(|);}}','signature',lambda r:bool(r and r.get('signatures'))),
 ('hover','class Solution { void run(String text){te|xt.length();}}','hover',lambda r:bool(r and r.get('contents'))),
 ('type-error','class Solution { boolean run(){return 123;} }|','diagnostics',lambda r:any(d['severity']==1 for d in r['diagnostics'])),
 ('error-clears','class Solution { boolean run(){return true;} }|','diagnostics',lambda r:not any(d['severity']==1 for d in r['diagnostics']))]:
    try:assert check(ide(marked,op));print('PASS',name,flush=True)
    except Exception as e:failed.append(name);print('FAIL',name,str(e)[:120],flush=True)
print(f'{len(cases)+7-len(failed)}/{len(cases)+7} cases passed; failures: {failed}',flush=True)
raise SystemExit(bool(failed))
