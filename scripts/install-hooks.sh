#!/usr/bin/env bash
# install-hooks.sh — point this clone's git hooks at the tracked .githooks/
# directory. Run once after cloning:
#
#   ./scripts/install-hooks.sh
#
# This sets core.hooksPath so the version-controlled hooks in .githooks/ are
# used instead of the (empty) default .git/hooks/. Safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

chmod +x .githooks/* 2>/dev/null || true
git config core.hooksPath .githooks

echo "Installed git hooks: core.hooksPath = $(git config core.hooksPath)"
echo "The pre-commit hook runs the pysilverline API tests (Tuya v3.3 + v3.4 + v3.5)."
echo "Bypass a single commit with:  git commit --no-verify"
