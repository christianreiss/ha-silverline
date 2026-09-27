# Development

## Git hooks

After cloning, install the git hooks once:

```bash
./scripts/install-hooks.sh
```

This points `core.hooksPath` at the tracked `.githooks/` directory. The
`pre-commit` hook runs the `pysilverline` protocol/client API test suite
(Tuya **v3.3**, **v3.4** and **v3.5**) before every commit, so a change that
breaks any wire protocol can't land. It's the library suite only (fast, a few seconds);
linting, type-checking, and the Home Assistant integration tests are left to
CI and `scripts/platinum-gate.sh`. CI and the gate both use Ruff 0.16.1 with
the explicit rule set in the two `pyproject.toml` files, so lint behavior does
not drift when Ruff changes its defaults.

Bypass the hook for a single commit with `git commit --no-verify`, or set
`SKIP_HOOK_TESTS=1` in the environment.

## Release notes

The Home Assistant integration pins `pysilverline` in `manifest.json`.
Release delivery requires green **Tests** (both Python versions, integration
coverage, mypy and Ruff), **HACS validation**, and **hassfest** main-push runs
for the **exact tagged commit**. `scripts/check_release_ci.py` checks the
[GitHub workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs)
and every job result; failed, cancelled, skipped or missing checks block delivery.
It waits up to 15 minutes for pending/missing runs and fails closed on API errors.
The successful run links and commit SHA appear in the delivery job summary.

1. Configure the PyPI Trusted Publisher for project `pysilverline`, repository
   `christianreiss/ha-silverline`, workflow `pysilverline-pypi.yaml`, environment `pypi`.
2. Set matching versions in the integration manifest and root `pyproject.toml`;
   if changing the library, update its version and the manifest requirement too.
3. Commit and push to **both** `github/main` and `origin/main`.
4. Run `./scripts/release.sh` (requires authenticated `gh`); even `--dry-run`
   verifies green CI before creating tags. Both tag workflows independently
   enforce the same gate, including manual recovery runs.
5. The GitHub Release workflow verifies versions without rewriting tested files
   and waits for the pinned library to be downloadable from PyPI before publishing.
   Verify **both delivery workflows**, the PyPI package and GitHub release asset
   before announcing success; pushing tags alone is not completed delivery.

If CI fails, fix and push the commit before tagging. Rerun failed checks for a
transient failure on the same SHA; a newer successful run at another SHA does
not qualify. Manual recovery must use an exact existing version tag whose commit
has passed all three main-push workflows. Older tags without the gate script fail
closed; do not retag published versions to bypass this requirement.

## Reference material

- [`GUIDELINES.md`](GUIDELINES.md) — Home Assistant integration idioms and
  quality-scale rules this repo follows.
- [`silverline-fe-specs.md`](silverline-fe-specs.md) — the original technical
  research on the Silverline FI Tuya DP schema. Parts of it are unsourced and
  have since been contradicted by hardware; the code comments and the
  [model notes](supported-devices.md) take precedence.
