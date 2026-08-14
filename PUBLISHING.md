# Publishing Obolus to the MCP Registry

Brief for whoever runs the release. Needed on **every version bump**, not just the
first one — the Registry stores metadata only, and that metadata carries a version
that must match what is on PyPI.

**Since 0.2.2 this is automated.** `.github/workflows/release.yml` has a
`registry` job that publishes on every `v*` tag using GitHub Actions OIDC. The
manual steps below are the fallback, and the explanation of what the automation
is doing. Reach for them when the job fails or when you are publishing outside a
tag.

## State as of 2026-08-14

| Thing | Status |
|---|---|
| PyPI `obolus` | **live**, 0.2.2 |
| `mcp-name: io.github.inviti8/obolus` in the published PyPI description | **present**, verified against the live JSON API — this is the ownership proof |
| `server.json` validity, name, title, identifier `obolus` | **valid** against the 2025-12-11 schema, checked with `mcp-publisher validate` |
| `server.json` version vs PyPI | **aligned**, and `tests/test_packaging.py` now fails the build if they drift |
| Launch command reaches a server | **fixed in 0.2.2** — see the next section |
| GitHub repo `inviti8/Obolus` | **renamed**, matches `repository.url` |
| Automated publish on tag | **in `release.yml`** |
| MCP Registry entry | see "Verify" below |

## The thing that nearly shipped a dead listing

A client builds its launch command from the PyPI distribution name. For us that
is `uvx obolus` — and `obolus` is the **CLI**, not the server. Measured:

```
$ uvx obolus
usage: obolus [-h] [--network {mainnet,testnet}] {vault,session,fetch,sessions,reap,mcp} ...
obolus: error: the following arguments are required: command
```

The process exits immediately. To an MCP client that is indistinguishable from a
server that crashed on startup, and the Registry entry would have installed
cleanly while never once working. Nothing in the schema, `mcp-publisher validate`
or the Registry's own checks catches this — all of them verify that the *package*
exists, not that the *command* serves.

Two things fix it, and both are needed:

- `obolus mcp` is a real subcommand (0.2.2), sharing one code path with the
  `obolus-mcp` script via `serve(cfg)` in `obolus/mcp/server.py`.
- `server.json` carries `packageArguments: [{"type": "positional", "value": "mcp"}]`,
  so the command a client assembles is `uvx obolus mcp`.

`tests/test_packaging.py::test_registry_package_arguments_are_real_cli_commands`
asserts every positional in `server.json` is a subcommand the CLI actually has.
Without it, renaming or dropping the verb would break the Registry entry silently
— nothing else in the build would notice.

**If you change the entry point, re-prove it end to end**, not by reading. Do a
real `initialize` + `tools/list` over stdio against the exact command the entry
names. A server that starts and hangs looks identical to a working one until a
client speaks to it.

## The goal

`io.github.inviti8/obolus` resolvable at
`https://registry.modelcontextprotocol.io/v0/servers?search=obolus`.

## Manual steps (the fallback)

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

*(CI uses `mcp-publisher login github-oidc` instead. Do not put `login github` in
a workflow — it will sit waiting for a human who is not there.)*

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
  equal a version that is live on PyPI, or the Registry rejects the submission
  with what reads like an auth error. `tests/test_packaging.py` now enforces the
  local half of this; the PyPI half is checked by the workflow, which waits for
  the new version to appear before publishing. By hand:

  ```bash
  python -c "import json,urllib.request;s=json.load(open('server.json'));p=json.load(urllib.request.urlopen('https://pypi.org/pypi/obolus/json'));print('server.json',s['version'],s['packages'][0]['version'],'| pypi',p['info']['version'])"
  ```
- **PyPI is not instantly consistent.** The JSON API can still serve the previous
  version for a few seconds after `uv publish` returns. Publishing to the Registry
  immediately can therefore fail against a version that genuinely exists.
- **The ownership marker lives on PyPI, not in the repo.** The Registry fetches
  the package description from PyPI and looks for `mcp-name: io.github.inviti8/obolus`.
  Editing `README.md` locally changes nothing until a release is published to
  PyPI. Verify with:
  `curl -s https://pypi.org/pypi/obolus/json | grep -o "mcp-name: [^ ]*"`
- **Wrong directory.** `login` and `publish` both operate on `./server.json`.
- **The marker needs a boundary after it** — newline, whitespace, or `-->`. Gluing
  it to a trailing period breaks the match. It is currently on its own line at the
  top of `README.md`; leave it there.
- **A valid entry is not a working one.** See "the thing that nearly shipped a
  dead listing" above. Validation proves the package exists; only a handshake
  proves the command serves.

## Links

- Registry quickstart — <https://modelcontextprotocol.io/registry/quickstart>
- Authentication (all methods) — <https://modelcontextprotocol.io/registry/authentication>
- Package types, incl. the PyPI ownership rule — <https://modelcontextprotocol.io/registry/package-types>
- `mcp-publisher` source and release binaries — <https://github.com/modelcontextprotocol/registry>
- Latest binaries — <https://github.com/modelcontextprotocol/registry/releases/latest>
- Live registry API — <https://registry.modelcontextprotocol.io/v0/servers>

The Registry is in preview; breaking changes and data resets are possible, so
re-read the quickstart if something contradicts this file.
