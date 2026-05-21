#!/usr/bin/env bash
set -euo pipefail

# 定时推送脚本
# - 只在有变更时提交
# - 通过环境变量覆盖仓库 / 分支 / 提交前缀

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_name="${GIT_REMOTE_NAME:-origin}"
branch_name="${GIT_BRANCH_NAME:-master}"
commit_prefix="${GIT_COMMIT_PREFIX:-auto-sync}"

cd "$repo_dir"

if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -m "${commit_prefix}: $(date '+%Y-%m-%d %H:%M:%S')"
  git push "${remote_name}" "${branch_name}"
else
  echo "No changes to commit."
fi
