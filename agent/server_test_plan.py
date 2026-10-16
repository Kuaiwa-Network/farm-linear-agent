"""A bounded Go test selection, classified by the host rather than a worker's cost label."""
import re

FULL_PLAN = {"packages": ["./..."], "tests": [], "race": True}
PROFILES = ("focused", "targeted", "full")
_PACKAGE = re.compile(r"\.(?:/[A-Za-z0-9_.-]+)*(?:/\.\.\.)?")
_TEST = re.compile(r"Test[A-Za-z0-9_]{1,150}")


def validate_plan(raw):
    if not isinstance(raw, dict) or set(raw) != {"packages", "tests", "race"}:
        raise ValueError("server test plan requires exactly packages, tests and race")
    packages, tests = raw["packages"], raw["tests"]
    if (not isinstance(packages, list) or not 1 <= len(packages) <= 128
            or any(not isinstance(p, str) or len(p) > 240 or not _PACKAGE.fullmatch(p)
                   or any(part in (".", "..") for part in p.split("/")[1:] if part != "...")
                   for p in packages)):
        raise ValueError("server packages must be module-local Go package paths")
    if (not isinstance(tests, list) or len(tests) > 128
            or any(not isinstance(t, str) or not _TEST.fullmatch(t) for t in tests)
            or type(raw["race"]) is not bool):
        raise ValueError("server tests must be exact Test names and race must be boolean")
    return {"packages": sorted(set(packages)), "tests": sorted(set(tests)), "race": raw["race"]}


def profile(plan):
    plan = validate_plan(plan)
    # This host's measured ./server selection loads the large designer source gate even
    # with -run. Naming one test cannot make that package's initialization lightweight.
    if (plan["race"] or any("..." in p or p.casefold() == "./server" or p.casefold().startswith("./server/")
                           for p in plan["packages"]) or len(plan["packages"]) > 4):
        return "full"
    if len(plan["packages"]) == 1 and 1 <= len(plan["tests"]) <= 8:
        return "focused"
    return "targeted"


def test_argv(plan):
    plan = validate_plan(plan)
    args = ["go", "test", "-p=1", "-count=1", "-timeout=600s", "-json"]
    if plan["race"]:
        args.append("-race")
    if plan["tests"]:
        args += ["-run", "^(" + "|".join(plan["tests"]) + ")$"]
    return args + plan["packages"]


def scope(plan):
    """Verification breadth is independent of the conservative race memory estimate."""
    plan = validate_plan(plan)
    if any('...' in p for p in plan['packages']):
        return 'broad'
    return 'focused' if plan['tests'] else 'targeted'


def timeout_seconds(plan, configured=1800):
    # A package's Go -timeout is not an aggregate suite deadline. Keep a hard
    # ceiling, but allow serial broad suites several package budgets. No heartbeat
    # can extend this indefinitely. Existing explicit shorter limits still scale.
    kind = scope(plan)
    multiplier = 4 if kind == 'broad' else max(1, (len(plan['packages']) + 3) // 4)
    return min(7200, configured * multiplier)


def changed_scope(paths):
    """Conservative minimum from a trusted base/candidate diff; callers add consumers."""
    packages, shared = set(), []
    for name in paths:
        if name.endswith('.md') or name.startswith('docs/'):
            continue
        parts = name.split('/')
        if len(parts) >= 3 and parts[0] == 'modules' and name.endswith('.go'):
            packages.add('./' + '/'.join(parts[:-1]))
        else:
            shared.append(name)
    return {'packages': sorted(packages), 'shared_paths': sorted(shared)}


def check_scope(plan, changes, reason=None):
    """Do not silently expand a local fix to full CI, or omit changed packages."""
    plan = validate_plan(plan)
    if reason is not None and (not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500):
        raise ValueError('server scope reason must contain 10-500 characters')
    selected = plan['packages']
    def covered(package):
        return any(p == './...' or p == package or
                   (p.endswith('/...') and (package == p[:-4] or package.startswith(p[:-3]))) for p in selected)
    if any(not covered(p) for p in changes['packages']):
        raise ValueError('server test plan omits a changed package')
    if changes['shared_paths'] and './...' not in selected and not reason:
        raise ValueError('shared server changes require full scope or a concrete dependency analysis via --server-scope-reason')
    if not changes['shared_paths'] and scope(plan) == 'broad' and not reason:
        raise ValueError('local server changes require affected packages; broad tests need --server-scope-reason')
    if plan['tests'] and not reason:
        raise ValueError('named-only acceptance needs --server-scope-reason explaining coverage')
    return {'changes': changes, 'plan': plan, 'reason': reason, 'verification_scope': scope(plan)}
