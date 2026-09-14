# CLAUDE.md — AI working notes for ha-silverline

Supplemental instructions for Claude Code and other AI assistants working in this repo.
Extends the global `~/.claude/CLAUDE.md`; if they conflict, this file wins.

---

## Release procedure

### Overview

There are **two git remotes**:

| remote   | URL                                       | purpose                          |
|----------|-------------------------------------------|----------------------------------|
| `origin` | `ssh://git.alpha-labs.net:2222/...`       | internal Gitea (primary dev)     |
| `github` | `git@github.com:christianreiss/ha-silverline.git` | public GitHub (HACS / PyPI) |

GitHub Actions only run on the **`github`** remote. Always push to both.

### Two independent release pipelines (both tag-driven on `github`)

| workflow                  | trigger tag          | produces                      |
|---------------------------|----------------------|-------------------------------|
| `release.yaml`            | `v*.*.*`             | GitHub Release + zip asset    |
| `pysilverline-pypi.yaml`  | `pysilverline-v*.*.*`| PyPI package `pysilverline`   |

**Cutting a release = pushing the matching tag to `github`.**
Bumping a version number in a commit does nothing on its own.

### Correct upgrade path (step by step)

1. Bump versions:
   - `pysilverline/pyproject.toml` → `version = "X.Y.Z"`
   - `pysilverline/src/pysilverline/__init__.py` → `__version__ = "X.Y.Z"`
   - `custom_components/poolex_silverline/manifest.json` → `"version": "A.B.C"` and `"requirements": ["pysilverline==X.Y.Z"]`
   - `pyproject.toml` (repo root) → `version = "A.B.C"` — tracks the
     *integration* version, not the library's. Easy to miss: `release.sh`
     does not check it and no test pins it, so it silently drifts.

   A library-version bump is only needed when something under `pysilverline/`
   actually changed. If the release is integration-only, leave
   `pysilverline/pyproject.toml`, `__init__.py` and the manifest's
   `requirements` pin alone — `release.sh` requires the manifest pin to equal
   the library version being tagged, so bumping one without the other fails
   the release.

2. Commit the version bump (one commit, both bumps together).

3. Push the version-bump commit to **both** remotes:

   ```bash
   git push github main
   git push origin main
   ```

4. Run `./scripts/release.sh`. It verifies the tree is clean, HEAD is on
   `github/main` (it fetches from the `github` remote to check — so it needs
   network, including with `--dry-run`), and the manifest pins the library
   version being tagged. It also requires successful Tests, HACS and hassfest
   main-push runs for HEAD (including in dry-run mode), using authenticated `gh`.
   It then creates and pushes `vA.B.C` and
   `pysilverline-vX.Y.Z` to GitHub and mirrors them to Gitea.

5. Those user-pushed tags trigger `release.yaml` (GitHub Release) and
   `pysilverline-pypi.yaml` (PyPI).

### Ordering constraint

The integration `manifest.json` pins `pysilverline==X.Y.Z`.
**PyPI must be live before the HACS release is usable.**
Both delivery workflows check the exact checked-out commit against successful
Tests, HACS and hassfest main-push runs before building or publishing. Missing
or pending runs wait up to 15 minutes; failures, cancellations, skipped jobs,
API errors and timeouts block delivery. Green CI at another SHA is insufficient.
The GitHub release verifies the manifest/project versions without modifying the
tested files and waits for the pinned PyPI package to be downloadable.

A release is complete only after both delivery workflows succeed and the PyPI
package plus GitHub ZIP are verified. Record the CI and delivery run links;
never report tag push alone as successful delivery.

### Manual recovery

If tags exist on GitHub but a downstream workflow did not fire, trigger it by
hand. Both workflows require the exact tag and check out that tag before
building, so a recovery cannot accidentally package current `main` under an
older version:

- **GitHub Release:** Actions → Release → "Run workflow" → enter tag (e.g. `v0.8.2`) → Run
- **PyPI:** Actions → Publish pysilverline to PyPI → "Run workflow" → enter
  the library tag (e.g. `pysilverline-v0.5.1`) → Run

Both workflows have `workflow_dispatch` enabled for exactly this scenario.

---

## Pre-commit hook

The repo uses a custom hooks path (`git config core.hooksPath`).
The pre-commit hook runs the full pysilverline test suite (v3.3 + v3.4 + v3.5 API tests).
**Never use `--no-verify`** — the hook catches protocol-level regressions.

---

## Key files

| path | purpose |
|------|---------|
| `custom_components/poolex_silverline/` | HA integration (HACS) |
| `pysilverline/` | standalone library published to PyPI |
| `scripts/release.sh` | validates, creates, and pushes both release tags |
| `.github/workflows/release.yaml` | builds GitHub Release from `v*` tag |
| `.github/workflows/pysilverline-pypi.yaml` | publishes library to PyPI |
| `GUIDELINES.md` | HA integration idioms and quality-scale rules |
