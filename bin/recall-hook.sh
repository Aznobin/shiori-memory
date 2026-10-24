#!/bin/sh
# memory-recall hook launcher (Linux / macOS)
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOL="$HERE/../tools/recall.py"
export PYTHONIOENCODING=utf-8

for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    exec "$c" "$TOOL" hook --host codex "$@"
  fi
done
exit 0
