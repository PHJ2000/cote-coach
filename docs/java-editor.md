# Java 편집 지원: 조사와 구현

2026-09-29. Java 21 코딩테스트의 `Solution.java` 편집을 대상으로 한다. 추천 내용은 Eclipse JDT LS가 타입·스코프를 분석하고, Monaco가 표시와 삽입을 담당한다. 전체 IntelliJ/VS Code 기능을 복제한 것은 아니다.

## 확인한 자료

| 자료 | 반영한 내용 |
| --- | --- |
| [Eclipse JDT LS](https://github.com/eclipse-jdtls/eclipse.jdt.ls), `CompletionHandler`, `CompletionResolveHandler`, `CompletionProposalReplacementProvider`, `SnippetCompletionProposal` | Java 의미 분석, 오버로드·제네릭·접근 범위, 메서드 인자와 문맥별 코드 틀, import 편집, 문서 조회 |
| [Red Hat의 VS Code Java 확장](https://github.com/redhat-developer/vscode-java/blob/master/package.json) | 메서드 인자 이름 삽입, 내부 JDK 타입 제외, 추천 개수·대소문자·문서 설정 확인 |
| [LSP 3.17 completion 규격](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_completion) | snippetSupport, insert/replace 범위, additionalTextEdits, 미완성 목록, 지연 문서 조회, itemDefaults 변환 |
| [Java SE 21 JLS §6.3](https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.3) | 지역 변수·매개변수·타입의 유효 범위에 맞춘 검증 |
| [IntelliJ IDEA 자동완성 안내](https://www.jetbrains.com/help/idea/auto-completing-code.html), [Eclipse code assist](https://help.eclipse.org/latest/topic/org.eclipse.jdt.doc.user/reference/ref-java-editor-code-assist.htm) | 문맥에 맞는 추천, 명시적 호출, 선택·삽입 흐름 참고 |
| [JDT 프로젝트 가져오기](https://github.com/eclipse-jdtls/eclipse.jdt.ls/blob/main/org.eclipse.jdt.ls.core/src/org/eclipse/jdt/ls/core/internal/managers/ProjectsManager.java), [문서 생명주기](https://github.com/eclipse-jdtls/eclipse.jdt.ls/blob/main/org.eclipse.jdt.ls.core/src/org/eclipse/jdt/ls/core/internal/handlers/JDTLanguageServer.java) | ProjectsImported 알림으로 준비 완료 확인, java.lsp.joinOnCompletion으로 편집 반영 후 추천 계산 |

조사한 upstream main/master 문서는 이후 바뀔 수 있다. 설치 스크립트는 JDT LS `1.62.0-202609282153` 아카이브를 지정한다. 서버 실행은 JDK 25, 분석 대상과 실제 채점은 Java 21이다.

## 구현한 동작

- 지역 변수·매개변수·필드·메서드·오버로드·상속·배열·제네릭·람다의 추천을 JDT에 맡긴다. 임의의 메서드 목록을 프런트에 하드코딩하지 않는다.
- 인자 이름이 선택된 메서드 호출을 삽입한다. Tab으로 인자/마지막 위치로 이동한다. 인자를 문자열로 바꿀 때 따옴표가 중복되지 않게 선택 영역의 자동 따옴표 감싸기를 제한했다.
- 타입 선택 시 JDT의 추가 import 편집을 전체 적용한다. 삭제만 하는 편집도 임의로 버리지 않는다. 기존 import를 보존하는지는 실제 삽입 코드로 검증한다.
- 단어 중간에서도 JDT의 insert/replace 범위를 유지하며 기본 선택은 replace다. 예를 들어 `sub|string`에서 완성해도 `substringstring`으로 중복되지 않는다.
- JDT의 `for`, `try` 같은 문맥별 코드 틀을 활성화했다. `prsf`, `psf`, `psfi`, `psfs`, `psvm`은 별도의 소규모 약어 편의 기능이다. 이 약어 판별기는 Java 전체 문법 분석기가 아니며, 메서드·주석·문자열 안에는 제안하지 않는다.
- 추천 상세 문서는 선택한 항목만 조회한다. JDT의 추천 캐시가 만료되면 기존 항목을 유지한다. 문서는 명령 실행/HTML을 허용하지 않는 Markdown으로 표시한다.
- 매개변수 안내, 마우스 hover, 오류 표시를 연결했다. 실제 실행 결과의 기준은 Docker의 javac와 테스트다.
- 문제마다 Monaco 모델·되돌리기 이력·커서 위치를 분리한다. 전환 후 편집기로 포커스를 돌린다.
- 문제/세션별 Java 프로젝트를 준비한 뒤 파일을 연다. 서로 같은 이름의 `Solution.java`가 JDT의 기본 임시 프로젝트에 섞이는 문제를 막았다. 파일을 매 요청마다 다시 열거나 디스크와 작업 버퍼를 동시에 덮어쓰지 않는다.
- 빠른 타이핑 중 기존 추천 응답이 늦으면 최신 문서로 재계산한다. 취소되거나 다른 문제의 응답은 표시하지 않는다. 문서 버전이 그대로면 불필요한 didChange를 보내지 않는다.

훈련 모드에서 Ctrl+Space로 목록을 호출하고 Tab으로 선택한다. Enter는 줄바꿈이다. 실전 모드에서는 의미 분석 기반 자동완성을 제공하지 않는다.

## 검증 범위

실제 JDT LS를 사용하며 사용자 DB·기록을 건드리지 않는 `tests/lab_fixture.py`에서 실행한다. 코치 추론만 모의 처리한다.

```bash
npm run build
.venv/bin/python tests/lab_fixture.py  # 별도 터미널
.venv/bin/python tests/ide-matrix.py
node tests/completion-protocol.cjs
node tests/java-completion-browser.cjs
node tests/java-editor-matrix.cjs
node tests/workspace-ux.cjs
node tests/lab-browser.cjs
.venv/bin/python -m unittest discover -s tests -v
```

- `ide-matrix.py`: 32개 시나리오. 지역 변수/매개변수, 메서드 체인, 배열, 중첩 제네릭, 오버로드, private 접근 제한, 중첩 클래스, static/enum, UTF-16 이모지 위치, 람다, 코드 틀, import/생성자, 단어 중간, 여러 프로젝트 전환, 문서/시그니처/hover, 오류 발생과 해소.
- `completion-protocol.cjs`: 규격 데이터 변환, insert/replace, 빈 문자열 추가 편집, 문서 신뢰 설정, 약어의 주석/문자열/텍스트 블록/람다 문맥.
- `java-completion-browser.cjs`: 자동 표시, 클래스 키워드 우선순위, 약어 삽입, 메서드 본문에서 멤버용 제안 제외, CSS 정렬.
- `java-editor-matrix.cjs`: 실제 키보드와 붙여넣기로 import·인자·Tab 이동을 거쳐 코드 작성 후 Docker 컴파일/실행. 단어 중간 완성, 문제별 커서·되돌리기, 주석에서의 약어 제외. 실제 JDT 응답 전달을 지연시키고 추가 타이핑했을 때 최신 입력의 추천이 나타나는지도 확인한다.
- `workspace-ux.cjs`: 지연된 응답과 문제 전환, 힌트·음성 UI 회귀. 지연 응답과 음성 인식 결과는 모의 처리한다.
- `lab-browser.cjs`: 녹화·실행·제출·힌트·전사·복기·보고서의 전체 흐름. 합성 미디어 입력을 사용한다.

Monaco 0.55.1의 모델 전환 시 단어 강조 작업이 취소 오류를 내는 [공개 이슈 #5294](https://github.com/microsoft/monaco-editor/issues/5294)와 같은 스택을 재현했다. 사용하지 않는 의미 기반 단어 강조를 끄고 문제별 모델은 유지했다. 텍스트 선택 강조·문법 색상·추천 기능은 유지한다.

최종 결과: 의미 분석 32/32, 프로토콜 검사, Java 추천/삽입 브라우저 검사, 워크스페이스 UX 검사, 전체 연습 흐름 검사, 기존 Python 테스트 26/26 통과.

## 남는 범위

완전한 IDE와 동등하다고 보장하지 않는다. 외부 라이브러리, Maven/Gradle 프로젝트, 여러 소스 파일 편집, 리팩터링·디버거·IntelliJ 전체 live template은 구현 범위 밖이다. Java 서버 초기화와 기본 JDK 인덱싱은 앱 실행 단계에서 수행한다. 새 훈련 세션의 문제별 프로젝트 준비는 풀이 타이머 시작 전에 수행한다. JDT가 제공하지 않는 항목이나 소스/Javadoc이 없는 라이브러리의 설명을 별도로 만들어 내지 않는다. 위 시나리오 통과는 모든 Java 코드와 입력 속도 조합에 대한 보증이 아니다.


## 첫 추천 지연 개선

서버 실행 시 `JavaIDE.warmup()`으로 문자열 메서드와 타입 검색까지 미리 실행한다. 훈련 시작은 선택한 문제들의 프로젝트/문서를 준비한 다음 시작 시각을 정한다. 시작 버튼은 준비 중 상태를 표시하며, 실전 세션은 문제별 IDE 준비를 건너뛴다. 준비 실패 시에도 기본 편집·기록은 가능하고 안내를 표시한다.

`tests/ide-startup.py`는 서로 분리된 임시 JDT 워크스페이스에서 실제 응답을 비교한다. 이 컴퓨터의 1회 측정은 준비 없는 첫 추천 4,310ms, 앱 실행 준비 4,284ms, 세션 준비 68ms, 준비 후 첫 추천 54ms였다. 서버 응답 측정이며 브라우저 표시 지연을 포함하지 않는다. 초기화 작업 자체를 없앤 것이 아니라 풀이 시작 전으로 옮긴 결과다.

28개 Python 테스트에서 준비 시간이 풀이 시간에 포함되지 않는지, 실전 모드는 준비를 건너뛰는지, 준비 실패 후에도 기본 연습이 가능한지를 포함해 검증한다. 이미 실행 중인 서버에는 프로세스를 재시작해야 적용된다.
