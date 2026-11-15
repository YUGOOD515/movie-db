#!/usr/bin/env bash
# 用法：bash run.sh <名字>   名字：luna | sol | terra | gemini
# 在干净的工作区上跑一轮执行者，结束后把改动存成补丁 + git stash，工作区恢复干净。
set -u
ROOT="C:/YUFiles/DFiles/Self-Knowledge/Design/2026-08-26-movieDB"
DIR="$ROOT/.planning/2026-11-14-island-buttons"
CODEX="/c/Users/18364/AppData/Local/OpenAI/Codex/bin/9e1da79431da526b/codex.exe"
NAME="$1"
cd "$ROOT"
SCOPE="app.py static templates viewmodels services queries models connectors tests docs scripts"

if [ -n "$(git status --porcelain -- $SCOPE)" ]; then echo "工作区不干净，停止"; git status --short; exit 1; fi

case "$NAME" in
  luna|sol|terra)
    case "$NAME" in luna) M=gpt-6-luna; E=max;; sol) M=gpt-6.1-sol; E=high;; terra) M=gpt-6.1-terra; E=high;; esac
    "$CODEX" exec -m "$M" -c model_reasoning_effort="\"$E\"" -s workspace-write --json \
      -o "$DIR/$NAME.last.md" - < "$DIR/prompt.md" > "$DIR/$NAME.jsonl" 2> "$DIR/$NAME.err"
    ;;
  gemini)
    agy -p "$(cat "$DIR/prompt.md")" --model gemini-3.8-flash-high --mode accept-edits \
      --dangerously-skip-permissions --output-format json > "$DIR/$NAME.json" 2> "$DIR/$NAME.err"
    ;;
  *) echo "未知名字"; exit 1;;
esac
echo "退出码 $?"

git stash push -u -m "island-buttons: $NAME" -- $SCOPE >/dev/null 2>&1 && \
  git stash show -p --include-untracked stash@{0} > "$DIR/$NAME.patch"
echo "补丁：$(wc -l < "$DIR/$NAME.patch" 2>/dev/null || echo 0) 行"
git status --short
