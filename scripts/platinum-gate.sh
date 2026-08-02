#!/usr/bin/env bash
# platinum-gate.sh — the single, provable source of truth for "is this Platinum yet?"
#
# Every check below is a command with an exit code. There are no opinions in here.
# The loop is only allowed to STOP when this script exits 0 *and* the reviewer
# panel signs off. This script is a GUARD FILE: it must never be weakened to make
# the loop pass (see HARD RULES in PLATINUM_LOOP.md). The review-auditor diffs it
# every round.
#
# Usage:
#   scripts/platinum-gate.sh            # run everything, collect all failures
#   PLATINUM_COV_INTEGRATION=100 \
#   PLATINUM_COV_LIB=100 scripts/platinum-gate.sh   # dial polish coverage up
#
# Exit: 0 = all hard checks pass. 1 = at least one failed. SKIPPED checks (e.g.
# hassfest when no runner is available locally) are reported as SKIPPED and never
# count as a pass — they must be confirmed green in CI before DONE is declared.

set -uo pipefail

# ---- config (floors, not ceilings) ----------------------------------------
# 95% is the official Quality-Scale floor (Silver `test-coverage`). It is the
# PROVABLE bar. Raise via env for polish; the gate never drops below 95.
COV_INT="${PLATINUM_COV_INTEGRATION:-95}"
COV_LIB="${PLATINUM_COV_LIB:-95}"
if (( COV_INT < 95 )); then COV_INT=95; fi
if (( COV_LIB < 95 )); then COV_LIB=95; fi

INTEGRATION="custom_components/poolex_silverline"
INT_TESTS="tests"
LIB_DIR="pysilverline"

# ---- bookkeeping ------------------------------------------------------------
PASS=0; FAIL=0; SKIP=0
declare -a FAILED=()
declare -a SKIPPED=()

hr() { printf '%.0s─' {1..72}; echo; }
run() { # run "<label>" cmd args...
  local label="$1"; shift
  hr; echo "▶ ${label}"; hr
  if "$@"; then
    echo "✔ PASS: ${label}"; ((PASS++))
  else
    echo "✗ FAIL: ${label}"; ((FAIL++)); FAILED+=("${label}")
  fi
}
skip() { local label="$1"; local why="$2"
  hr; echo "↷ SKIP: ${label} — ${why}"; ((SKIP++)); SKIPPED+=("${label}"); }

have() { command -v "$1" >/dev/null 2>&1; }

# ---- 1. lint & format -------------------------------------------------------
RUFF_VERSION="0.16.1"
if have uvx; then
  RUFF=(uvx --from "ruff==${RUFF_VERSION}" ruff)
elif have ruff && [[ "$(ruff --version)" == "ruff ${RUFF_VERSION}" ]]; then
  RUFF=(ruff)
else
  RUFF=()
fi

if ((${#RUFF[@]})); then
  run "ruff ${RUFF_VERSION} lint" "${RUFF[@]}" check "${INTEGRATION}" "${LIB_DIR}/src" "${INT_TESTS}" "${LIB_DIR}/tests"
  run "ruff ${RUFF_VERSION} format check" "${RUFF[@]}" format --check "${INTEGRATION}" "${LIB_DIR}/src" "${INT_TESTS}" "${LIB_DIR}/tests"
else
  skip "ruff" "install uvx or ruff==${RUFF_VERSION}"
fi

# ---- 2. strict typing (Platinum: strict-typing) -----------------------------
# Both the integration AND the library must be mypy --strict clean. The strict
# flags live in each pyproject's [tool.mypy]; this gate also verifies they are
# actually set so nobody can pass by quietly relaxing them.
if have mypy; then
  run "mypy strict — integration" mypy "${INTEGRATION}"
  run "mypy strict — library"     bash -c "cd '${LIB_DIR}' && mypy src"
else
  skip "mypy" "mypy not installed (pip install mypy homeassistant)"
fi

run "mypy strict flag is set (integration)" \
  python - "$PWD/pyproject.toml" <<'PY'
import sys, tomllib, pathlib
cfg = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
strict = cfg.get("tool", {}).get("mypy", {}).get("strict")
sys.exit(0 if strict is True else 1)
PY

run "mypy strict flag is set (library)" \
  python - "$PWD/${LIB_DIR}/pyproject.toml" <<'PY'
import sys, tomllib, pathlib
cfg = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
strict = cfg.get("tool", {}).get("mypy", {}).get("strict")
sys.exit(0 if strict is True else 1)
PY

# ---- 3. library is typed-package shippable (Platinum: strict-typing) --------
run "library ships py.typed" test -f "${LIB_DIR}/src/pysilverline/py.typed"

# ---- 4. tests + coverage ----------------------------------------------------
if have pytest; then
  run "pytest — integration (cov ≥ ${COV_INT}%)" \
    pytest "${INT_TESTS}/" \
      --cov="${INTEGRATION//\//.}" \
      --cov-report=term-missing \
      --cov-fail-under="${COV_INT}" -q
  run "pytest — library (cov ≥ ${COV_LIB}%)" \
    bash -c "cd '${LIB_DIR}' && pytest tests/ --cov=pysilverline --cov-fail-under='${COV_LIB}' -q"
else
  skip "pytest" "pytest not installed"
fi

# ---- 5. no coverage theater (anti-gaming) -----------------------------------
# New skip/xfail/no-cover markers are a classic way to fake coverage. The gate
# fails if any exist without an inline justification comment on the same line.
run "no unjustified skip/xfail/pragma-no-cover" \
  python - "${INTEGRATION}" "${INT_TESTS}" "${LIB_DIR}/src" "${LIB_DIR}/tests" <<'PY'
import re, sys, pathlib
pat = re.compile(r'(@pytest\.mark\.(skip|xfail)|pragma:\s*no cover|# *type: *ignore\b(?!\[))')
bad = []
for root in sys.argv[1:]:
    for f in pathlib.Path(root).rglob("*.py"):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            m = pat.search(line)
            if not m:
                continue
            # justified == has a reason after a '#' that isn't just the marker,
            # or a specific ignore code like [arg-type]. Blanket ones fail.
            if "# type: ignore" in line and "[" in line:
                continue
            tail = line.split("#", 2)
            justified = len(tail) >= 3 and tail[2].strip()
            reason = "reason=" in line or "comment=" in line
            if not (justified or reason):
                bad.append(f"{f}:{i}: {line.strip()}")
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
PY

# ---- 6. quality_scale.yaml + manifest honesty (structural) ------------------
# Provable structural floor for "I claimed Platinum": manifest tier == platinum,
# every rule is done|exempt, every exempt carries a non-empty comment, no todo.
run "quality_scale + manifest are internally honest" \
  python - "${INTEGRATION}" <<'PY'
import json, sys, pathlib
try:
    import yaml
except ModuleNotFoundError:
    print("pyyaml missing (pip install pyyaml)"); sys.exit(1)

comp = pathlib.Path(sys.argv[1])
manifest = json.loads((comp / "manifest.json").read_text())
qs = yaml.safe_load((comp / "quality_scale.yaml").read_text())["rules"]

errs = []
if manifest.get("quality_scale") != "platinum":
    errs.append(f"manifest quality_scale is {manifest.get('quality_scale')!r}, want 'platinum'")

PLATINUM = {"async-dependency", "inject-websession", "strict-typing"}
for slug in PLATINUM:
    if slug not in qs:
        errs.append(f"missing Platinum rule: {slug}")

for slug, val in qs.items():
    status = val if isinstance(val, str) else val.get("status")
    comment = "" if isinstance(val, str) else (val.get("comment") or "")
    if status not in ("done", "exempt"):
        errs.append(f"{slug}: status {status!r} (must be done|exempt for Platinum)")
    if status == "exempt" and not comment.strip():
        errs.append(f"{slug}: exempt without comment")

for e in errs:
    print(e)
sys.exit(1 if errs else 0)
PY

# ---- 7. HACS / manifest basics ----------------------------------------------
run "repo has README + LICENSE + hacs.json" \
  bash -c 'test -f README.md && test -f LICENSE && test -f hacs.json'

run "manifest version present (custom-component requirement)" \
  python - "${INTEGRATION}/manifest.json" <<'PY'
import json, sys, pathlib
m = json.loads(pathlib.Path(sys.argv[1]).read_text())
sys.exit(0 if m.get("version") and m.get("documentation") else 1)
PY

run "root package version matches manifest" \
  python - pyproject.toml "${INTEGRATION}/manifest.json" <<'PY'
import json, pathlib, sys, tomllib
project = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
manifest = json.loads(pathlib.Path(sys.argv[2]).read_text())
actual = project["project"]["version"]
expected = manifest["version"]
if actual != expected:
    print(f"version mismatch: pyproject={actual}, manifest={expected}")
    sys.exit(1)
PY

# ---- 8. library builds & wheel carries py.typed -----------------------------
if have python && python -c "import build" 2>/dev/null; then
  run "library builds (sdist+wheel) with py.typed in wheel" bash -c '
    set -e
    cd "'"${LIB_DIR}"'"
    rm -rf dist
    python -m build >/dev/null
    whl=$(ls dist/*.whl | head -n1)
    python - "$whl" <<PY
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
sys.exit(0 if any(n.endswith("pysilverline/py.typed") for n in names) else 1)
PY'
else
  skip "library build" "python -m build unavailable (pip install build)"
fi

# ---- 9. CI thresholds not weakened (anti-gaming, machine-checked) -----------
# Detects the obvious cheat: relaxing the CI coverage gate. The grepped value
# must stay >= 95. hassfest + HACS proper run in CI (below).
run "CI coverage gate not weakened (>= 95)" \
  python - .github/workflows/tests.yaml <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
if not p.exists():
    print("tests.yaml missing"); sys.exit(1)
vals = [int(m) for m in re.findall(r'--cov-fail-under[= ]+(\d+)', p.read_text())]
sys.exit(0 if vals and all(v >= 95 for v in vals) else 1)
PY

# ---- 10. hassfest + HACS (CI authority; optional local via act) -------------
# hassfest is the canonical validator for manifest/quality_scale/icons/strings.
# It runs reliably as the GitHub Action; locally we only structurally pre-check
# (done above). If `act` is present we attempt the real jobs.
if have act; then
  run "hassfest (act)" act -j hassfest -W .github/workflows/hassfest.yaml
  run "HACS (act)"     act -j hacs     -W .github/workflows/hacs.yaml
else
  skip "hassfest + HACS" "no local runner (install nektos/act, or confirm both CI jobs green before DONE)"
fi

# ---- summary ----------------------------------------------------------------
hr
echo "SUMMARY:  ${PASS} passed   ${FAIL} failed   ${SKIP} skipped"
if ((${#SKIPPED[@]})); then
  echo "SKIPPED (must be confirmed in CI, NOT a pass):"
  printf '  - %s\n' "${SKIPPED[@]}"
fi
if ((FAIL)); then
  echo "FAILED:"
  printf '  - %s\n' "${FAILED[@]}"
  hr
  echo "GATE: RED — not Platinum yet."
  exit 1
fi
hr
echo "GATE: GREEN — all hard checks pass. (Reviewer panel + CI hassfest/HACS still required for DONE.)"
exit 0
