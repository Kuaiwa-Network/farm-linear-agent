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
    if (plan["race"] or any("..." in p or p == "./server" or p.startswith("./server/")
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
