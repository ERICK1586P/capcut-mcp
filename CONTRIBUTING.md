# Contributing

Thanks for your interest in capcut-mcp. Small, focused pull requests are the easiest to review.

## Development setup

- Docker (Docker Desktop on Windows, or Docker Engine on Linux) and Python 3.10+ on the host.
- Build the image: `./build.sh` (tags `capcut-mcp:<version>` and `capcut-mcp:local`).
- The host-side scripts use the standard library only; no virtualenv is required.

## Tests

| Command | What it covers | Needs |
|---|---|---|
| `python3 test_timeline.py` | pure timeline operations (`timeline_edit.py`) on a synthetic project | nothing |
| `VECTCUT_REAL_FIXTURE=<draft_content.json> python3 test_timeline.py` | the same operations on a real CapCut 9.3 project of yours | a CapCut project |
| `python3 smoke_test.py` | end-to-end run of the container over JSON-RPC: new drafts, opening/editing/saving existing projects, negative cases | Docker + the image |
| `VECTCUT_SAMPLE_MP4=<~5 s mp4> python3 smoke_test.py` | same, with your own sample clip instead of the synthesised one | Docker |

Both scripts print `... PASSED` at the end and exit non-zero on failure. CI runs the first and third.

## Rules of the road

- **Upstream is not patched.** `vendor/VectCutAPI-<sha>.tar.gz` is an unmodified `git archive` of the pinned
  VectCutAPI commit. Behaviour changes live in `entry.py` (wrapper) and `timeline_edit.py` (timeline
  operations). To bump upstream, see "Updating upstream" in the README.
- Keep the wrapper safe by default: no new writable mounts, no network listeners, validate every tool
  argument that ends up in a path or a URL.
- New tools: add the schema in `patch_tools()`, the dispatch in `execute_tool_wrapper()`, a smoke-test step,
  and a README row. Bump `WRAPPER_VERSION` in `entry.py` (semver) and add a CHANGELOG entry.
- Commit messages and PR descriptions in English. One logical change per PR.
- Anything that was verified in CapCut itself (project opens, edits visible) is worth mentioning in the PR;
  the JSON format is only partially documented and CapCut is the final judge.

## Reporting bugs

Open an issue with: wrapper version (`capcut_healthcheck` tool or the container's first stderr line),
CapCut version, OS, MCP client (Claude Desktop, OpenCode, ...), the tool call that failed and the
container's stderr. Please do not attach your `draft_content.json` if it contains anything private.
