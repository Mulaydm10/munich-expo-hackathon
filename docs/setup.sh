#!/usr/bin/env bash
# Runtime for the `run` job and for workers. Design-owned; CI executes the copy on the BASE branch, never the PR's.
# Runner is ubuntu-latest (python3, node, go, java preinstalled; BASE_SHA is set). Swap the body for your stack:
#   node:  npm ci            go: go mod download            rust: cargo fetch
# Read dependency manifests from BASE to keep the reviewed trust path:  git show "$BASE_SHA:package.json" > package.json
if [ -n "${BASE_SHA:-}" ]; then git show "$BASE_SHA:requirements-dev.txt" > /tmp/requirements-dev.txt
else cp requirements-dev.txt /tmp/requirements-dev.txt; fi
python3 -m pip install -q -r /tmp/requirements-dev.txt
