"""Skill registry: each skills/<name>/ holds SKILL.md and skill.json (spec §5)."""
from dataclasses import dataclass
import json
from pathlib import Path

from .kw_ops import GRANTS

TRIGGERS = ("delegation", "mention")
REQUIRED = ("name", "trigger", "intents", "writes", "resources", "gates", "mcp", "budget")
# The stage keys (spec §9.5), then opt_in and exclusive (Phase B plan, P1 and P8).
OPTIONAL = ("initial_root", "staged", "reads", "opt_in", "exclusive")
BUDGET_KEYS = ("lease_seconds", "max_hours", "renew_minutes")


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    trigger: tuple
    intents: tuple
    writes: tuple
    resources: tuple
    gates: tuple
    mcp: tuple
    budget: dict
    path: Path
    # Optional stage keys (spec §9.5). A staged skill writes one repository per attempt: its recorded root,
    # else its initial_root, else nothing (a neutral start, as fix). reads names repositories to check out
    # read-only; nothing uses it yet (spec §9.6).
    initial_root: str | None = None
    staged: bool = False
    reads: tuple = ()
    # An opt-in skill is loaded everywhere but runs only where the host's enabled_skills names it (P1). Of all
    # exclusive skills together, at most one attempt may run at a time (P8, spec §5.8).
    opt_in: bool = False
    exclusive: bool = False

    @property
    def skill_md(self):
        return self.path / "SKILL.md"


def _load_one(directory):
    manifest_path = directory / "skill.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkillError(f"{manifest_path}: unreadable manifest") from exc
    missing = set(REQUIRED) - set(manifest)
    if missing:
        raise SkillError(f"{manifest_path}: missing {sorted(missing)}")
    if manifest["name"] != directory.name:
        raise SkillError(f"{manifest_path}: name must equal the directory name")
    unknown_keys = sorted(set(manifest) - set(REQUIRED) - set(OPTIONAL))
    if unknown_keys:  # a misspelled stage key must not load as its default: "stagged": true would run unstaged
        raise SkillError(f"{manifest_path}: unknown key {unknown_keys[0]!r}")
    for key in ("trigger", "intents", "writes", "resources", "gates", "mcp"):
        if not isinstance(manifest[key], list) or not all(isinstance(v, str) and v for v in manifest[key]):
            raise SkillError(f"{manifest_path}: {key} must be a list of strings")
    unknown = [grant for grant in manifest["mcp"] if grant not in GRANTS]
    if unknown:
        raise SkillError(f"{manifest_path}: unknown mcp grant {unknown[0]!r}")
    staged = manifest.get("staged", False)
    reads = manifest.get("reads", [])
    initial_root = manifest.get("initial_root")
    if type(staged) is not bool:
        raise SkillError(f"{manifest_path}: staged must be true or false")
    if staged and not manifest["writes"]:
        raise SkillError(f"{manifest_path}: a staged skill needs writes")
    if not isinstance(reads, list) or not all(isinstance(v, str) and v for v in reads):
        raise SkillError(f"{manifest_path}: reads must be a list of strings")
    if initial_root is not None and (not staged or initial_root not in manifest["writes"]):
        raise SkillError(f"{manifest_path}: initial_root must name one of a staged skill's writes")
    opt_in = manifest.get("opt_in", False)
    exclusive = manifest.get("exclusive", False)
    for key, value in (("opt_in", opt_in), ("exclusive", exclusive)):
        if type(value) is not bool:
            raise SkillError(f"{manifest_path}: {key} must be true or false")
    if not manifest["trigger"] or not set(manifest["trigger"]) <= set(TRIGGERS):
        raise SkillError(f"{manifest_path}: trigger must be a nonempty subset of {TRIGGERS}")
    budget = manifest["budget"]
    if not isinstance(budget, dict) or set(budget) != set(BUDGET_KEYS):
        raise SkillError(f"{manifest_path}: budget needs exactly {BUDGET_KEYS}")
    if any(not isinstance(budget[k], (int, float)) or budget[k] <= 0 for k in BUDGET_KEYS):
        raise SkillError(f"{manifest_path}: budget values must be positive numbers")
    if not (directory / "SKILL.md").is_file():
        raise SkillError(f"{directory}: SKILL.md missing")
    return Skill(name=manifest["name"], trigger=tuple(manifest["trigger"]), intents=tuple(manifest["intents"]),
                 writes=tuple(manifest["writes"]), resources=tuple(manifest["resources"]), gates=tuple(manifest["gates"]),
                 mcp=tuple(manifest["mcp"]), budget=dict(budget), path=directory,
                 initial_root=initial_root, staged=staged, reads=tuple(reads), opt_in=opt_in, exclusive=exclusive)


def load_skills(root):
    root = Path(root)
    skills = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if (directory / "skill.json").exists():
            skill = _load_one(directory)
            skills[skill.name] = skill
    if not skills:
        raise SkillError(f"{root}: no skills found")
    return skills


def enabled_skills(skills, names, *, authority):
    """The loaded skills this host runs: when its private config names none, every one but the opt-in skills
    (spec §9.11; Phase B plan P1), so a checkout that ships an opt-in skill starts nothing new until a host names it.

    A name the checkout lacks is a configuration error, not a skill silently left off; `chat` must stay, because
    every route that is not write work falls back to it, and so it cannot be opt-in; and every skill that runs needs
    its part of the dispatch AUTHORITY (`authority`, keyed by skill name), without which each of its launches would
    fail only after its worktrees were made."""
    if names is None:
        selected = {name: skill for name, skill in skills.items() if not skill.opt_in}
        if "chat" in skills and "chat" not in selected:
            raise SkillError("chat cannot be opt-in: every route that is not write work falls back to it")
    else:
        unknown = sorted(set(names) - set(skills))
        if unknown:
            raise SkillError(f"enabled_skills names skills this checkout does not have: {', '.join(unknown)}")
        if "chat" not in names:
            raise SkillError("enabled_skills must include chat: every route that is not write work falls back to it")
        selected = {name: skill for name, skill in skills.items() if name in names}
    unbriefed = sorted(set(selected) - set(authority))
    if unbriefed:
        raise SkillError(f"no dispatch AUTHORITY part for enabled skills: {', '.join(unbriefed)}")
    return selected
