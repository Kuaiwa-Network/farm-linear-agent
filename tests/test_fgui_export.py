"""Synthetic descriptor/container and real-byte staging boundary regressions."""
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from agent import fgui_export as export
from agent.ledger import LedgerError


def raw(value):
    data = value.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def package_bytes(name="One", identity="pack0001", *, items=None, dependencies=(),
                  short=False, overrides=(), sprites=b"\0\0", hit_tests=None, branches=()):
    """Emit the documented Client container with minimal fully bounded records."""
    table = []

    def s(value):
        if value is None:
            return b"\xff\xfe"
        if value == "":
            return b"\xff\xfd"
        if value not in table:
            table.append(value)
        return struct.pack(">H", table.index(value))

    dep = struct.pack(">h", len(dependencies)) + b"".join(s(i) + s(n) for n, i in dependencies)
    dep += struct.pack(">h", len(branches)) + b"".join(s(b) for b in branches)
    items = items if items is not None else [{"id": "atlas0", "kind": 4, "file": "atlas0.png"}]
    records = []
    for item in items:
        kind = item["kind"]
        body = bytes([kind]) + s(item["id"]) + s(item.get("name")) + s(item.get("path")) + s(item.get("file"))
        body += b"\1" + struct.pack(">ii", item.get("width", 2), item.get("height", 2))
        if kind == 0:
            body += b"\0\1"
        elif kind == 1:
            body += b"\1" + struct.pack(">i", len(item.get("payload", b""))) + item.get("payload", b"")
        elif kind == 3:
            body += b"\0" + struct.pack(">i", len(item.get("payload", b""))) + item.get("payload", b"")
        elif kind == 5:
            body += struct.pack(">i", len(item.get("payload", b""))) + item.get("payload", b"")
        body += s(item.get("branch")) + b"\0\0"
        records.append(struct.pack(">i", len(body)) + body)
    entries = struct.pack(">h", len(items)) + b"".join(records)
    override = struct.pack(">i", len(overrides))
    for index, value in overrides:
        data = value.encode("utf-8")
        override += struct.pack(">Hi", index, len(data)) + data
    strings = struct.pack(">i", len(table)) + b"".join(raw(t) for t in table)
    blocks = [dep, entries, sprites, hit_tests, strings, override if overrides else None]
    header = b"FGUI" + struct.pack(">i", 7) + b"\0" + raw(identity) + raw(name) + bytes(20)
    offset = 2 + 6 * (2 if short else 4)
    offsets, body = [], b""
    for block in blocks:
        offsets.append(offset if block is not None else 0)
        if block is not None:
            offset += len(block)
            body += block
    index = b"\6" + bytes([short]) + b"".join(struct.pack(">H" if short else ">i", v) for v in offsets)
    return header + index + body


class FguiDescriptorTests(unittest.TestCase):
    def parse(self, data=None):
        return export.descriptor(data or package_bytes(), "One", "pack0001")

    def index(self, data):
        return 4 + 4 + 1 + 2 + 8 + 2 + 3 + 20

    def test_both_index_widths_produce_exact_inventory_and_dependency_identities(self):
        for short in (False, True):
            with self.subTest(short=short):
                d = self.parse(package_bytes(short=short, dependencies=(("Common", "cmcommon"),)))
                self.assertEqual(d.dependencies, (("Common", "cmcommon"),))
                self.assertEqual([(f.identity, f.kind, f.relative) for f in d.files],
                                 [("atlas0", "atlas", "One_atlas0.png")])
                self.assertEqual(d.ambiguous_names, ())

    def test_sound_misc_and_nested_portable_artifact_names(self):
        d = self.parse(package_bytes(items=[{"id": "audio", "kind": 2, "file": "voice.mp3"},
                                           {"id": "extra", "kind": 7, "file": "sub/data.bin"}]))
        self.assertEqual([(f.kind, f.relative) for f in d.files],
                         [("sound", "One_voice.mp3"), ("misc", "One_sub/data.bin")])

    def test_opaque_component_animation_font_lengths_and_branch_names_are_bounded(self):
        d = self.parse(package_bytes(items=[{"id": str(i), "kind": k, "name": "View", "branch": str(i),
                                            "payload": b"opaque runtime data"} for i, k in enumerate((1, 3, 5))],
                                    branches=("other",)))
        self.assertEqual((d.item_count, d.files, d.ambiguous_names), (3, (), ()))

    def test_inherited_duplicate_names_are_reported_but_resource_ids_remain_distinct(self):
        d = self.parse(package_bytes(items=[{"id": "first", "kind": 3, "name": "View", "path": "/A/"},
                                           {"id": "second", "kind": 3, "name": "View", "path": "/B/"}]))
        self.assertEqual(d.ambiguous_names, ("View",))
        with self.assertRaisesRegex(LedgerError, "identity"):
            self.parse(package_bytes(items=[{"id": "same", "kind": 3}, {"id": "same", "kind": 3}]))

    def test_extended_utf8_string_table_replaces_the_declared_file(self):
        # atlas ID is table index zero, filename is index one in this fixture.
        d = self.parse(package_bytes(overrides=((1, "图集.png"),)))
        self.assertEqual(d.files[0].relative, "One_图集.png")

    def test_duplicate_or_out_of_range_string_overrides_refuse(self):
        for overrides in (((1, "a"), (1, "b")), ((100, "a"),)):
            with self.subTest(overrides=overrides), self.assertRaises(LedgerError):
                self.parse(package_bytes(overrides=overrides))

    def test_lfs_pointer_bad_magic_unknown_version_and_compressed_container_refuse(self):
        good = package_bytes()
        cases = [b"version https://git-lfs.github.com/spec/v1\n", b"NOPE" + good[4:],
                 good[:4] + struct.pack(">i", 8) + good[8:], good[:8] + b"\1" + good[9:]]
        for data in cases:
            with self.subTest(prefix=data[:9]), self.assertRaises(LedgerError):
                self.parse(data)

    def test_selected_descriptor_identity_and_name_must_match(self):
        for data in (package_bytes(identity="pack0002"), package_bytes(name="Two")):
            with self.assertRaisesRegex(LedgerError, "selected package identity"):
                self.parse(data)

    def test_all_truncated_prefixes_fail_with_sanitized_domain_errors(self):
        good = package_bytes()
        for size in range(1, len(good)):
            with self.subTest(size=size), self.assertRaises(LedgerError):
                self.parse(good[:size])

    def test_index_count_boolean_missing_required_block_overlap_and_bad_offsets_refuse(self):
        good = bytearray(package_bytes())
        index = self.index(good)
        mutations = [(index, b"\7"), (index + 1, b"\2"), (index + 2, bytes(4)),
                     (index + 2, struct.pack(">i", -1)), (index + 2, struct.pack(">i", 1)),
                     (index + 6, bytes(good[index + 2:index + 6]))]
        for pos, value in mutations:
            data = good.copy(); data[pos:pos + len(value)] = value
            with self.subTest(pos=pos, value=value), self.assertRaises(LedgerError):
                self.parse(bytes(data))

    def test_negative_counts_and_bad_utf8_are_not_silent_empty_inventory(self):
        good = bytearray(package_bytes())
        index = self.index(good)
        offsets = struct.unpack(">6i", good[index + 2:index + 26])
        cases = []
        for number in (0, 1, 4):
            data = good.copy(); start = index + offsets[number]
            length = 4 if number == 4 else 2
            data[start:start + length] = b"\xff" * length
            cases.append(data)
        data = good.copy(); data[index + offsets[4] + 6] = 0xff; cases.append(data)
        for data in cases:
            with self.assertRaises(LedgerError):
                self.parse(bytes(data))

    def test_invalid_string_indices_record_lengths_and_booleans_refuse(self):
        good = bytearray(package_bytes())
        index = self.index(good)
        start = index + struct.unpack(">i", good[index + 6:index + 10])[0] + 2
        for pos, value in ((start, struct.pack(">i", -1)), (start, struct.pack(">i", 999999)),
                           (start + 5, b"\xff\xff"), (start + 13, b"\2")):
            data = good.copy(); data[pos:pos + len(value)] = value
            with self.subTest(pos=pos), self.assertRaises(LedgerError):
                self.parse(bytes(data))

    def test_self_and_duplicate_dependencies_refuse(self):
        for deps in ((("One", "pack0001"),), (("Common", "cmcommon"), ("Common", "cmcommon"))):
            with self.assertRaises(LedgerError):
                self.parse(package_bytes(dependencies=deps))

    def test_unmodeled_types_and_non_disk_types_carrying_a_file_refuse(self):
        for kind in (6, 8, 9, 10, 255, 0, 1, 3, 5):
            with self.subTest(kind=kind), self.assertRaisesRegex(LedgerError, "unmodeled"):
                self.parse(package_bytes(items=[{"id": "item", "kind": kind, "file": "asset.png"}]))

    def test_disk_item_requires_a_safe_nonempty_unique_non_descriptor_file(self):
        for file in (None, "", "../atlas.png", "/absolute.png", "a\\b.png", "C:atlas.png", "a:stream",
                     ".hidden", "nul.png", "a?.png", "a.png.meta", "a.png ", "fui.bytes"):
            with self.subTest(file=file), self.assertRaises(LedgerError):
                self.parse(package_bytes(items=[{"id": "a", "kind": 4, "file": file}]))
        with self.assertRaisesRegex(LedgerError, "duplicate"):
            self.parse(package_bytes(items=[{"id": "a", "kind": 4, "file": "A.png"},
                                           {"id": "b", "kind": 4, "file": "a.png"}]))

    def test_truncated_or_oversized_sprite_and_hit_test_records_refuse(self):
        for sprites in (b"\0", b"\0\1\0\2\xff\xfe", b"\0\1\xff\xff"):
            with self.assertRaises(LedgerError):
                self.parse(package_bytes(sprites=sprites))
        for hits in (b"\0", b"\0\1" + struct.pack(">i", 1000), b"\xff\xff"):
            with self.assertRaises(LedgerError):
                self.parse(package_bytes(hit_tests=hits))

    def test_valid_sprite_matches_its_atlas_and_out_of_bounds_or_wrong_atlas_refuses(self):
        # Minimal default table has atlas0 at index 0 and its filename at 1.
        payload = b"\0\1\0\0" + struct.pack(">iiii", 0, 0, 2, 2) + b"\0\0"
        sprites = b"\0\1" + struct.pack(">H", len(payload)) + payload
        self.assertEqual(self.parse(package_bytes(sprites=sprites)).item_count, 1)
        for altered in (payload[:2] + b"\0\1" + payload[4:],
                        payload[:4] + struct.pack(">iiii", 0, 0, 3, 2) + payload[20:]):
            with self.assertRaises(LedgerError):
                self.parse(package_bytes(sprites=b"\0\1" + struct.pack(">H", len(altered)) + altered))


class FguiExportSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="FGUI export ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "staging 空间"
        self.root.mkdir()
        self.packages = {"One": "pack0001"}
        self.write_package("One", "pack0001")

    def write_package(self, name, identity, *, items=None, dependencies=()):
        (self.root / (name + "_fui.bytes")).write_bytes(package_bytes(name, identity, items=items, dependencies=dependencies))
        with Image.new("RGBA", (2, 2), "red") as image:
            image.save(self.root / (name + "_atlas0.png"))

    def snapshot(self):
        return export.snapshot_export(self.root, self.packages)

    def test_snapshot_has_exact_immutable_bytes_hashes_and_no_host_paths(self):
        snapshot = self.snapshot()
        evidence = snapshot.evidence()
        self.assertEqual([a["file"] for a in evidence["artifacts"]], ["One_atlas0.png", "One_fui.bytes"])
        self.assertNotIn(str(self.root), str(evidence))
        captured = snapshot.artifacts[0].data
        (self.root / "One_atlas0.png").unlink()
        self.assertEqual(snapshot.artifacts[0].data, captured)
        with self.assertRaises(AttributeError):
            snapshot.artifacts = ()

    def test_dependencies_require_selected_and_exported_exact_ids(self):
        self.write_package("One", "pack0001", dependencies=(("Common", "cmcommon"),))
        with self.assertRaisesRegex(LedgerError, "dependency"):
            self.snapshot()
        self.packages["Common"] = "cmcommon"
        self.write_package("Common", "cmcommon")
        self.assertEqual(len(self.snapshot().packages), 2)
        self.packages["Common"] = "pack0002"
        self.write_package("Common", "pack0002")
        with self.assertRaisesRegex(LedgerError, "dependency"):
            self.snapshot()

    def test_missing_descriptor_missing_atlas_and_orphan_artifact_refuse(self):
        for filename in ("One_fui.bytes", "One_atlas0.png"):
            data = (self.root / filename).read_bytes()
            (self.root / filename).unlink()
            with self.assertRaises(LedgerError):
                self.snapshot()
            (self.root / filename).write_bytes(data)
        (self.root / "One_old.png").write_bytes(b"orphan")
        with self.assertRaisesRegex(LedgerError, "orphan"):
            self.snapshot()

    def test_old_nested_package_export_is_not_silently_accepted(self):
        nested = self.root / "One"; nested.mkdir()
        (self.root / "One_atlas0.png").rename(nested / "One_atlas0.png")
        with self.assertRaisesRegex(LedgerError, "orphan"):
            self.snapshot()

    def test_valid_sound_misc_and_nested_file_inventory(self):
        items = [{"id": "atlas0", "kind": 4, "file": "atlas0.png"},
                 {"id": "sound", "kind": 2, "file": "clip.wav"},
                 {"id": "other", "kind": 7, "file": "sub/blob.dat"}]
        self.write_package("One", "pack0001", items=items)
        (self.root / "One_clip.wav").write_bytes(b"RIFF fixture")
        (self.root / "One_sub").mkdir()
        (self.root / "One_sub/blob.dat").write_bytes(b"opaque misc bytes")
        self.assertEqual(len(self.snapshot().artifacts), 4)

    def test_package_map_must_be_explicit_bounded_and_unique_portable_identities(self):
        for packages in ({}, [], {"../One": "pack0001"}, {"One": "short"},
                         {"One": "pack0001", "one": "pack0002"},
                         {"One": "pack0001", "Two": "pack0001"}, {"NUL": "pack0001"}):
            with self.subTest(packages=packages), self.assertRaises(LedgerError):
                export.snapshot_export(self.root, packages)

    def test_lfs_pointer_empty_and_malformed_png_refuse(self):
        for content in (b"version https://git-lfs.github.com/spec/v1\n", b"", b"not a png"):
            (self.root / "One_atlas0.png").write_bytes(content)
            with self.subTest(content=content), self.assertRaises(LedgerError):
                self.snapshot()

    def test_declared_atlas_must_actually_be_png(self):
        stream = io.BytesIO()
        with Image.new("RGB", (2, 2)) as image:
            image.save(stream, format="JPEG")
        (self.root / "One_atlas0.png").write_bytes(stream.getvalue())
        with self.assertRaises(LedgerError):
            self.snapshot()

    def test_atlas_decoded_dimensions_must_match_descriptor(self):
        self.write_package("One", "pack0001", items=[{"id": "a", "kind": 4, "file": "atlas0.png", "width": 3}])
        with self.assertRaisesRegex(LedgerError, "decoded PNG"):
            self.snapshot()

    def test_dependency_cycle_and_cross_package_artifact_collision_refuse(self):
        self.packages["Two"] = "pack0002"
        self.write_package("One", "pack0001", dependencies=(("Two", "pack0002"),))
        self.write_package("Two", "pack0002", dependencies=(("One", "pack0001"),))
        with self.assertRaisesRegex(LedgerError, "cycle"):
            self.snapshot()
        for p in self.root.iterdir():
            p.unlink()
        self.packages = {"One": "pack0001", "One_sub": "pack0002"}
        (self.root / "One_fui.bytes").write_bytes(package_bytes(items=[{"id": "data", "kind": 7, "file": "sub_fui.bytes"}]))
        self.write_package("One_sub", "pack0002")
        with self.assertRaisesRegex(LedgerError, "collide"):
            self.snapshot()

    def test_extra_metadata_hidden_files_and_empty_directories_refuse(self):
        for name, folder in (("One_atlas0.png.meta", False), (".hidden", False), ("empty", True)):
            path = self.root / name
            path.mkdir() if folder else path.write_bytes(b"unexpected")
            with self.subTest(name=name), self.assertRaises(LedgerError):
                self.snapshot()
            path.rmdir() if folder else path.unlink()

    def test_hardlinked_file_and_symlinked_file_directory_or_ancestor_refuse(self):
        link = self.root / "extra.bin"
        link.hardlink_to(self.root / "One_atlas0.png")
        with self.assertRaisesRegex(LedgerError, "unlinked"):
            self.snapshot()
        link.unlink()
        for target in (self.root / "One_atlas0.png", self.root):
            link.symlink_to(target, target_is_directory=target.is_dir())
            with self.assertRaisesRegex(LedgerError, "links"):
                self.snapshot()
            link.unlink()
        alias = self.root.parent / "linked source"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(LedgerError, "links"):
            export.snapshot_export(alias, self.packages)

    def test_relative_root_and_parent_traversal_refuse(self):
        for root in (Path("relative"), self.root / ".." / self.root.name):
            with self.assertRaisesRegex(LedgerError, "absolute"):
                export.snapshot_export(root, self.packages)

    def test_file_byte_count_aggregate_and_descriptor_limits_are_independent(self):
        for constant, value in (("MAX_FILES", 1), ("MAX_FILE_BYTES", 1), ("MAX_BYTES", 1),
                                ("MAX_DESCRIPTOR_BYTES", 1), ("MAX_PACKAGES", 0)):
            with self.subTest(constant=constant), patch.object(export, constant, value), self.assertRaises(LedgerError):
                self.snapshot()

    def test_file_mutation_membership_change_or_new_hardlink_during_read_refuse(self):
        original = export._read_regular
        for action in ("replace", "new", "link"):
            mutated = False

            def racing(path, limit):
                nonlocal mutated
                data = original(path, limit)
                if not mutated:
                    mutated = True
                    if action == "replace":
                        path.write_bytes(data + b"changed")
                    elif action == "new":
                        (self.root / "new.bin").write_bytes(b"new")
                    else:
                        (self.root / "new.bin").hardlink_to(path)
                return data

            with self.subTest(action=action), patch.object(export, "_read_regular", side_effect=racing), self.assertRaises(LedgerError):
                self.snapshot()
            if (self.root / "new.bin").exists():
                (self.root / "new.bin").unlink()
            self.write_package("One", "pack0001")

    def test_file_mutation_during_png_decode_refuses_before_snapshot_return(self):
        original = export.read_image

        def racing(path, root):
            result = original(path, root)
            path.write_bytes(result.data + b"changed")
            return result

        with patch.object(export, "read_image", side_effect=racing), self.assertRaisesRegex(LedgerError, "changed"):
            self.snapshot()

    def test_normal_import_needs_no_optional_imaging_dependency(self):
        # Optional Pillow is imported only for an actual atlas snapshot.
        import subprocess
        import sys
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                                 "import sys;sys.path.insert(0,sys.argv[1]);import agent.fgui_export",
                                 str(Path(__file__).resolve().parents[1])], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
