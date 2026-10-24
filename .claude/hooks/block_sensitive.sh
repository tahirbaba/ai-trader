#!/usr/bin/env bash
set -e
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file_path',''))" 2>/dev/null || true)
PROTECTED=(".env" "config/risk.yaml" "config/live_approval.yaml")
for p in "${PROTECTED[@]}"; do
  if [[ "$FILE_PATH" == *"$p" ]]; then
    echo "BLOCKED: '$p' is a protected file. Requires explicit owner instruction." >&2
    exit 1
  fi
done
