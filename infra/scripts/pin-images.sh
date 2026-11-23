#!/usr/bin/env bash
# Pin every container image of the platform to the digest of its current tag, so a re-pushed tag can never change what runs
# (`image: caddy:2.8-alpine@sha256:...` keeps the readable tag; Docker checks the digest).
#   scripts/pin-images.sh           pin images that have no digest yet
#   scripts/pin-images.sh --update  move every digest to what the tag points to NOW (do this deliberately: monthly, then smoke + Trivy)
# Files: infra/compose.yaml, infra/monitoring/compose.yaml, auth/Dockerfile. Product repos pin their own base images on their own branches.
# The digest is the multi-arch index digest, so the same line works on arm64 laptops and the x86 server.
set -euo pipefail
cd "$(dirname "$0")/../.."
update=0; [ "${1:-}" = "--update" ] && update=1
files=(infra/compose.yaml infra/monitoring/compose.yaml auth/Dockerfile)
python3 - "$update" "${files[@]}" <<'PY'
import re, subprocess, sys

update, files = sys.argv[1] == "1", sys.argv[2:]
pat = re.compile(r"^(?P<lead>\s*(?:image:\s*|FROM\s+))(?P<ref>[^\s@]+)(?:@(?P<dig>sha256:[0-9a-f]{64}))?(?P<tail>.*)$")
cache: dict[str, str] = {}

def digest(ref: str) -> str:
    if ref not in cache:
        out = subprocess.run(["docker", "buildx", "imagetools", "inspect", ref], capture_output=True, text=True)
        m = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})", out.stdout, re.M)
        if not m:
            sys.exit(f"cannot resolve {ref}: {out.stderr.strip()[:200]}")
        cache[ref] = m.group(1)
    return cache[ref]

for path in files:
    lines, changed = open(path).read().split("\n"), False
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m or line.lstrip().startswith("#") or "scratch" in m["ref"] or ("FROM" in m["lead"] and " AS " in m["tail"] and m["ref"] in ("builder",)):
            continue
        if m["dig"] and not update:
            continue
        new = f'{m["lead"]}{m["ref"]}@{digest(m["ref"])}{m["tail"]}'
        if new != line:
            lines[i], changed = new, True
            print(f"{path}: {m['ref']} -> {digest(m['ref'])[:19]}")
    if changed:
        open(path, "w").write("\n".join(lines))
PY
