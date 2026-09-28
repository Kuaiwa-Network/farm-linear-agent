"""Repository scope for a worker attempt, from the skill manifest and independent of checkpoint prose."""


def current_root(root_repo, skill):
    """The repository an attempt is rooted in: its recorded root_repo, else the manifest's initial_root.

    None means no root: a neutral attempt of a staged skill without an initial_root (fix before its first
    handoff), or any unstaged skill, which is never rooted. Stages, the dispatch stage block and the ledger's
    handoff checks all resolve the root here (spec §9.4).
    """
    return skill.initial_root if root_repo is None else root_repo


def runtime_can_launch(skill, runtime):
    """Whether a worker runtime can launch `skill`. Only Codex's workspace-write sandbox holds a staged attempt
    to its one writable root, and the fake runtime stands in for Codex offline; Claude has no equivalent
    repository write boundary. The scheduler refuses such a launch, and doctor names each enabled skill whose
    launches the configured runtime would have refused, both by this rule."""
    return not skill.staged or runtime in ("codex", "fake")


def write_repositories(item, skill):
    """A staged skill writes only its current root, and nothing while neutral; an unstaged skill writes its
    whole manifest scope."""
    if not skill.staged:
        return tuple(skill.writes)
    root = current_root(item.get("root_repo"), skill)
    if root is None:
        return ()
    if root not in skill.writes:
        raise ValueError(f"invalid {skill.name} repository root: {root}")
    return (root,)
