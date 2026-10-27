# Go compilation reuse across issues

`go_build_cache` optionally seeds writable farm-hive workers' private Go caches
from an immutable, operator-published snapshot. It is separate from the host's
downloaded module cache. Read-only chats do not copy the seed.

```json
"go_build_cache": {
  "manifest": "/absolute/host-only/go-build-seeds/version/manifest.json",
  "sha256": "<SHA256 returned by publication>"
}
```

The seed and its preparation directory must be outside instance state,
sandbox-writable temporary directories and module/preparation storage.
`go_dependencies` must already pin the Go executable. Each seed records the
instance, Go digest, source commit and packages. Startup validates its identity
and file inventory before contacting Linear or opening the ledger. Doctor reads
the same inputs without creating storage. Missing or changed inputs are errors.

The controller copies hash-verified compilation files into each issue's
`compiler-cache-v1/go-cache`, after proving previous attempts quiescent. It does
not hardlink files, overwrite that issue's existing entries, or import its writes
back into the shared seed. Paths cannot contain links, and shared inputs cannot
have multiple hardlinks. A partially copied artifact is never installed. Later
attempts restore missing entries after partial imports or cache retention. The
host seed is outside worker retention.

Seeded workers retain private `GOCACHE`, `GOTMPDIR` and build-output roots. Their
inherited `GOFLAGS` ends with `-trimpath`, so identical local packages can reuse
compilation despite different checkout paths. An inherited `GOCACHEPROG` is
removed for this mode. Go still keys artifacts by source, compiler and build
options: changed inputs compile normally; race and non-race artifacts differ.
Existing server-test scope, `-p=1`, `GOMAXPROCS=2`, `-count=1` and containment
remain in effect. Cached compilation is never a test-result pass.

## Prepare and publish

This is an explicit operator operation, separate from automatic module download
preparation. Neither the controller nor a worker compiles or publishes shared
cache entries automatically. Never promote worker-writable caches: hashing
untrusted bytes does not establish a trustworthy Go action mapping.

1. Select an approved, clean source commit in a fresh host-owned checkout outside
   worker state. Use verified configured module dependencies. Record the source,
   toolchain, environment and commands as private rollout evidence.
2. Compile selected packages into a fresh host-only `GOCACHE` with the pinned Go,
   `-trimpath`, offline module access, `GOENV=off`, `GOWORK=off` and
   `GOTOOLCHAIN=local`. Match the workers' native target/build profile. For example,
   `go test -c -trimpath -mod=readonly -p=1 -o <private-output> ./server` compiles
   without executing tests. Use owned process containment and bounded teardown;
   never run generators, tests or the resulting binary merely to warm the cache.
3. After the compiler and descendants exit, seal into a **new** directory. On
   Windows use configured Python; on macOS use `python3`:

   ```text
   python -B -m agent.go_build_cache --config ABSOLUTE_CONFIG --cache ABSOLUTE_WARM_CACHE --output ABSOLUTE_NEW_SEED --repo farm-hive --commit FULL_APPROVED_COMMIT --package ./server
   ```

   Publication copies recognized Go compilation artifacts and writes the
   manifest last. Commit/packages are operator-recorded provenance; sealing does
   not independently reconstruct or certify the preceding build. Retain a failed
   publication for diagnosis; never configure it as a seed.
4. Validate a second checkout/private cache: identical inputs must avoid compiler
   invocations, and changed source must invalidate the appropriate entry. Record
   copy time separately from compilation/linking time. Set the returned manifest
   and hash during settled maintenance, update supervisor/Monitor pins, restart,
   inspect Doctor and resume the existing admission policy.

Every seeded attempt writes `go-cache-seed.json` with the manifest hash, copied
file/byte counts and copy duration. Preserve it with attempt logs. This records
imports, not whether a subsequent build hit every entry. New toolchains or
dependencies may require a new seed. Existing private caches and reports remain;
a seed/release change alone does not require repeating unchanged tests.

This feature adds no database schema. For rollback, settle workers, remove
`go_build_cache` from config and update installation pins before restart. Older
releases do not accept the new field. Keep seeds/private caches for recovery; do
not overwrite newer issue progress with an older database to revert this setting.
