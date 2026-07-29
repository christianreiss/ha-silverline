#!/usr/bin/env bash
# Cut a release without GitHub Actions needing a PAT.
#
# Why this exists: GitHub will not trigger one workflow from a tag pushed by
# another workflow's GITHUB_TOKEN (anti-recursion). auto-release.yaml worked
# around that with a RELEASE_PAT. A tag pushed from a workstation is a real
# user push, so release.yaml and pysilverline-pypi.yaml fire normally — no
# token to create, scope, or renew.
#
#   ./scripts/release.sh            # tag the versions in HEAD and push them
#   ./scripts/release.sh --dry-run  # show what would happen
#
# Run it after the version-bump commit is pushed to main.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

integration=$(python3 -c \
  "import json;print(json.load(open('custom_components/poolex_silverline/manifest.json'))['version'])")
library=$(python3 -c \
  "import tomllib;print(tomllib.load(open('pysilverline/pyproject.toml','rb'))['project']['version'])")
pinned=$(python3 -c \
  "import json,re;r=json.load(open('custom_components/poolex_silverline/manifest.json'))['requirements'][0];print(re.sub(r'.*==','',r))")

echo "integration : $integration  -> v$integration"
echo "library     : $library  -> pysilverline-v$library"

# The manifest pins an exact library version. If the pin and the library
# disagree, HACS installs an integration whose dependency does not exist yet.
if [[ "$pinned" != "$library" ]]; then
  echo "ABORT: manifest pins pysilverline==$pinned but the library is $library" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ABORT: working tree is dirty — commit the version bump first" >&2
  exit 1
fi

if ! git merge-base --is-ancestor HEAD "$(git rev-parse --abbrev-ref --symbolic-full-name @{u})" 2>/dev/null; then
  echo "ABORT: HEAD is not pushed yet — run 'git push github main' first" >&2
  exit 1
fi

created=()
for pair in "v$integration" "pysilverline-v$library"; do
  if git rev-parse -q --verify "refs/tags/$pair" >/dev/null; then
    echo "skip: tag $pair already exists"
  else
    created+=("$pair")
  fi
done

if [[ ${#created[@]} -eq 0 ]]; then
  echo "Nothing to do — both tags already exist."
  exit 0
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  echo "would create and push: ${created[*]}"
  exit 0
fi

for tag in "${created[@]}"; do
  case "$tag" in
    pysilverline-v*) git tag -a "$tag" -m "pysilverline $library" ;;
    *)               git tag -a "$tag" -m "Poolex Silverline $integration" ;;
  esac
done

# github first: these pushes are what actually fire the release pipelines.
git push github "${created[@]}"
# Keep Gitea in sync; failure here does not affect the release.
git push origin "${created[@]}" || echo "warn: could not sync tags to origin"

echo
echo "Pushed: ${created[*]}"
echo "Watch:  gh run list -R christianreiss/ha-silverline --limit 5"
