#!/usr/bin/env bash
# 用法：bash resume.sh <名字> <上一轮标签> <新标签> <反馈文件>   例：bash resume.sh luna luna luna-r2 feedback-luna.md
# 先把该执行者上一轮的改动从 stash 放回工作区，再接着它原来的对话发反馈；结束后同样存补丁 + stash。
set -u
ROOT="C:/YUFiles/DFiles/Self-Knowledge/Design/2026-08-26-movieDB"
DIR="$ROOT/.planning/2026-11-14-island-buttons"
CODEX="/c/Users/18364/AppData/Local/OpenAI/Codex/bin/9e1da79431da526b/codex.exe"
NAME="$1"; PREV="$2"; TAG="$3"; MSG="$4"
cd "$ROOT"
SCOPE="app.py static templates viewmodels services queries models connectors tests docs scripts"

if [ -n "$(git status --porcelain -- $SCOPE)" ]; then echo "工作区不干净，停止"; git status --short; exit 1; fi
REF=$(git stash list | grep -m1 "island-buttons: $PREV$" | cut -d: -f1)
[ -z "$REF" ] && { echo "找不到 stash：$PREV"; exit 1; }
git stash pop "$REF" >/dev/null || exit 1

case "$NAME" in
  luna|sol|terra)
    case "$NAME" in luna) M=gpt-6-luna; E=max;; sol) M=gpt-6.1-sol; E=high;; terra) M=gpt-6.1-terra; E=high;; esac
    ID=$(head -1 "$DIR/$NAME.jsonl" | sed -E 's/.*"thread_id":"([^"]+)".*/\1/')
    "$CODEX" exec resume -m "$M" -c model_reasoning_effort="\"$E\"" -c sandbox_mode='"workspace-write"' --json \
      -o "$DIR/$TAG.last.md" "$ID" - < "$MSG" > "$DIR/$TAG.jsonl" 2> "$DIR/$TAG.err"
    ;;
  gemini)
    ID=$(python -X utf8 -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));print(d.get('conversation_id') or d.get('conversationId') or d.get('session_id') or '')" "$DIR/gemini.json")
    agy -p "$(cat "$MSG")" --conversation "$ID" --model gemini-3.8-flash-high --mode accept-edits \
      --dangerously-skip-permissions --output-format json > "$DIR/$TAG.json" 2> "$DIR/$TAG.err"
    ;;
esac
echo "退出码 $?"

git stash push -u -m "island-buttons: $TAG" -- $SCOPE >/dev/null 2>&1 && \
  git stash show -p --include-untracked stash@{0} > "$DIR/$TAG.patch"
echo "补丁 $TAG：$(wc -l < "$DIR/$TAG.patch" 2>/dev/null || echo 0) 行"
git status --short
