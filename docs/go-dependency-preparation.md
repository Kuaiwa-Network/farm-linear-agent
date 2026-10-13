# Host Go dependency preparation

An opt-in host controller prepares module inputs before workers run Go checks.
After a clean candidate is committed, the worker requests its configured module
path and exact HEAD through `prepare-go-dependencies`. The CLI authenticates the
running claim, validates the owned clean worktree, reads regular committed
`go.mod`/`go.sum` blobs through the existing clone trust checks, and records a
durable request. The controller handles one request at a time on a separate loop;
the scheduler, receiver, progress reporting and Stop continue independently.
Requests must precede resource reservations and bind the current claim and host
settings. A replacement claim cancels old pending preparation; changed host
settings cannot reuse an old request's readiness. Storage ownership also binds
the instance's absolute state root, so two roots cannot share it under one app ID.

Workers poll `go-dependencies` while retaining/renewing their claim and doing
independent work. Pending host preparation needs no human reply. Once ready, the
worker selects the returned cache for its own offline gates. A changed candidate
requires a new request. Failed/cancelled requests can be retried after host
correction, with a maximum of three requests for the same candidate/module.
Preparation certifies dependencies only; it does not replace build, generation,
tests, actual sandbox access or reserved server/Unity verification.

## Configure once on the host

The private config accepts the following block. Select existing native tools;
the controller never installs or upgrades them. Use absolute native paths and the
selected Go binary's SHA256. Cache and preparation roots must be separate, must
not overlap the instance state root, and must belong exclusively to this instance.
The module map names only repositories already present in `repos`.

```json
"go_dependencies": {
  "go": "/absolute/native/go",
  "go_sha256": "<64 lowercase hex characters>",
  "git": "/absolute/native/git",
  "gh": "/absolute/native/gh",
  "cache_root": "/absolute/instance-prepared-go-cache",
  "work_root": "/absolute/instance-go-preparation",
  "modules": {"farm-hive": ["."], "common": ["designer/configgen"]},
  "private_prefixes": ["github.com/Kuaiwa-Network"]
}
```

On Windows use the configured Python executable and Windows absolute paths.
On macOS use `python3`. Once configured, every `serve` startup validates the
native tools and pinned Go digest, initializes missing or empty cache/preparation
roots, and verifies existing ownership markers. It preserves already prepared
cache contents. See the [host startup guide](startup.md) for Go and Unity setup,
routine startup and readiness checks.

The operator can also initialize ownership explicitly, including before starting
an older service revision that requires marked roots:

```sh
python3 -m agent.go_dependencies --config /absolute/private-config.json
```

Add `--adopt-existing` only to explicitly adopt an existing host-prepared cache.
An existing ownership marker for another instance is always rejected. `serve`
refuses a nonempty unmarked, linked or incorrectly owned root, missing native
tools, or a changed Go executable digest. It never adopts existing unmarked
content at startup. Host `gh`
authentication must already be available; no login or credential setup runs.

## Preparation and recovery

Only Go module parsing, download, module graph enumeration and checksum
verification run on the host. Local replacements are refused. Public modules use
the Go proxy/checksum database, with public VCS fallback disabled. Private VCS is
limited to explicitly configured GitHub organization prefixes and Git, with a
fixed host `gh` credential helper, hooks/fsmonitor disabled and isolated Git
configuration. No repository package, tool, generator or build executes here.

Downloads occur in separate staging storage. Go expands archive-only modules
there, including missing source `go.mod` files. Original committed inputs are
restored after download; the full graph and checksums must then pass offline with
read-only module flags. Publication adds only missing cache files, refuses
conflicting existing immutable bytes and preserves existing version indexes.
Final offline module graph and checksum checks run against the published cache.
Workers gain no cache write root, network capability, credential or tool-install
authority. Worker-owned HOME, build cache and temporary storage remain private.
Offline checks override private-module proxy bypass with `GONOPROXY=none` and
disable every VCS route with `GOVCS=*:off`, in addition to proxy/checksum services
being off. Complete caches are reverified offline without downloads or expansion.

Native Windows Go/Git descendants run in a suspended-created, non-breakaway Job
Object. POSIX processes use an owned process group. Timeout, Stop, withdrawal,
expired claims or repository/generation changes cancel preparation; descendants
must drain before a command succeeds. A partial cancelled publication is retained
and cannot certify readiness. Host diagnostics and receipts stay in the configured
preparation root; failures expose a fixed explanation and directory, not raw tool
errors or credential values.

Requests/results use existing audit rows; there is no schema migration. A
restarted controller reconciles unfinished requests and revalidates their claims
and exact source. On rollback, settle workers and stop the service, remove the
`go_dependencies` config block before starting older code, and retain cache,
ownership markers and diagnostics. Older code ignores the new audit records;
it does not provide this worker CLI or automatic preparation. This feature does
not merge, deploy, change issue answers or release resource reservations.
