# Security

capcut-mcp is a local tool: it runs as a Docker container spawned by your MCP client over stdio and
edits CapCut project files on your disk.

## What the wrapper does for you

- No network listener: the container talks to the client over stdin/stdout only; no ports are exposed.
- Writes go only to the mounted CapCut projects folder (`/drafts`): the draft being created, or the
  opened project and its copy. `/media` is mounted read-only.
- Every path and URL argument is validated before it reaches the upstream engine (no path traversal
  out of `/drafts` or `/media`, no loopback/private-network URLs, only http(s) downloads).
- Saving an opened project keeps timestamped backups (`draft_content.json.vectcut-bak-<ts>`, last 3)
  and refuses to write if the file changed on disk since it was opened (sha256 check).
- The container runs as an unprivileged user with memory and process limits set on the command line.

## What it does not protect against

- CapCut autosave: if a project is open in CapCut while you save into it, CapCut may overwrite your
  edits. Close the project first, or work on a copy (`open_project` default mode).
- The upstream engine (VectCutAPI) is used unmodified and was not written with hostile input in mind.
  Do not expose the container to untrusted clients or mount folders you do not want modified.
- Media downloaded by URL is fetched by the container; only point it at sources you trust.

## Reporting a vulnerability

Open a GitHub issue describing the class of problem and the affected version. Please do not include
working exploit details in the public issue; a maintainer will follow up for specifics.
