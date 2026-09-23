"""Repository scope for a worker attempt, independent of checkpoint prose."""

FIX_REPOSITORIES = ("Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract")


def write_repositories(item, skill):
    """An unrooted fix investigates; a rooted fix writes only its own repository."""
    if item["skill"] != "fix":
        return tuple(skill.writes)
    root = item.get("root_repo")
    if root is None:
        return ()
    if root not in FIX_REPOSITORIES or root not in skill.writes:
        raise ValueError(f"invalid fix repository root: {root}")
    return (root,)
