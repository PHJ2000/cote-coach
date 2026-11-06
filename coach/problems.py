"""Original practice problems with explicit Java signatures and test oracles."""
import copy
import functools
import json
import random
import re
import string
import uuid

TYPES = {'int','long','double','boolean','String','int[]','long[]','String[]','int[][]','boolean[]'}
# Each test call has its own wall-clock limit inside the JVM; the whole run
# (container start + javac + all tests) is separately limited in java.py.
DEFAULT_TIME_LIMIT_MS = 3000
# Performance cases run at the maximum constraint. Each call has PERF_LIMIT_MS,
# and all performance cases of one submission share PERF_BUDGET_MS (measured in
# the JVM, excluding container start/compile/input parsing). A JIT-optimized
# O(n^2) pair scan takes ~0.6s per n=100,000 case on a fast machine (measured by
# Codex), so a single case cannot separate it from O(n) without a fragile
# threshold. Eight such cases need ~4.5s+ of O(n^2) work against a 2s budget,
# while a linear/O(n log n) answer needs roughly 10-30ms per case (>10x margin).
PERF_LIMIT_MS = 1000
PERF_BUDGET_MS = 2000

# Large hidden tests are stored as tiny generator specs and expanded only when
# grading, so sessions/UI never duplicate megabytes of input. Generators are
# deterministic (fixed seed) and return (args, expected) by construction or by
# a simple linear reference.
def _signed_evens(rnd, n):
    return [2*v*(1 if i%2 else -1) for i,v in enumerate(rnd.sample(range(1,500001),n))]

def _pair(shape, n, seed):
    rnd=random.Random(seed)
    if shape=='zeros':return [[0]*n,1],False
    if shape=='no_pair':return [_signed_evens(rnd,n),1],False
    return [_signed_evens(rnd,n-2)+[0,1],1],True # Only the last two elements (0+1) match.

def _supply(n, seed):
    rnd=random.Random(seed)
    names=[''.join(rnd.choice(string.ascii_lowercase) for _ in range(rnd.randint(1,20))) for _ in range(2000)]
    requested=[rnd.choice(names) for _ in range(n)];delivered=requested[:]
    missing=delivered.pop(rnd.randrange(n));rnd.shuffle(delivered)
    return [requested,delivered],missing

def _brackets(shape, n, seed):
    rnd=random.Random(seed);close={'(':')','[':']','{':'}'}
    opens=[rnd.choice('([{') for _ in range(n//2)];text=''.join(opens)+''.join(close[c] for c in reversed(opens))
    if shape=='valid':return [text],True
    wrong=next(c for c in ')]}' if c!=close[opens[0]]);return [text[:-1]+wrong],False

def _window(n, k, seed):
    rnd=random.Random(seed);values=[rnd.randint(-10000,10000) for _ in range(n)]
    current=best=sum(values[:k])
    for i in range(k,n):current+=values[i]-values[i-k];best=max(best,current)
    return [values,k],best

def _stairs(n):
    a,b=1,1
    for _ in range(n):a,b=b,(a+b)%1000000007
    return [n],a

SEED=(0,2**31-1)
GENERATORS={
    'pair':(_pair,{'shape':{'zeros','no_pair','tail_pair'},'n':(2,100000),'seed':SEED}),
    'supply':(_supply,{'n':(1,100000),'seed':SEED}),
    'brackets':(_brackets,{'shape':{'valid','invalid'},'n':(2,100000),'seed':SEED}),
    'window':(_window,{'n':(1,200000),'k':(1,200000),'seed':SEED}),
    'stairs':(_stairs,{'n':(0,100000)}),
}

def check_generate(spec):
    if not isinstance(spec,dict) or spec.get('name') not in GENERATORS:raise ValueError('테스트 생성 규칙을 확인하세요.')
    schema=GENERATORS[spec['name']][1]
    if set(spec)!=set(schema)|{'name'}:raise ValueError('테스트 생성 규칙의 항목을 확인하세요.')
    for key,rule in schema.items():
        value=spec[key]
        if isinstance(rule,set):ok=isinstance(value,str) and value in rule
        else:ok=type(value) is int and rule[0]<=value<=rule[1]
        if not ok:raise ValueError('테스트 생성 규칙의 값을 확인하세요: '+key)
    if spec['name']=='window' and spec['k']>spec['n']:raise ValueError('k는 n 이하여야 합니다.')

@functools.lru_cache(maxsize=32)
def _generated(key):
    spec=json.loads(key);fn=GENERATORS[spec.pop('name')][0]
    return fn(**spec)

def expand_test(test, problem=None):
    """(args, expected) for a literal or generated test. Callers must not mutate the result."""
    if 'generate' not in test:return test['args'],test['expected']
    check_generate(test['generate'])
    return _generated(json.dumps(test['generate'],sort_keys=True))

def make(pid, title, level, tags, description, returns, params, tests, generated=()):
    default = {'int':'0','long':'0L','double':'0.0','boolean':'false','String':'""'}.get(returns, 'new '+returns.replace('[]','[0]',1))
    starter='import java.util.*;\n\nclass Solution {\n    public '+returns+' solution('+', '.join(t+' '+n for t,n in params)+') {\n        // 여기에 풀이를 작성하세요.\n        return '+default+';\n    }\n}\n'
    hidden=[{'generate':spec,'public':False,'label':label}|({'time_limit_ms':limit[0],'performance':True} if limit else {}) for spec,label,*limit in generated]
    return dict(id=pid,title=title,level=level,tags=tags,description=description,source='코테 연습실 자체 문제',url='',method='solution',returns=returns,params=[{'type':t,'name':n} for t,n in params],starter=starter,tests=[{'args':args,'expected':expected,'public':i<2} for i,(args,expected) in enumerate(tests)]+hidden)

BUILTINS=[
make('supply','부족한 물품 찾기',1,['해시','배열·문자열'],'''창고에서 요청한 물품 이름 requested와 실제 도착한 물품 이름 delivered가 주어집니다. 같은 이름의 물품을 여러 개 요청할 수 있습니다. 도착한 물품은 요청한 물품의 일부이고, 정확히 한 개가 덜 도착했습니다. 부족한 물품의 이름을 반환하세요.

제한 조건
• 1 ≤ requested.length ≤ 100,000
• delivered.length = requested.length - 1
• 물품 이름은 영문 소문자로 된 길이 1~20의 문자열
• 두 배열의 순서는 서로 다를 수 있습니다.''','String',[('String[]','requested'),('String[]','delivered')],[([['pen','book','pen'],['pen','book']],'pen'),([['a'],[]],'a'),([['x','y','z'],['z','x']],'y'),([['a','b','a','a'],['a','a','b']],'a'),([['red','blue','red','blue'],['blue','red','red']],'blue')],
    [({'name':'supply','n':100000,'seed':21},'대입력 성능 · 10만 개 중복 이름',PERF_LIMIT_MS)]),
make('pair','두 수의 목표 합',1,['해시','완전탐색'],'''정수 배열 numbers에서 서로 다른 두 인덱스의 값을 더해 target을 만들 수 있으면 true, 없으면 false를 반환하세요. 같은 값을 가진 서로 다른 원소는 사용할 수 있습니다.

제한 조건
• 2 ≤ numbers.length ≤ 100,000
• -1,000,000 ≤ 각 원소와 target ≤ 1,000,000
• 합을 만들 수 있는지 여부만 반환합니다.''','boolean',[('int[]','numbers'),('int','target')],[([[2,7,11,15],9],True),([[1,2,3],8],False),([[3,3],6],True),([[-4,1,5],1],True),([[0,1],0],False),([[0,0],0],True),([[1000000,-1000000],0],True),([[1000000,1000000,-1000000],1000000],False)],
    [({'name':'pair','shape':'zeros','n':100000,'seed':0},'대입력 성능 · 같은 값 10만 개, 답 없음',PERF_LIMIT_MS),
     *[({'name':'pair','shape':'no_pair','n':100000,'seed':seed},'대입력 성능 · 서로 다른 값 10만 개, 답 없음',PERF_LIMIT_MS) for seed in (11,13,15)],
     *[({'name':'pair','shape':'tail_pair','n':100000,'seed':seed},'대입력 성능 · 마지막 두 원소만 정답',PERF_LIMIT_MS) for seed in (12,14,16,18)]]),
make('brackets','괄호 검사기',2,['스택·큐','배열·문자열'],'''문자열 text는 (, ), [, ], {, } 여섯 문자로만 이루어집니다. 모든 열린 괄호가 같은 종류의 닫힌 괄호와 올바른 순서로 대응하면 true를 반환하세요. 빈 문자열은 올바른 괄호 문자열입니다.

제한 조건
• 0 ≤ text.length ≤ 100,000
• 중첩된 괄호와 연속된 괄호가 모두 나올 수 있습니다.''','boolean',[('String','text')],[(['([]){}'],True),(['([)]'],False),([''],True),(['('],False),([']'],False),(['{{[()]}}'],True),(['())('],False)],
    [({'name':'brackets','shape':'valid','n':100000,'seed':31},'대입력 성능 · 깊이 5만 중첩',PERF_LIMIT_MS),
     ({'name':'brackets','shape':'invalid','n':100000,'seed':32},'대입력 성능 · 마지막 괄호만 불일치',PERF_LIMIT_MS)]),
make('window','연속 구간의 최대 합',2,['배열·문자열','기타'],'''정수 배열 values에서 길이가 정확히 k인 연속 구간의 합 중 최댓값을 반환하세요. 원소는 음수일 수도 있습니다.

제한 조건
• 1 ≤ values.length ≤ 200,000
• 1 ≤ k ≤ values.length
• -10,000 ≤ 각 원소 ≤ 10,000
• 반환형은 long입니다.''','long',[('int[]','values'),('int','k')],[([[1,3,-2,5,4],3],7),([[-5,-2,-7],2],-7),([[4],1],4),([[1,2,3],3],6),([[-9,-1,-5],1],-1),([[0,0,0],2],0)],
    [({'name':'window','n':200000,'k':100000,'seed':41},'대입력 성능 · n=20만, k=10만',PERF_LIMIT_MS)]),
make('islands','연결된 땅의 개수',2,['DFS·BFS','그래프'],'''0과 1로 이루어진 직사각형 격자 grid가 주어집니다. 1은 땅, 0은 물입니다. 상하좌우로 연결된 땅을 하나의 섬으로 셉니다. 섬의 개수를 반환하세요. 대각선은 연결되지 않습니다. 입력 배열을 수정해도 됩니다.

제한 조건
• 행과 열의 수는 각각 1~100
• grid는 직사각형이며 각 원소는 0 또는 1''','int',[('int[][]','grid')],[([[[1,1,0],[0,1,0],[1,0,1]]],3),([[[0,0],[0,0]]],0),([[[1]]],1),([[[1,0],[0,1]]],2),([[[1,1,1],[1,1,1]]],1)]),
make('stairs','한 칸 또는 두 칸',2,['DP'],'''계단 n개를 올라갑니다. 한 번에 한 칸 또는 두 칸을 오를 수 있을 때, 꼭대기에 도착하는 방법의 수를 1,000,000,007로 나눈 나머지를 반환하세요. n=0일 때는 움직이지 않는 한 가지 방법이 있습니다.

제한 조건
• 0 ≤ n ≤ 100,000
• 방문 순서가 다르면 다른 방법입니다.''','int',[('int','n')],[([3],3),([4],5),([0],1),([1],1),([2],2),([10],89),([50],365010934)],
    [({'name':'stairs','n':100000},'대입력 경계 · n=100,000')])]

def check_value(value, kind):
    if kind.endswith('[]'):
        if not isinstance(value,list) or len(value)>200000: raise ValueError('배열 테스트 형식을 확인하세요.')
        for x in value: check_value(x,kind[:-2])
    elif kind=='String':
        if not isinstance(value,str) or len(value)>200000: raise ValueError('문자열 테스트 형식을 확인하세요.')
    elif kind=='boolean':
        if type(value) is not bool: raise ValueError('boolean 값은 true/false여야 합니다.')
    elif kind in {'int','long'}:
        limit=2**(31 if kind=='int' else 63)
        if type(value) is not int or not -limit<=value<limit: raise ValueError('정수 범위를 확인하세요.')
    elif kind=='double':
        import math
        if type(value) not in {float,int} or not math.isfinite(value): raise ValueError('유한한 실수가 필요합니다.')

def validate_problem(p):
    if not isinstance(p,dict):raise ValueError('문제 JSON 객체가 필요합니다.')
    p=copy.deepcopy(p)
    for key,limit in [('title',200),('description',40000),('starter',60000)]:
        if not isinstance(p.get(key),str) or not p[key].strip() or len(p[key])>limit:raise ValueError(key+' 입력을 확인하세요.')
    if p.get('returns') not in TYPES or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',p.get('method','')):raise ValueError('Java 반환형·메서드 이름을 확인하세요.')
    if not isinstance(p.get('params'),list) or not 0<=len(p['params'])<=10:raise ValueError('매개변수는 최대 10개입니다.')
    names=[]
    for param in p['params']:
        if not isinstance(param,dict) or param.get('type') not in TYPES or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',param.get('name','')):raise ValueError('매개변수 형식을 확인하세요.')
        names.append(param['name'])
    if len(set(names))!=len(names):raise ValueError('매개변수 이름이 중복됩니다.')
    if not isinstance(p.get('tests'),list) or not 1<=len(p['tests'])<=100:raise ValueError('테스트 1~100개가 필요합니다.')
    for i,test in enumerate(p['tests']):
        if not isinstance(test,dict):raise ValueError('테스트 형식을 확인하세요.')
        limit=test.get('time_limit_ms')
        if limit is not None and (type(limit) is not int or not 100<=limit<=10000):raise ValueError('테스트 시간 제한은 100~10000ms입니다.')
        if type(test.get('performance',False)) is not bool:raise ValueError('performance는 true/false입니다.')
        if 'generate' in test:
            # Generated inputs can be huge, so they are never shown as public examples.
            check_generate(test['generate']);args,expected=expand_test(test);label=test.get('label')
            test={'generate':test['generate'],'public':False}|({'label':label[:80]} if isinstance(label,str) and label else {})|({'time_limit_ms':limit} if limit else {})|({'performance':True} if test.get('performance') else {})
            p['tests'][i]=test
        else:
            args,expected=test.get('args'),test.get('expected')
            test['public']=bool(test.get('public',True))
        if not isinstance(args,list) or len(args)!=len(names):raise ValueError('테스트 인자 개수를 확인하세요.')
        for val,param in zip(args,p['params']):check_value(val,param['type'])
        check_value(expected,p['returns'])
    if not any(t['public'] for t in p['tests']):raise ValueError('공개 예제가 하나 이상 필요합니다.')
    if type(p.get('level')) is not int or not 0<=p['level']<=5:raise ValueError('난이도는 0~5입니다.')
    if not isinstance(p.get('tags'),list) or not 1<=len(p['tags'])<=8 or any(not isinstance(t,str) or len(t)>30 for t in p['tags']):raise ValueError('유형 태그를 확인하세요.')
    url=p.get('url','')
    if not isinstance(url,str) or (url and not url.startswith(('https://','http://'))):raise ValueError('문제 URL 형식을 확인하세요.')
    return {k:p[k] for k in ['title','description','starter','returns','method','params','tests','level','tags']}|{'id':'custom-'+uuid.uuid4().hex[:12],'url':url[:2000],'source':'사용자 등록 · 앱 테스트 기준'}

def public_problem(p, reveal=False):
    p=copy.deepcopy(p)
    p['test_count']=len(p['tests'])
    if not reveal:p['tests']=[t for t in p['tests'] if t['public']]
    return p
