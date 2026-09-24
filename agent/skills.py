"""Skill registry: each skills/<name>/ holds SKILL.md and skill.json (spec §5)."""
from dataclasses import dataclass
import json
from pathlib import Path

from .kw_ops import GRANTS

TRIGGERS = ("delegation", "mention")
REQUIRED = ("name", "trigger", "intents", "writes", "resources", "gates", "mcp", "budget")
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
    for key in ("trigger", "intents", "writes", "resources", "gates", "mcp"):
        if not isinstance(manifest[key], list) or not all(isinstance(v, str) and v for v in manifest[key]):
            raise SkillError(f"{manifest_path}: {key} must be a list of strings")
    unknown = [grant for grant in manifest["mcp"] if grant not in GRANTS]
    if unknown:
        raise SkillError(f"{manifest_path}: unknown mcp grant {unknown[0]!r}")
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
                 mcp=tuple(manifest["mcp"]), budget=dict(budget), path=directory)


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
