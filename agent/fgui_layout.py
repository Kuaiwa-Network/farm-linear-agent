"""Bounded source/export checks for explicitly selected FairyGUI components.

These checks compare serialized facts, not rendered appearance. Unity lifecycle,
relations, gears, SafeArea and art proportions still need runtime verification.
No Git, publisher, filesystem writes or resource ownership lives in this module.
"""
from collections import deque
import hashlib
import re
import xml.etree.ElementTree as ET

from .fgui_export import _Reader
from .ledger import LedgerError
from .uploads import _read_regular
from .preview_upload import _plain_path

# Native FairyGUI XML spellings, checked against serialized FillType values.
# The editor treats matchHeight/matchWidth as None; they are not aliases.
FILLS = {"none": 0, "scale": 1, "scaleMatchHeight": 2, "scaleMatchWidth": 3,
         "scaleFree": 4, "scaleNoBorder": 5}
MAX_COMPONENTS = 512
MAX_XML_BYTES = 2 << 20


def _same_size(value, native):
    try:
        return tuple(float(v) for v in (value or "").split(",")) == native
    except ValueError:
        return False


def selections(values, packages):
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or not 0 < len(values) <= MAX_COMPONENTS:
        raise LedgerError("layout checks require a bounded nonempty component selection")
    result = []
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}/[A-Za-z0-9_]{1,128}", value):
            raise LedgerError("layout component must be PACKAGE/RESOURCE_ID from package.xml")
        package, rid = value.split("/")
        if package not in packages or (package, rid) in result:
            raise LedgerError("layout component package is unselected or component is duplicate")
        result.append((package, rid))
    return tuple(result)


def _blocks(data):
    r = _Reader(data)
    count = r.count(">B", 20)
    short = r.boolean()
    offsets = [r.number(">H" if short else ">i") for _ in range(count)]
    starts = [v for v in offsets if v]
    # Native v7 writes offsets for empty blocks too, including a final offset
    # equal to the record length. Adjacent blocks can share that boundary.
    if starts != sorted(starts) or any(v < r.pos or v > len(data) for v in starts):
        raise LedgerError("layout component has invalid block offsets")

    def block(index):
        if index >= count or not offsets[index]:
            raise LedgerError("layout component is missing a required block")
        start = offsets[index]
        end = next((v for v in offsets[index + 1:] if v), len(data))
        return _Reader(data, start, end)
    return block


def _nodes(data, strings):
    r = _blocks(data)(2)
    result = {}
    for _ in range(r.count(">h", 32767)):
        raw = r.take(r.u16())
        block = _blocks(raw)
        base = block(0)
        kind = base.byte()
        src, package, identity, name = (base.string(strings) for _ in range(4))
        xy = (base.number(">i"), base.number(">i"))
        size = (base.number(">i"), base.number(">i")) if base.boolean() else None
        if not identity or identity in result:
            raise LedgerError("layout component has missing or duplicate child IDs")
        node = {"kind": kind, "src": src, "package": package, "name": name, "xy": xy, "size": size}
        if kind == 4:
            loader = block(5)
            node["url"] = loader.string(strings)
            loader.take(2)  # horizontal/vertical alignment
            node["fill"] = loader.byte()
        if src and kind >= 9:
            component = block(4)
            component.number(">h")  # page controller
            overrides = {}
            for _ in range(component.count(">h", 32767)):
                controller, page = component.string(strings), component.string(strings)
                if not controller or controller in overrides:
                    raise LedgerError("layout component has invalid controller overrides")
                overrides[controller] = page
            node["controllers"] = overrides
        result[identity] = node
    r.finished()
    return result


def _controller_defaults(data, strings):
    """Static native home pages; branch/variable-selected pages remain unknown."""
    r = _blocks(data)(1)
    result = {}
    for _ in range(r.count(">h", 32767)):
        block = _blocks(r.take(r.u16()))
        name = block(0).string(strings)
        pages = block(1)
        ids = []
        for _ in range(pages.count(">h", 32767)):
            ids.append(pages.string(strings))
            pages.string(strings)
        home = pages.byte()
        index = pages.number(">h") if home == 1 else 0 if home == 0 else -1
        if not name or name in result:
            raise LedgerError("layout component has invalid controller names")
        result[name] = ids[index] if 0 <= index < len(ids) else None
    r.finished()
    return result


def check(project, packages, selected, source_files, snapshot):
    """Check selected roots and their source component references in selected packages.

    All reads must match the caller's previously verified source hash map. The
    export snapshot is immutable. Unselected components are never decoded.
    """
    roots = selections(selected, packages)
    report = {"format": "farmbot-fgui-layout-v1", "status": "not_requested",
              "roots": list(selected or ()), "checked": [], "issues": [],
              "limitations": ["No runtime layout, SafeArea, gear/state or visual-reference verification."]}
    if not roots:
        return report
    descriptors = {p.name: p for p in snapshot.packages}
    by_id = {identity: name for name, identity in packages.items()}
    manifests, trees, defaults = {}, {}, {}
    total_bytes = 0

    def xml(relative):
        nonlocal total_bytes
        if relative not in source_files:
            raise LedgerError("layout source is absent from verified source: " + relative)
        path = project / relative
        _plain_path(path)
        data = _read_regular(path, MAX_XML_BYTES)
        total_bytes += len(data)
        if total_bytes > 64 << 20:
            raise LedgerError("layout source exceeds aggregate XML budget")
        if hashlib.sha256(data).hexdigest() != source_files.get(relative):
            raise LedgerError("layout source is untracked or differs from verified source: " + relative)
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise LedgerError("layout XML entities are refused")
        try:
            return ET.fromstring(data)
        except ET.ParseError:
            raise LedgerError("layout source XML is malformed: " + relative) from None

    def source(package, rid):
        key = (package, rid)
        if key in trees:
            return trees[key]
        if package not in manifests:
            manifest = xml("assets/" + package + "/package.xml")
            entries = {}
            for entry in manifest.findall("resources/*"):
                identity = entry.get("id")
                if not identity or identity in entries:
                    raise LedgerError("layout manifest has missing or duplicate resource IDs")
                entries[identity] = entry
            manifests[package] = entries
        entry = manifests[package].get(rid)
        if entry is None or entry.tag != "component":
            raise LedgerError("layout selection does not name a source component: " + package + "/" + rid)
        suffix = entry.get("path", "/").lstrip("/") + entry.get("name", "")
        if (not suffix.endswith(".xml") or "\\" in suffix or ":" in suffix
                or any(part in ("", ".", "..") for part in suffix.split("/"))):
            raise LedgerError("layout manifest component path is invalid")
        relative = "assets/" + package + "/" + suffix
        tree = xml(relative)
        if tree.tag != "component":
            raise LedgerError("layout source has the wrong XML root")
        trees[key] = (relative, tree)
        return relative, tree

    def issue(path, node, code, detail):
        report["issues"].append({"file": path, "node": node, "code": code, "detail": detail})
        if len(report["issues"]) > 2000:
            raise LedgerError("layout issues exceed report budget")

    pending, scheduled = deque(roots), set(roots)
    while pending:
        package, rid = pending.popleft()
        path, tree = source(package, rid)
        report["checked"].append(package + "/" + rid)
        desc = descriptors[package]
        exported = next((c for c in desc.components if c.identity == rid), None)
        if exported is None:
            issue(path, "", "component-missing", "Component is absent from the native package.")
            continue
        if not _same_size(tree.get("size"), (exported.width, exported.height)):
            issue(path, "", "component-size", f"Source size {tree.get('size')} != native {exported.width},{exported.height}.")
        try:
            nodes = _nodes(exported.data, desc.strings)
        except LedgerError as exc:
            issue(path, "", "component-unreadable", str(exc))
            continue
        for child in tree.findall("displayList/*"):
            identity = child.get("id", "")
            label = child.get("name") or identity
            native = nodes.get(identity)
            reference = None
            if child.tag == "component" and child.get("src"):
                target_package = by_id.get(child.get("pkg", packages[package]))
                if target_package:
                    reference = (target_package, child.get("src"))
                    if reference not in scheduled:
                        scheduled.add(reference)
                        if len(scheduled) > MAX_COMPONENTS:
                            raise LedgerError("layout component dependency budget exceeded")
                        pending.append(reference)
                else:
                    issue(path, label, "dependency-unselected", "Include the referenced component's package in the export.")
            if child.tag not in ("loader", "component"):
                continue
            if native is None:
                issue(path, label, "node-missing", "Source child ID is absent from the native component.")
                continue
            if reference and (native.get("src") != reference[1]
                              or (native.get("package") or packages[package]) != packages[reference[0]]):
                issue(path, label, "component-reference", "Native instance points to a different source component.")
            if child.tag == "loader":
                fill = child.get("fill", "none")
                if fill not in FILLS:
                    issue(path, label, "unsupported-fill", f"Unknown fill={fill!r}; use a native FairyGUI fill value (scale for proportional fit).")
                elif native.get("fill") != FILLS[fill]:
                    issue(path, label, "loader-fill", f"Source fill={fill} != native FillType {native.get('fill')}.")
                if child.get("size") and not _same_size(child.get("size"), native.get("size")):
                    issue(path, label, "loader-size", f"Source size {child.get('size')} != native explicit size {native.get('size')}.")
            for controller in child.findall("controller"):
                name, selected_index = controller.get("name"), controller.get("selected")
                target = source(*reference)[1] if reference else None
                definitions = target.findall("controller") if target is not None else ()
                definition = next((c for c in definitions if c.get("name") == name), None)
                pages = definition.get("pages", "").split(",")[::2] if definition is not None else []
                index = int(selected_index) if selected_index and re.fullmatch(r"[0-9]{1,5}", selected_index) else -1
                expected = pages[index] if 0 <= index < len(pages) else None
                actual = native.get("controllers", {}).get(name)
                if actual is None and reference:
                    if reference not in defaults:
                        target_desc = descriptors[reference[0]]
                        target_item = next((c for c in target_desc.components if c.identity == reference[1]), None)
                        try:
                            defaults[reference] = _controller_defaults(target_item.data, target_desc.strings) if target_item else {}
                        except LedgerError:
                            defaults[reference] = {}  # Unreadable defaults cannot establish a match.
                    actual = defaults[reference].get(name)
                if expected is None or actual != expected:
                    issue(path, label, "controller-override", f"Instance controller {name!r} selected={selected_index!r} was not preserved (native page={actual!r}). Save the selection with the native editor and verify its export.")
    report["status"] = "failed" if report["issues"] else "passed"
    return report
