"""Source/export disagreements that previously passed UI binding/click tests."""
import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

from agent.fgui_export import descriptor
from agent.fgui_layout import check, selections, _nodes, _blocks
from agent.ledger import LedgerError
from test_fgui_export import package_bytes


def blocks(values, short=True):
    offset = 2 + len(values) * (2 if short else 4)
    offsets, body = [], b""
    for value in values:
        offsets.append(offset if value is not None else 0)
        if value is not None:
            offset += len(value)
            body += value
    return bytes([len(values), short]) + b"".join(struct.pack(">H" if short else ">i", n) for n in offsets) + body


def component_payload(s, *, fill=1, overrides=(), child=False, size=(128, 128), short=True):
    # GObject.Setup_BeforeAdd; GLoader block 5; GComponent.Setup_AfterAdd block 4.
    base = bytes([9 if child else 4]) + s("child" if child else None) + s(None) + s("n1") + s("honorBtn" if child else "icon")
    base += struct.pack(">ii?", 0, 0, size is not None)
    if size is not None:
        base += struct.pack(">ii", *size)
    extra = struct.pack(">hh", -1, len(overrides)) + b"".join(s(n) + s(p) for n, p in overrides) + b"\0\0"
    node = blocks([base, None, None, None, extra if child else None,
                   None if child else s(None) + bytes([1, 1, fill])], short)
    children = struct.pack(">hH", 1, len(node)) + node
    return blocks([None, None, children], short)


def child_payload(s):
    controller = blocks([s("featured") + b"\0", b"\0\2" + s("normal") + s("Normal")
                         + s("banner") + s("Banner") + b"\0", b"\0\0"])
    return blocks([None, struct.pack(">hH", 1, len(controller)) + controller, b"\0\0"])


class LayoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="UI layout 空间 ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.folder = self.root / "assets/One"
        self.folder.mkdir(parents=True)
        (self.folder / "package.xml").write_text(
            '<packageDescription id="pack0001"><resources>'
            '<component id="view" name="View.xml" path="/"/>'
            '<component id="child" name="Child.xml" path="/"/>'
            '<component id="unrelated" name="Old.xml" path="/"/>'
            '</resources></packageDescription>', encoding="utf-8")
        self.write('<loader id="n1" name="icon" size="128,128" fill="scale"/>')
        self.write('', file="Child.xml", prefix='<controller name="featured" pages="normal,Normal,banner,Banner"/>')
        (self.folder / "Old.xml").write_text('unrelated invalid XML', encoding="utf-8")

    def write(self, nodes, *, file="View.xml", prefix="", size="200,200"):
        (self.folder / file).write_text(f'<component size="{size}">{prefix}<displayList>{nodes}</displayList></component>', encoding="utf-8")

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.folder.glob("*.xml")}

    def snapshot(self, **kwargs):
        data = package_bytes(items=[
            {"kind": 3, "id": "view", "name": "View", "width": 200, "height": 200,
             "payload": lambda s: component_payload(s, **kwargs)},
            {"kind": 3, "id": "child", "width": 200, "height": 200, "payload": child_payload},
            {"kind": 3, "id": "unrelated", "payload": b"opaque unrelated data"}])
        return SimpleNamespace(packages=(descriptor(data, "One", "pack0001"),))

    def check(self, **kwargs):
        return check(self.root, {"One": "pack0001"}, ["One/view"], self.hashes(), self.snapshot(**kwargs))

    def test_valid_scaling_checks_native_bytes_and_leaves_unrelated_debt_opaque(self):
        before = self.hashes()
        for short in (True, False):
            result = self.check(short=short)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["checked"], ["One/view"])
        self.assertEqual(before, self.hashes())

    def test_guild_regression_unknown_scale_to_fit_does_not_become_a_success(self):
        self.write('<loader id="n1" name="icon" size="128,128" fill="scaleToFit"/>')
        report = self.check(fill=0)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["issues"][0]["code"], "unsupported-fill")
        self.assertEqual(report["issues"][0]["node"], "icon")

    def test_native_fill_spellings_preserve_serialized_modes(self):
        # FairyGUI source tokens and runtime FillType enum; neither source nor
        # native bytes may be rewritten to make the comparison succeed.
        for spelling, native in (("none", 0), ("scale", 1), ("scaleMatchHeight", 2),
                                 ("scaleMatchWidth", 3), ("scaleFree", 4), ("scaleNoBorder", 5)):
            with self.subTest(spelling=spelling):
                self.write(f'<loader id="n1" name="icon" size="128,128" fill="{spelling}"/>')
                before = self.hashes()
                self.assertEqual(self.check(fill=native)["status"], "passed")
                self.assertEqual(before, self.hashes())
                report = self.check(fill=(native + 1) % 6)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["issues"][0]["code"], "loader-fill")

    def test_non_native_fill_aliases_cannot_certify_fallback_none(self):
        for spelling in ("matchHeight", "matchWidth"):
            with self.subTest(spelling=spelling):
                self.write(f'<loader id="n1" name="icon" size="128,128" fill="{spelling}"/>')
                report = self.check(fill=0)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["issues"][0]["code"], "unsupported-fill")

    def test_valid_source_spelling_with_native_none_still_fails(self):
        report = self.check(fill=0)
        self.assertEqual(report["issues"][0]["code"], "loader-fill")
        self.assertIn("native FillType 0", report["issues"][0]["detail"])

    def test_controller_page_must_survive_export_and_reference_is_checked(self):
        self.write('<component id="n1" name="honorBtn" src="child"><controller name="featured" selected="1"/></component>')
        failed = self.check(child=True)
        self.assertEqual(failed["checked"], ["One/view", "One/child"])
        self.assertEqual(failed["issues"][0]["code"], "controller-override")
        self.assertEqual(self.check(child=True, overrides=(("featured", "banner"),))["status"], "passed")
        self.assertEqual(self.check(child=True, overrides=(("featured", "normal"),))["status"], "failed")

    def test_redundant_override_can_use_the_actual_native_home_page(self):
        self.write('<component id="n1" name="honorBtn" src="child"><controller name="featured" selected="0"/></component>')
        self.assertEqual(self.check(child=True)["status"], "passed")

    def test_repeated_and_cyclic_references_are_checked_once(self):
        self.write('<component id="n1" src="child"/><component id="n2" src="child"/>')
        self.write('<component id="parent" src="view"/>', file="Child.xml")
        result = self.check(child=True)
        self.assertEqual(result["checked"], ["One/view", "One/child"])
        self.assertEqual({i["code"] for i in result["issues"]}, {"node-missing"})

    def test_component_and_explicit_loader_dimensions_are_compared(self):
        self.write('<loader id="n1" size="100,100" fill="scale"/>', size="1080,1920")
        self.assertEqual({i["code"] for i in self.check()["issues"]}, {"component-size", "loader-size"})

    def test_dropped_explicit_size_fails_but_numeric_formatting_is_not_a_difference(self):
        self.assertEqual(self.check(size=None)["issues"][0]["code"], "loader-size")
        self.write('<loader id="n1" size="128.0, 128" fill="scale"/>', size="200, 200.0")
        self.assertEqual(self.check()["status"], "passed")

    def test_unselected_export_explicitly_reports_no_layout_verification(self):
        result = check(self.root, {"One": "pack0001"}, None, {}, self.snapshot())
        self.assertEqual((result["status"], result["checked"]), ("not_requested", []))

    def test_selection_validation_missing_resource_and_missing_native_component(self):
        for values in ([], ["One/../view"], ["Other/view"], ["One/view"] * 2, ["One/view"] * 513):
            with self.subTest(values=values[:2]), self.assertRaises(LedgerError):
                selections(values, {"One": "pack0001"})
        with self.assertRaisesRegex(LedgerError, "does not name"):
            check(self.root, {"One": "pack0001"}, ["One/missing"], self.hashes(), self.snapshot())
        desc = descriptor(package_bytes(), "One", "pack0001")
        result = check(self.root, {"One": "pack0001"}, ["One/view"], self.hashes(), SimpleNamespace(packages=(desc,)))
        self.assertEqual(result["issues"][0]["code"], "component-missing")

    def test_unreadable_scoped_component_is_retained_as_a_failed_finding(self):
        self.write('', file="Old.xml")
        report = check(self.root, {"One": "pack0001"}, ["One/unrelated"], self.hashes(), self.snapshot())
        self.assertEqual(report["status"], "failed")
        self.assertIn("component-unreadable", {i["code"] for i in report["issues"]})

    def test_modified_untracked_entity_and_escaping_sources_are_refused(self):
        hashes = self.hashes()
        self.write('')
        with self.assertRaisesRegex(LedgerError, "verified source"):
            check(self.root, {"One": "pack0001"}, ["One/view"], hashes, self.snapshot())
        self.write('<!DOCTYPE x>')
        with self.assertRaisesRegex(LedgerError, "entities"):
            self.check()
        manifest = self.folder / "package.xml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('path="/"', 'path="/../"'), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "path is invalid"):
            self.check()

    def test_truncated_native_records_never_pass_or_escape_as_struct_errors(self):
        desc = self.snapshot().packages[0]
        data = desc.components[0].data
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(LedgerError):
                _nodes(data[:length], desc.strings)

    def test_native_empty_block_boundaries_are_valid_but_required_reads_remain_bounded(self):
        # Native components use repeated offsets, including empty blocks at EOF.
        data = blocks([b"a", b"", b"b", b"", b""])
        block = _blocks(data)
        self.assertEqual(block(0).take(1), b"a")
        self.assertEqual(block(2).take(1), b"b")
        for index in (1, 3, 4):
            block(index).finished()
            with self.assertRaises(LedgerError):
                block(index).byte()


if __name__ == '__main__':
    unittest.main()
