"""Proof that the Editor in front of a worker is this folder, at this commit, in Edit Mode (spec §7).

Ported from FarmTestAgent/tools/farmqa_identity.py. The slot switch only moves a folder; this module is
the only thing that can tell a worker the Editor it is talking to really loaded what was checked out. An
interactive run without it can produce confident evidence about the wrong build, which is worse than no
evidence at all.

Three things differ from FarmQA. `ready` has no clock at all: the timestamp FarmQA bounded records the last
state *change* rather than a heartbeat, so the bound refused precisely the idle Editors it was meant to
admit — see `ready`. The observation is returned rather than written: `Ledger.record_identity` owns that
table now, and no SQLite transaction is ever held across an MCP call. And the verdict is an aggregate over
the checks instead of the constant BLOCKED, which also means FarmQA's two permanently-unknown checks are
gone — see `collect`.
"""
import hashlib
import math
import re
import subprocess
import time
from pathlib import Path


def source_snapshot(repository):
    root = Path(repository).resolve(strict=True)
    def git(*args):
        return subprocess.run(['git','--no-optional-locks','-c','core.fsmonitor=false',
                               '-C',str(root),*args], check=True, capture_output=True,
                              timeout=10).stdout.decode('utf-8')
    if Path(git('rev-parse','--show-toplevel').strip()).resolve() != root:
        raise ValueError('Target must be the repository root')
    head = git('rev-parse','--verify','HEAD').strip()
    if not re.fullmatch('[0-9a-f]{40}', head): raise ValueError('Unknown source revision')
    # No rename folding: each entry is exactly XY + path + NUL, including Unicode.
    status = git('status','--porcelain=v1','-z','--untracked-files=all','--no-renames')
    if len(status) > 1024*1024: raise ValueError('Source status exceeds bound')
    dirty = []
    for entry in filter(None, status.split('\0')):
        relative = entry[3:]
        path = root/relative
        digest = None
        # Never follow dirty symlinks/junctions outside the checkout or read devices.
        if path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink():
            if path.stat().st_size > 16*1024*1024: raise ValueError('Dirty file exceeds hash bound')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        dirty.append({'path':relative, 'status':entry[:2], 'sha256':digest})
    index_hash = hashlib.sha256(git('ls-files','--stage','-z').encode('utf-8')).hexdigest()
    return {'repository':str(root), 'commit_sha':head, 'dirty':dirty,
            'index_sha256':index_hash}


def ready(state, instance):
    """Require the expected instance, explicitly idle Edit Mode; missing booleans never pass. No clock.

    There is deliberately no freshness bound. `observed_at_unix_ms` is not a heartbeat: the plugin's
    `EditorStateCache.OnUpdate` returns before `BuildSnapshot` whenever nothing it tracks has moved — its
    own comment is "No state change - skip the expensive BuildSnapshot entirely" (EditorStateCache.cs:348)
    — `ForceUpdate("tick")` sits after that guard, and `ForceUpdate` is private with no MCP tool reaching
    it. The field therefore says *when the state last changed*, so the more reliably idle an Editor is, the
    older its sample grows. FarmQA never hit this because a human clicking around an Editor flips
    `isFocused`, which is in the change set. Live rehearsal Step 4 measured an age past 117 seconds with
    `sequence` frozen at 3, every busy flag False and `execute_code` answering throughout, and FarmQA's
    ten-second bound made that a permanent probe-stage hold on the best-behaved Editors there are.

    The bound was worse than useless, because it was inverted. `GetSnapshot` re-stamps
    `observed_at_unix_ms` to the read time whenever the Editor is *not* the active application
    (EditorStateCache.cs:529-532) while leaving the flags exactly as the last rebuild left them. So a
    backgrounded Editor always looked fresh no matter how old its flags were, and a foregrounded one — a
    slot this pool launched and left alone, which is every slot — was refused for flags that were current.
    The clause admitted precisely the case it could not vouch for and rejected the one it could.

    Losing the bound does not lose the safety property, because a frozen timestamp is the stronger signal
    here, not the weaker one: every flag below has a change trigger that forces a rebuild. The compilation
    edge bypasses even the one-second throttle (:295-300); `playModeStateChanged` and `beforeAssemblyReload`
    call `ForceUpdate` directly (:260, :268-273); and `is_updating`, tests-running and the derived activity
    phase are all in the `hasChanges` set (:336-344). So "this sample is old and says idle" means "nothing
    has become busy since it was taken". What the bound really caught — a main thread wedged badly enough
    that the cache cannot tick — is caught instead by the `execute_code` probe `collect` runs next, which
    cannot return without that thread, and whose live flags are what actually certify `editor_ready`.

    `staleness.is_stale` and `advice.ready_for_tools` went with it because both are the server's own
    arithmetic on this same timestamp, not independent evidence: `is_stale = age_ms > 2000`, and `is_stale`
    appends "stale_status" to `blocking_reasons`, whose emptiness *is* `ready_for_tools`
    (mcpforunityserver services/resources/editor_state.py:186-207). They were a tighter copy of the clause
    above, which is why `ready_for_tools` was observed going False on an idle Editor. Every other reason
    they can block — compiling, domain reload, running tests — is checked directly below, and the fourth,
    `assets.refresh.is_refresh_in_progress`, is hardcoded `false` by the installed plugin
    (EditorStateCache.cs:473-478).

    `observed_at_unix_ms` must still be a finite number. That is well-formedness, not freshness: `collect`
    records it on both sides of the probe as evidence in the ledger row.
    """
    try:
        observed = state['observed_at_unix_ms']
        return (state['schema_version'] == 'unity-mcp/editor_state@2'
                and type(observed) in (int,float) and math.isfinite(observed)
                and state['unity']['instance_id'] == instance
                and all(state['editor']['play_mode'][key] is False
                        for key in ('is_playing','is_paused','is_changing'))
                and state['compilation']['is_compiling'] is False
                and state['compilation']['is_domain_reload_pending'] is False
                and state['assets']['is_updating'] is False
                and state['tests']['is_running'] is False)
    except (KeyError, TypeError):
        return False


def quiet(state):
    """`ready`'s four busy flags, without the schema check, the play-mode flags or the instance comparison.

    The omissions are deliberate. The refresh wait cannot compare instances, because hearing from the
    instance is the thing it is waiting for, and it must not mind play mode, because what it is waiting out
    is a compile or an import and nothing else.
    """
    try:
        return (state['compilation']['is_compiling'] is False
                and state['compilation']['is_domain_reload_pending'] is False
                and state['assets']['is_updating'] is False
                and state['tests']['is_running'] is False)
    except (KeyError, TypeError):
        return False


def safe_text(value, limit=4096):
    return value if (isinstance(value,str) and 0 < len(value) <= limit
                     and not any(ord(c)<32 for c in value)) else None


def project_matches(value, expected):
    return bool(safe_text(value) and Path(value).resolve() == Path(expected).resolve())


def aggregate(checks):
    """match only when every check is match; unknown is never match (farmqa_request_session.py:138-139)."""
    values = set(checks.values())
    if "mismatch" in values:
        return "mismatch"
    if values - {"match"}:
        return "unknown"
    return "match"


MVID = re.compile('[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')

CHECKS = ("editor_ready", "project", "source_commit", "source_clean", "source_stable", "build_target",
          "loaded_assemblies")


def collect(client, *, repository, instance, expected, probe_source):
    """One identity observation. Never raises: an MCP or git failure is an `unknown` aggregate, which the
    pool turns into a hold, and the exception type is kept as evidence.

    The order of operations is FarmQA's and is load-bearing: source snapshot, custom-tools, instances,
    select, editor/state, the ready() gate, project/info, the probe, editor/state again, second source
    snapshot. The two snapshots either side are what make "the slot really was on that commit for the whole
    run" checkable rather than asserted, and no SQLite transaction is ever held across any of it.

    FarmQA's `loaded_source` and `server_environment` checks are gone rather than ported. They were pinned
    to 'unknown' on purpose, because that tool's verdict was the constant BLOCKED and could afford it. Here
    an unknown is never a match, so carrying them would make every aggregate unknown for ever and every
    interactive switch a probe-stage hold — a permanent hold dressed up as a measurement.
    """
    checks = {key: "unknown" for key in CHECKS}
    result = {"checks": checks, "started_at": time.time(), "instance": instance,
              "expected": {"build_target": expected.get("build_target"),
                           "commit_sha": expected.get("commit_sha"),
                           "assemblies": sorted(expected.get("assemblies") or ())}}
    try:
        result['source_before'] = source_snapshot(repository)
        client.read_resource('mcpforunity://custom-tools')
        # Presence, not FarmQA's "exactly one connected instance": that rule would make a second slot
        # impossible and would fail whenever any other Editor under this user reached the same server.
        # select_instance enforces it and names the ones that are connected when it does not hold.
        listed = client.read_resource('mcpforunity://instances')
        client.select_instance(instance)
        before = client.read_resource('mcpforunity://editor/state')
        if ready(before, instance):
            info = client.read_resource('mcpforunity://project/info')
            probe = client.call_tool('execute_code', {'action':'execute', 'code':probe_source,
                                                      'safety_checks':True})['result']
            after = client.read_resource('mcpforunity://editor/state')
            result['source_after'] = source_snapshot(repository)
            result['editor'] = {key:safe_text(probe.get(key)) for key in
                                ('unityVersion','platform','buildTarget','dataPath')}
            result['editor']['instance'] = instance
            result['editor']['observed_before_ms'] = before['observed_at_unix_ms']
            observed_after = after.get('observed_at_unix_ms')
            result['editor']['observed_after_ms'] = observed_after if (
                type(observed_after) in (int,float) and math.isfinite(observed_after)) else None
            result['editor']['play_mode_off'] = probe.get('isPlaying') is False
            # The C# probe no longer filters; the expected set is configuration and lives here.
            wanted = set(expected['assemblies'])
            observed = []
            assemblies = probe.get('assemblies')
            if isinstance(assemblies,list):
                for assembly in assemblies:
                    name, mvid = assembly.get('name'), assembly.get('moduleMvid')
                    if name in wanted and isinstance(mvid,str) and MVID.fullmatch(mvid):
                        observed.append({'name':name, 'module_mvid':mvid.lower(),
                                         'has_game_test_driver': assembly.get('hasGameTestDriver') is True})
            result['editor']['assemblies'] = observed
            checks['loaded_assemblies'] = 'match' if {a['name'] for a in observed} == wanted else 'mismatch'
            if (ready(after, instance)
                    and before['unity'] == after['unity']
                    and all(probe.get(key) is False for key in
                            ('isPlaying','isPlayingOrWillChangePlaymode','isCompiling','isUpdating'))
                    and probe.get('scene',{}).get('isDirty') is False):
                checks['editor_ready'] = 'match'
            checks['project'] = 'match' if (project_matches(info.get('projectRoot'),repository)
                and project_matches(probe.get('dataPath'),Path(repository)/'Assets')) else 'mismatch'
            checks['build_target'] = 'match' if (probe.get('buildTarget') == expected['build_target']
                and info.get('platform') == expected['build_target']) else 'mismatch'
            first, last = result['source_before'], result['source_after']
            checks['source_commit'] = ('match' if first['commit_sha'] == last['commit_sha']
                                       == expected['commit_sha'] else 'mismatch')
            checks['source_clean'] = 'match' if not first['dirty'] and not last['dirty'] else 'mismatch'
            checks['source_stable'] = 'match' if first == last else 'mismatch'
            result['samples'] = {'instances':listed, 'project_info':info, 'editor_state_before':before,
                                 'editor_state_after':after,
                                 # The probe minus its assembly list, which is replaced by the compact
                                 # observed set above: the fullName and location of ~200 framework
                                 # assemblies are not evidence, and this row is stored as JSON.
                                 'probe':{k:v for k,v in probe.items() if k != 'assemblies'}}
    except Exception as exc:
        result['error_type'] = type(exc).__name__
    result['aggregate'] = aggregate(checks)
    result['observed_at'] = time.time()
    return result
