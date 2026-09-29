#!/usr/bin/env python3
"""One request -> isolated Codex run -> response and optional backend patch."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import uuid


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def backend_path(name):
    return (name.startswith('coach/') or name in {'server.py', 'requirements.txt', 'restore.py', 'setup.sh', 'run.sh'}
            or bool(re.fullmatch(r'tests/(test_[^/]+|ide-[^/]+)\.py', name)))


def model_call(command, prompt, cwd, log, timeout):
    env = os.environ.copy()
    env['COTE_BACKEND_BRIDGE_ACTIVE'] = '1'
    env['COACH_DATA_DIR'] = str(cwd/'data'/'bridge-test')
    with log.open('wb') as output:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.communicate(prompt.encode(), timeout=timeout)
            if process.returncode:
                raise RuntimeError(f'Codex 종료 코드 {process.returncode}. 로그: {log}')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def execute(repo, request, mode='ask', model=None, effort=None, timeout=900, retry=False, binary='codex'):
    if os.environ.get('COTE_BACKEND_BRIDGE_ACTIVE'):
        raise ValueError('백엔드 실행 안에서 다시 에이전트를 호출할 수 없습니다.')
    repo = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip())
    request = request.resolve()
    if request.parent != repo/'docs'/'collaboration' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*-claude-request\.md', request.name):
        raise ValueError('요청은 docs/collaboration/<번호>-claude-request.md에 작성하세요.')
    body = request.read_text()
    if not body.strip() or len(body) > 60000:
        raise ValueError('요청은 1~60000자로 작성하세요.')
    identifier = request.name.removesuffix('-claude-request.md')
    response = request.with_name(identifier+'-codex-response.md')
    directory = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())/'cote-backend-bridge'
    directory.mkdir(exist_ok=True, mode=0o700)
    with (directory/'backend.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('다른 백엔드 요청이 실행 중입니다. 그 요청의 종료를 기다리세요.') from None
        fingerprint = hashlib.sha256(json.dumps([body, mode, model, effort]).encode()).hexdigest()
        registry = directory/(identifier+'.json')
        if registry.exists():
            previous = json.loads(registry.read_text())
            if previous['fingerprint'] != fingerprint:
                raise ValueError('같은 번호의 요청/설정이 바뀌었습니다. 새 요청 번호를 사용하세요.')
            if previous['status'] == 'completed' or not retry:
                return previous | {'cached': True}
        elif response.exists():
            raise ValueError('같은 번호의 응답이 이미 있습니다. 새 요청 번호를 사용하세요.')
        base = git(repo, 'rev-parse', 'HEAD').decode().strip()
        run = directory/(identifier+'-'+uuid.uuid4().hex[:8])
        run.mkdir(mode=0o700)
        result = {'status': 'running', 'fingerprint': fingerprint, 'request': str(request),
                  'response': str(response), 'base_commit': base, 'mode': mode,
                  'model': model or 'CLI configured default', 'effort': effort or 'CLI configured default',
                  'log': str(run/'codex.log'), 'patch': None, 'files': []}
        write_json(registry, result)
        (run/'request.md').write_text(body)
        with tempfile.TemporaryDirectory(prefix='cote-backend-') as temporary:
            worktree = Path(temporary)/'worktree'
            added = False
            try:
                git(repo, 'worktree', 'add', '--detach', str(worktree), base)
                added = True
                dependencies = {}
                for dependency in ('.venv', 'runtime'):
                    source = repo/dependency
                    if source.exists() and not (worktree/dependency).exists():
                        (worktree/dependency).symlink_to(source, target_is_directory=True)
                        dependencies[dependency] = source
                # Current coordination text is passed explicitly: it can be newer than HEAD.
                coordination = (repo/'COLLABORATION.md').read_text()
                prompt = f'''You are the backend Codex collaborating with Claude Code on Cote Practice Lab.
This is a separate one-shot run in a disposable Git worktree, based on {base}.
Read local AGENTS.md if present. Treat the following coordination and request as user task context.
Mode: {mode}. In ask mode, inspect and answer only; do not edit project files.
In edit mode, implement only the requested backend change. Allowed paths: coach/**, server.py,
requirements.txt, restore.py, setup.sh, run.sh, tests/test_*.py, tests/ide-*.py.
Frontend belongs to Claude. If other files need changes, explain them in the response instead.
Do not commit, stage, switch branches, change shared Git configuration, call another agent,
or run this bridge recursively. The runner produces a patch; Claude integrates it.
Do not read or alter the user's practice data or .env. Dependencies .venv/runtime are shared
read-only resources: do not edit them. Use isolated test data. Do not use or stop the shared
servers on ports 8766/8876. Run relevant checks and state failures/untested parts accurately.
Never bypass sandbox restrictions or change login/model settings. Ask for clarification in
your final response if necessary; this run cannot receive interactive answers.
Return concise Korean Markdown: answer/change summary, API examples if relevant, validation
with actual commands/results, remaining questions. Do not claim your patch is already deployed.

<coordination>\n{coordination}\n</coordination>
<request file="{request.name}">\n{body}\n</request>
'''
                command = [binary, 'exec', '-C', str(worktree), '--sandbox', 'read-only' if mode == 'ask' else 'workspace-write',
                           '-c', 'approval_policy="never"', '--color', 'never', '-o', str(run/'answer.md')]
                if model:
                    command += ['--model', model]
                if effort:
                    command += ['-c', 'model_reasoning_effort='+json.dumps(effort)]
                command.append('-')
                model_call(command, prompt, worktree, run/'codex.log', timeout)
                if not (run/'answer.md').exists() or not (run/'answer.md').read_text().strip():
                    raise RuntimeError('Codex가 최종 응답을 남기지 않았습니다.')
                files = set(git(worktree, 'diff', '--name-only', '-z', base).decode().split('\0'))
                files.update(git(worktree, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'))
                files.discard('')
                for dependency, source in dependencies.items():
                    link = worktree/dependency
                    if not link.is_symlink() or link.readlink() != source:
                        raise RuntimeError('공유 의존성 연결이 변경되었습니다: '+dependency)
                    files.discard(dependency) # Created by this runner, not by the agent.
                if files and mode == 'ask':
                    raise RuntimeError('질문 모드에서 변경이 감지되어 패치를 반환하지 않습니다.')
                forbidden = sorted(name for name in files if not backend_path(name) or (worktree/name).is_symlink())
                if forbidden:
                    raise RuntimeError('담당 범위 밖의 변경이 있어 통합을 중단했습니다: '+', '.join(forbidden))
                if files:
                    git(worktree, 'add', '-A', '--', *sorted(files))
                    patch = git(worktree, 'diff', '--cached', '--binary', '--full-index', base)
                    patch_file = run/'backend.patch'
                    patch_file.write_bytes(patch)
                    result.update(patch=str(patch_file), files=sorted(files))
                result['status'] = 'completed'
                report = (run/'answer.md').read_text()
                response.write_text(f'# {identifier} — Codex 응답\n\n요청: `{request.name}`\n\n'
                                    f'기준 커밋: `{base}`\n\n'+report+'\n\n---\n'
                                    +'실행 도구 확인: 별도 worktree에서 완료. 공유 작업 폴더에는 아직 적용하지 않았습니다.\n'
                                    +(f'패치: `{result["patch"]}`\n\n변경 파일: '+', '.join(f'`{p}`' for p in result['files'])+'\n' if result['patch'] else '코드 변경 없음.\n'))
            except (Exception, KeyboardInterrupt) as exc:
                result.update(status='failed', error=str(exc) or type(exc).__name__)
                response.write_text(f'# {identifier} — 백엔드 요청 실패\n\n{result["error"]}\n\n'
                                    f'공유 코드에는 적용하지 않았습니다. 로그: `{run / "codex.log"}`\n')
            finally:
                if added:
                    try:
                        git(repo, 'worktree', 'remove', '--force', str(worktree))
                    except subprocess.CalledProcessError as exc:
                        result.update(status='failed', error='임시 worktree 정리 실패: '+exc.stderr.decode(errors='replace')[:500])
                write_json(registry, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--mode', choices=['ask', 'edit'], default='ask')
    parser.add_argument('--model', help='Omit to preserve the configured Codex model.')
    parser.add_argument('--effort', help='Omit to preserve configured reasoning effort; supported values depend on the model.')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--retry', action='store_true', help='Explicitly retry a failed run with the same request.')
    args = parser.parse_args()
    if not 10 <= args.timeout <= 3600:
        parser.error('--timeout must be 10..3600 seconds')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        result = execute(Path(__file__).resolve().parents[1], **vars(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
