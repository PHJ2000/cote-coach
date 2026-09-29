"""Eclipse JDT LS: actual Java completion, signature help and diagnostics."""
import os
import copy
import uuid
from collections import OrderedDict
import threading
from pathlib import Path
from urllib.parse import urlparse, unquote
from .rpc import RPC

class JavaIDE:
    def __init__(self,root,data):
        self.projects=set();self.projects_ready=threading.Condition()
        self.root=root;self.data=data;self.rpc=None;self.lock=threading.RLock();self.documents={};self.document_codes={};self.diagnostics={};self.diagnostics_ready=threading.Condition();self.completions=OrderedDict();self.queue_lock=threading.Lock();self.latest={}
    def warmup(self):
        # Exercise completion too: initialize alone leaves JDK indexing lazy.
        code='class Solution { void warmup(String value) { value.sub } }'
        self.request('_editor_warmup',code,'completion',0,code.index('sub')+3)
        code='class Solution { ArrayL }'
        self.request('_editor_warmup',code,'completion',0,code.index('ArrayL')+6)
    def prepare(self,key,code):
        return self.request(key,code,'prepare')
    def start(self):
        if self.rpc and self.rpc.proc.poll() is None:return
        home=self.root/'runtime/jdtls';jars=list((home/'plugins').glob('org.eclipse.equinox.launcher_*.jar'))
        if not jars:raise ValueError('Java 자동완성 서버가 설치되지 않았습니다. setup.sh를 실행하세요.')
        workspace=self.data/'java-workspace';workspace.mkdir(parents=True,exist_ok=True)
        cmd=[str(self.root/'runtime/jdk25/bin/java'),'-Djava.lsp.joinOnCompletion=true','-Declipse.application=org.eclipse.jdt.ls.core.id1','-Dosgi.bundles.defaultStartLevel=4','-Declipse.product=org.eclipse.jdt.ls.core.product','-Xmx1G','--add-modules=ALL-SYSTEM','--add-opens','java.base/java.util=ALL-UNNAMED','--add-opens','java.base/java.lang=ALL-UNNAMED','-jar',str(jars[0]),'-configuration',str(home/'config_linux'),'-data',str(self.data/'jdt-state')]
        self.projects.clear()
        self.rpc=RPC(cmd,self.data/'jdt.log',framed=True,cwd=str(workspace))
        self.rpc.on_event=self.event
        self.settings={'java':{
            'configuration':{'runtimes':[{'name':'JavaSE-21','path':str(self.root/'runtime/jdk21'),'default':True}]},
            'autobuild':{'enabled':False},'import':{'gradle':{'enabled':False},'maven':{'enabled':False}},
            'completion':{'enabled':True,'guessMethodArguments':'insertParameterNames','matchCase':'firstLetter',
                          'lazyResolveTextEdit':{'enabled':False},'postfix':{'enabled':True},'maxResults':150,
                          'filteredTypes':['java.awt.*','com.sun.*','sun.*','jdk.*','org.graalvm.*'],
                          'favoriteStaticMembers':[],'importOrder':['#','java','javax','org','com','']},
            'signatureHelp':{'enabled':True,'description':{'enabled':True}}
        }}
        capabilities={'textDocument':{
            'completion':{'contextSupport':True,'completionItem':{
                'snippetSupport':True,'insertReplaceSupport':True,'labelDetailsSupport':True,
                'documentationFormat':['markdown','plaintext'],'deprecatedSupport':True,
                'tagSupport':{'valueSet':[1]},'resolveSupport':{'properties':['documentation','detail']}}},
            'signatureHelp':{'signatureInformation':{'documentationFormat':['markdown','plaintext'],
                'parameterInformation':{'labelOffsetSupport':True},'activeParameterSupport':True}},
            'hover':{'contentFormat':['markdown','plaintext']},
            'publishDiagnostics':{'versionSupport':True}},'workspace':{'configuration':False}}
        self.rpc.call('initialize',{'processId':os.getpid(),'rootUri':workspace.as_uri(),
            'workspaceFolders':[{'uri':workspace.as_uri(),'name':'Practice'}],
            'capabilities':capabilities,'initializationOptions':{'settings':self.settings}},timeout=90)
        self.rpc.notify('initialized',{})
        self.rpc.notify('workspace/didChangeConfiguration',{'settings':self.settings})
        self.documents={};self.document_codes={};self.diagnostics={};self.completions.clear()
    def event(self,method,params):
        if method=='language/eventNotification' and params.get('eventType') in (200,'ProjectsImported'):
            with self.projects_ready:
                self.projects.update(Path(unquote(urlparse(uri).path)).as_uri() for uri in params.get('data',[]) if urlparse(uri).scheme=='file')
                self.projects_ready.notify_all()
        if method=='textDocument/publishDiagnostics':
            with self.diagnostics_ready:
                self.diagnostics[params['uri']]=params
                self.diagnostics_ready.notify_all()
    def request(self,key,code,op,line=0,character=0,context=None,resolve_id=None):
        ticket=uuid.uuid4().hex
        with self.queue_lock:self.latest[(key,op)]=ticket
        with self.lock:
            if self.latest.get((key,op))!=ticket:return None
            self.start(); folder=self.data/'java-workspace'/key/'src';folder.mkdir(parents=True,exist_ok=True)
            project=folder.parent
            if not (project/'.project').exists():
                (project/'.project').write_text('<projectDescription><name>'+key+'</name><buildSpec><buildCommand><name>org.eclipse.jdt.core.javabuilder</name></buildCommand></buildSpec><natures><nature>org.eclipse.jdt.core.javanature</nature></natures></projectDescription>')
                prefs=project/'.settings';prefs.mkdir(exist_ok=True);(prefs/'org.eclipse.jdt.core.prefs').write_text('eclipse.preferences.version=1\norg.eclipse.jdt.core.compiler.codegen.targetPlatform=21\norg.eclipse.jdt.core.compiler.compliance=21\norg.eclipse.jdt.core.compiler.source=21\n')
                (project/'.classpath').write_text('<classpath><classpathentry kind="src" path="src"/><classpathentry kind="con" path="org.eclipse.jdt.launching.JRE_CONTAINER/org.eclipse.jdt.internal.debug.ui.launcher.StandardVMType/JavaSE-21"/><classpathentry kind="output" path="bin"/></classpath>')
            source=folder/'Solution.java';uri=source.as_uri()
            if op=='resolve':
                cached=self.completions.get(resolve_id)
                if not cached or cached['key']!=key or cached['code']!=code or self.document_codes.get(uri)!=code:return None
                try:return self.rpc.call('completionItem/resolve',copy.deepcopy(cached['item']),timeout=8)
                except RuntimeError:return cached['item'] # A newer completion request can expire JDT's proposal cache.
            if project.as_uri() not in self.projects:
                # Import before didOpen: unmanaged Solution.java files otherwise share JDT's fallback project.
                source.write_text(code)
                self.rpc.call('workspace/executeCommand',{'command':'java.project.import','arguments':[]},timeout=30)
                with self.projects_ready:
                    if not self.projects_ready.wait_for(lambda:project.as_uri() in self.projects,timeout=30):
                        raise RuntimeError('Java 프로젝트 준비 시간이 초과되었습니다. 다시 시도해 주세요.')
            changed=self.document_codes.get(uri)!=code
            if changed:
                version=self.documents.get(uri,0)+1;self.documents[uri]=version;self.document_codes[uri]=code
                with self.diagnostics_ready:self.diagnostics.pop(uri,None)
                if version==1:
                    source.write_text(code)
                    self.rpc.notify('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':'java','version':version,'text':code}})
                else:self.rpc.notify('textDocument/didChange',{'textDocument':{'uri':uri,'version':version},'contentChanges':[{'text':code}]})
            version=self.documents[uri]
            params={'textDocument':{'uri':uri},'position':{'line':line,'character':character}}
            if op=='completion':
                if context:params['context']=context
                response=self.rpc.call('textDocument/completion',params,timeout=20)
                result={'items':response,'isIncomplete':False} if isinstance(response,list) else (response or {'items':[]})
                for item in result.get('items',[]):
                    handle=uuid.uuid4().hex
                    self.completions[handle]={'key':key,'code':code,'item':copy.deepcopy(item)}
                    item['_resolve_id']=handle
                while len(self.completions)>600:self.completions.popitem(last=False)
                return result
            if op=='signature':return self.rpc.call('textDocument/signatureHelp',params,timeout=15)
            if op=='hover':return self.rpc.call('textDocument/hover',params,timeout=15)
            self.rpc.call('textDocument/documentSymbol',{'textDocument':{'uri':uri}},timeout=15)
            if op=='prepare':return {'ready':True}
            with self.diagnostics_ready:
                self.diagnostics_ready.wait_for(lambda:uri in self.diagnostics and self.diagnostics[uri].get('version',version)==version,timeout=1)
                result=self.diagnostics.get(uri,{})
                return {'diagnostics':result.get('diagnostics',[]) if result.get('version',version)==version else [],'version':version}
