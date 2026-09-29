#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for dependency in python3 node npm docker curl tar; do
  command -v "$dependency" >/dev/null || { echo "필요한 도구: $dependency" >&2; exit 1; }
done
docker info >/dev/null
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
docker pull eclipse-temurin:21-jdk
mkdir -p runtime/jdtls
if [[ ! -x runtime/jdk21/bin/java ]]; then
  container=$(docker create eclipse-temurin:21-jdk)
  trap 'docker rm "$container" >/dev/null 2>&1 || true' EXIT
  docker cp "$container:/opt/java/openjdk" runtime/jdk21
  docker rm "$container" >/dev/null
  trap - EXIT
fi
if [[ ! -f runtime/jdtls/plugins/org.eclipse.equinox.launcher_1.8.0.v20260804-1928.jar ]]; then
  archive=$(mktemp)
  curl -fsSL https://download.eclipse.org/jdtls/snapshots/jdt-language-server-1.62.0-202609282153.tar.gz -o "$archive"
  tar -xzf "$archive" -C runtime/jdtls
  rm "$archive"
fi
if [[ ! -x runtime/jdk25/bin/java ]]; then
  docker pull eclipse-temurin:25-jdk
  container=$(docker create eclipse-temurin:25-jdk)
  trap 'docker rm "$container" >/dev/null 2>&1 || true' EXIT
  docker cp "$container:/opt/java/openjdk" runtime/jdk25
  docker rm "$container" >/dev/null
  trap - EXIT
fi
.venv/bin/python - <<'PY'
from faster_whisper import WhisperModel
WhisperModel('small',device='cpu',compute_type='int8',download_root='runtime/whisper',cpu_threads=8)
print('로컬 한국어 음성 전사 모델 준비 완료')
PY
if command -v codex >/dev/null; then codex login status; else echo 'Codex CLI를 설치하고 codex login으로 ChatGPT 계정에 로그인하세요.'; fi
printf '\n준비 완료. ./run.sh → http://localhost:8766\n'
