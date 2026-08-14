# Publishing Obolus to the MCP Registry

Brief for whoever runs the release. Needed on **every version bump**, not just the
first one — the Registry stores metadata only, and that metadata carries a version
that must match what is on PyPI.

## State as of 2026-08-14

| Thing | Status |
|---|---|
| PyPI `obolus` 0.2.0 | **live** |
| `mcp-name: io.github.inviti8/obolus` in the published PyPI description | **present** — this is the ownership proof |
| `server.json` — name, title, version 0.2.0, package identifier `obolus` | **valid** against the 2025-12-11 schema |
| GitHub repo `inviti8/Obolus` | **renamed**, matches `repository.url` |
| MCP Registry entry | **MISSING** — this document exists to fix that |

Everything except the Registry entry is done. Do not re-do them.

## The goal

`io.github.inviti8/obolus` resolvable at
`https://registry.modelcontextprotocol.io/v0/servers?search=obolus`.

## Steps

### 1. Install `mcp-publisher`

Windows:

```powershell
$arch = if ([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -eq "Arm64") { "arm64" } else { "amd64" }
Invoke-WebRequest -Uri "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_windows_$arch.tar.gz" -OutFile "mcp-publisher.tar.gz"
tar xf mcp-publisher.tar.gz mcp-publisher.exe
rm mcp-publisher.tar.gz
```

**The download does not put it on PATH.** That manual step is the most likely
reason a previous attempt "didn't work" — move `mcp-publisher.exe` somewhere on
PATH, or call it by full path. Confirm with `mcp-publisher --help` before going on.

### 2. Log in

Run from the repo root — the tool reads `./server.json`:

```bash
mcp-publisher login github
```

**This is a device-code flow, not a browser redirect.** Nothing will open. It
prints a code and waits:

```
To authenticate, please:
1. Go to: https://github.com/login/device
2. Enter code: ABCD-1234
```

Open that URL yourself, enter the code, authorise. The terminal then prints
`Successfully authenticated!`. If you were waiting for a browser to launch, this
looks identical to a hang — it isn't.

GitHub auth is what grants the `io.github.inviti8/*` namespace. It must be the
`inviti8` account; any other account is rejected for this name.

### 3. Validate, then publish

```bash
mcp-publisher validate
mcp-publisher publish
```

`publish` takes **no `--registry` flag** — it reads the registry URL from the
token `login` stored, and a positional argument is interpreted as a path to
`server.json`. Passing `--registry` is a common way to get a confusing error.

### 4. Verify — do not assume

```bash
curl -s -A 'curl/8.5.0' "https://registry.modelcontextprotocol.io/v0/servers?search=obolus"
```

Must return an entry named `io.github.inviti8/obolus`. A zero-result response
means it did not publish, regardless of what the CLI printed. Search is known to
work: `?search=obol` returns the unrelated `dev.fly.obol-x402/obol`.

## Gotchas that fail the submission

- **Version drift.** `server.json` `version` *and* `packages[0].version` must both
  equal the version live on PyPI. All three are `0.2.0` right now. Bump all of
  them together or the Registry rejects the package as unverifiable.
- **The ownership marker lives on PyPI, not in the repo.** The Registry fetches
  the package description from PyPI and looks for `mcp-name: io.github.inviti8/obolus`.
  Editing `README.md` locally changes nothing until a release is published to
  PyPI. Verify with:
  `curl -s https://pypi.org/pypi/obolus/json | grep -o "mcp-name: [^ ]*"`
- **Wrong directory.** `login` and `publish` both operate on `./server.json`.
- **The marker needs a boundary after it** — newline, whitespace, or `-->`. Gluing
  it to a trailing period breaks the match. It is currently on its own line at the
  top of `README.md`; leave it there.

## Links

- Registry quickstart — <https://modelcontextprotocol.io/registry/quickstart>
- Authentication (all methods) — <https://modelcontextprotocol.io/registry/authentication>
- Package types, incl. the PyPI ownership rule — <https://modelcontextprotocol.io/registry/package-types>
- `mcp-publisher` source and release binaries — <https://github.com/modelcontextprotocol/registry>
- Latest binaries — <https://github.com/modelcontextprotocol/registry/releases/latest>
- Live registry API — <https://registry.modelcontextprotocol.io/v0/servers>

The Registry is in preview; breaking changes and data resets are possible, so
re-read the quickstart if something contradicts this file.
