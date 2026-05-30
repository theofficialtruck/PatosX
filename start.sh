#!/usr/bin/env bash
# PatosX, a multipurpose Discord bot (moderation, economy, AI, fun)
# Copyright (C) 2025 theofficialtruck
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

set -euo pipefail

if [[ -z "${1:-}" ]]; then
  echo "[!] Error: No branch specified."
  echo "Usage: ./start.sh <branch> [--force]"
  echo "Example: ./start.sh main"
  exit 1
fi

BRANCH="$1"
FORCE="${2:-}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[!] Error: $PROJECT_DIR is not a git repository."
  echo "    Clone the repo here first, then rerun this script."
  exit 1
fi

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "[!] Error: .env is tracked by git. Remove it from the repo to keep secrets local:"
  echo "    git rm --cached .env && git commit -m 'Stop tracking .env'"
  exit 1
fi

echo "[!] Fetching origin/$BRANCH ..."
git fetch origin "$BRANCH" --prune

if ! git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
  echo "[!] Error: Remote branch origin/$BRANCH not found."
  exit 1
fi

if [[ "$FORCE" == "--force" ]]; then
  echo "[!] Forcing working tree to match origin/$BRANCH (discarding local commits on current branch)..."
  git reset --hard "origin/$BRANCH"
else
  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  echo "[!] Merging origin/$BRANCH into $CURRENT_BRANCH (fast-forward only)..."
  if ! git merge --ff-only "origin/$BRANCH"; then
    echo "[!] Fast-forward merge failed (history diverged)."
    echo "    Resolve manually, or rerun with: ./start.sh $BRANCH --force"
    exit 1
  fi
fi

if [[ ! -f .env ]]; then
  echo "[!] Warning: .env not found in $PROJECT_DIR"
  echo "    Create it locally (it should stay untracked from git)."
fi

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if [[ ! -d venv ]]; then
  "$PYTHON_BIN" -m venv venv
fi

# shellcheck source=/dev/null
source venv/bin/activate

python -m pip install --upgrade pip
if [[ -f requirements.txt ]]; then
  pip install -r requirements.txt
fi

python main.py
