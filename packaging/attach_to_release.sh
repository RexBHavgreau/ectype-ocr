#!/usr/bin/env bash
# Attach built files to a GitHub release: attach_to_release.sh TAG FILE...
# Makes the release (with GitHub's generated notes) if the tag has none yet; gives an
# existing release with no notes the generated ones; replaces files of the same name.
# Both release jobs run it, so creating can race: a failed create is retried as a lookup.
set -euo pipefail
tag="$1"; shift
repo="${GITHUB_REPOSITORY:?}"
if ! gh release view "$tag" --repo "$repo" >/dev/null 2>&1; then
  gh release create "$tag" --repo "$repo" --verify-tag --generate-notes --title "$tag" \
    || gh release view "$tag" --repo "$repo" >/dev/null
fi
body="$(gh release view "$tag" --repo "$repo" --json body --jq .body)"
if [[ -z "${body//[[:space:]]/}" ]]; then
  notes="$(gh api "repos/$repo/releases/generate-notes" -f tag_name="$tag" --jq .body || true)"
  if [[ -n "$notes" ]]; then gh release edit "$tag" --repo "$repo" --notes "$notes"; fi
fi
for f in "$@"; do
  [[ -f "$f" ]] || { echo "::error::No file to attach: $f"; exit 1; }
  for try in 1 2 3; do
    if gh release upload "$tag" "$f" --repo "$repo" --clobber; then break; fi
    if [[ $try == 3 ]]; then echo "::error::Couldn't attach $f to $tag"; exit 1; fi
    sleep $((try * 15))
  done
  echo "Attached $(basename "$f") to $tag"
done
