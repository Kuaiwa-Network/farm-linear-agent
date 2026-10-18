"""Repository scope for a worker attempt, from the skill manifest and independent of checkpoint prose."""

COORDINATION_ROOT = "@coordination"


def current_root(root_repo, skill):
    """The repository an attempt is rooted in: its recorded root_repo, else the manifest's initial_root.

    None means no root: a neutral attempt of a staged skill without an initial_root (fix before its first
    handoff), a controller-selected work coordination attempt, or an unstaged skill. Stages, the dispatch
    stage block and the ledger's handoff checks all resolve the root here (spec §9.4).
    """
    # Controller-only parked Client continuation. NULL still means the manifest's
    # initial root (Farm-Contract for work); it cannot represent this narrower scope.
    if skill.name == "work" and root_repo == COORDINATION_ROOT:
        return None
    return skill.initial_root if root_repo is None else root_repo


def runtime_can_launch(skill, runtime):
    """Whether a worker runtime can launch `skill`. Only Codex's workspace-write sandbox holds a staged attempt
    to its one writable root, and the fake runtime stands in for Codex offline; Claude has no equivalent
    repository write boundary. The scheduler refuses such a launch, and doctor names each enabled skill whose
    launches the configured runtime would have refused, both by this rule."""
    restricted = skill.staged or skill.name == 'work' and bool(skill.writes)
    return not restricted or runtime in ("codex", "fake")


def write_repositories(item, skill):
    """A staged skill writes only its current root, and nothing while neutral; an unstaged skill writes its
    whole manifest scope."""
    from .execution import single, REPOSITORIES
    if single(item):
        return REPOSITORIES
    if not skill.staged:
        return tuple(skill.writes)
    root = current_root(item.get("root_repo"), skill)
    from .presentation_contract import source_only
    if source_only(item) and root != 'farmgui':
        raise ValueError('FGUI source task has no Client write grant')
    if root is None:
        return ()
    if root not in skill.writes:
        raise ValueError(f"invalid {skill.name} repository root: {root}")
    return (root,)
