"""Package-scoped mirroring, native importer templates, fences and recovery."""
import dataclasses
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from agent import fgui_install as install
from agent.fgui_export import snapshot_export
from agent.ledger import LedgerError
from test_fgui_export import package_bytes


def metadata(guid, kind):
    importer = {"folder": "DefaultImporter", "descriptor": "TextScriptImporter",
                "atlas": "TextureImporter", "sound": "AudioImporter"}[kind]
    text = f"fileFormatVersion: 2\nguid: {guid}\n"
    if kind == "folder":
        text += "folderAsset: yes\n"
    text += importer + ":\n  externalObjects: {}\n"
    if kind == "atlas":
        text += ("  internalIDToNameTable: []\n  serializedVersion: 13\n"
                 "  mipmaps:\n    enableMipMap: 0\n  textureSettings:\n    filterMode: 1\n"
                 "  platformSettings:\n  - buildTarget: DefaultTexturePlatform\n    overridden: 0\n"
                 "  spriteSheet:\n    sprites: []\n  maxTextureSize: 2048\n"
                 "  textureType: 8\n  alphaIsTransparency: 1\n")
    elif kind == "sound":
        text += ("  serializedVersion: 7\n  defaultSettings:\n    quality: 1\n"
                 "  platformSettingOverrides: {}\n  forceToMono: 0\n  normalize: 1\n"
                 "  loadInBackground: 0\n  ambisonic: 0\n  3D: 1\n")
    return (text + "  userData: preserved importer data\n  assetBundleName: \n  assetBundleVariant: \n").encode("utf-8")


class FguiInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="FGUI install ")
        self.addCleanup(self.tmp.cleanup)
        self.private = Path(self.tmp.name).resolve()
        self.client = self.private / "Client 空间"
        self.fairy = self.client / "Assets/GameRes/FairyRes"
        self.fairy.mkdir(parents=True)
        self.guid_count = 0
        self.make_client_package("One")
        self.make_client_package("Template")
        self.staging = self.private / "export staging"
        self.staging.mkdir()
        self.write_export("One", "pack0001")
        self.snapshot = snapshot_export(self.staging, {"One": "pack0001"})
        self.recovery = self.private / "new recovery"

    def guid(self):
        self.guid_count += 1
        return f"{self.guid_count:032x}"

    def make_client_package(self, name):
        folder = self.fairy / name
        folder.mkdir()
        (self.fairy / (name + ".meta")).write_bytes(metadata(self.guid(), "folder"))
        for filename, kind in ((name + "_fui.bytes", "descriptor"), (name + "_atlas0.png", "atlas"),
                               (name + "_old.mp3", "sound")):
            (folder / filename).write_bytes(b"previous hydrated artifact")
            (folder / (filename + ".meta")).write_bytes(metadata(self.guid(), kind))

    def write_export(self, name, identity, *, extra=None):
        items = [{"id": "atlas0", "kind": 4, "file": "atlas0.png"}]
        if extra:
            items.extend(extra)
        (self.staging / (name + "_fui.bytes")).write_bytes(package_bytes(name, identity, items=items))
        with Image.new("RGBA", (2, 2), "red") as image:
            image.save(self.staging / (name + "_atlas0.png"))

    def contents(self):
        return {p.relative_to(self.client).as_posix(): p.read_bytes() for p in self.client.rglob("*") if p.is_file()}

    def plan(self, **kwargs):
        return install.prepare_install(self.snapshot, self.client, ["One"], new_guid=self.guid, **kwargs)

    def apply(self, plan=None, fence=lambda: None):
        return install.apply_install(plan or self.plan(), self.recovery, fence=fence)

    def test_planning_is_read_only_and_existing_metadata_remains_byte_identical(self):
        before = self.contents()
        plan = self.plan()
        self.assertEqual(self.contents(), before)
        self.assertFalse(self.recovery.exists())
        after = dict(plan.after_files)
        for name in ("One.meta", "One/One_fui.bytes.meta", "One/One_atlas0.png.meta"):
            self.assertEqual(after[name], (self.fairy / name).read_bytes())
        self.assertEqual(plan.evidence()["deletes"], ["One/One_old.mp3", "One/One_old.mp3.meta"])
        self.assertNotIn(str(self.client), str(plan.evidence()))

    def test_only_changed_packages_install_with_exact_mirror_and_complete_recovery(self):
        before = self.contents()
        plan = self.plan()
        self.apply(plan)
        after = self.contents()
        unrelated = {n: b for n, b in before.items() if "/Template" in n}
        self.assertEqual({n: b for n, b in after.items() if "/Template" in n}, unrelated)
        self.assertFalse((self.fairy / "One/One_old.mp3").exists())
        self.assertFalse((self.fairy / "One/One_old.mp3.meta").exists())
        journal = json.loads((self.recovery / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "complete")
        for n in journal["before"]:
            self.assertEqual((self.recovery / n["backup"]).read_bytes(), dict(plan.before_files)[n["file"]])
        self.assertEqual(install._selected(self.fairy, ("One",)), (dict(plan.after_files), set(plan.after_dirs)))

    def test_new_package_folders_and_assets_copy_full_templates_with_fresh_global_guids(self):
        for path in self.staging.iterdir():
            path.unlink()
        self.write_export("Two", "pack0002")
        snapshot = snapshot_export(self.staging, {"Two": "pack0002"})
        plan = install.prepare_install(snapshot, self.client, ["Two"], new_guid=self.guid)
        self.apply(plan)
        for filename, kind in (("Two.meta", "folder"), ("Two/Two_fui.bytes.meta", "descriptor"),
                               ("Two/Two_atlas0.png.meta", "atlas")):
            data = (self.fairy / filename).read_bytes()
            guid, text = install._metadata(data, kind)
            self.assertIn("userData: preserved importer data", text)
            self.assertEqual(install._metadata_guids(self.client)[guid], 1)
        self.assertEqual(len([n for n in plan.after_files if n[0].endswith(".meta")]), 3)

    def test_unchanged_exported_dependencies_are_never_installed(self):
        self.write_export("Two", "pack0002")
        snapshot = snapshot_export(self.staging, {"One": "pack0001", "Two": "pack0002"})
        plan = install.prepare_install(snapshot, self.client, ["One"], new_guid=self.guid)
        self.apply(plan)
        self.assertFalse((self.fairy / "Two").exists())
        self.assertEqual(plan.packages, ("One",))

    def test_new_sound_and_nested_atlas_get_full_importers_and_folder_metadata(self):
        self.write_export("One", "pack0001", extra=[{"id": "sound", "kind": 2, "file": "clip.mp3"},
                                                     {"id": "atlas1", "kind": 4, "file": "nested/atlas.png"}])
        (self.staging / "One_clip.mp3").write_bytes(b"actual sound fixture")
        (self.staging / "One_nested").mkdir()
        with Image.new("RGBA", (2, 2), "blue") as image:
            image.save(self.staging / "One_nested/atlas.png")
        self.snapshot = snapshot_export(self.staging, {"One": "pack0001"})
        self.apply()
        for name, kind in (("One/One_clip.mp3.meta", "sound"), ("One/One_nested.meta", "folder"),
                           ("One/One_nested/atlas.png.meta", "atlas")):
            install._metadata((self.fairy / name).read_bytes(), kind)

    def test_stale_nested_files_and_orphan_metadata_empty_folder_are_removed_within_package(self):
        folder = self.fairy / "One/old sub"
        folder.mkdir()
        (self.fairy / "One/old sub.meta").write_bytes(metadata(self.guid(), "folder"))
        (folder / "dead.png").write_bytes(b"old image")
        (folder / "dead.png.meta").write_bytes(metadata(self.guid(), "atlas"))
        (folder / "orphan.png.meta").write_bytes(metadata(self.guid(), "atlas"))
        plan = self.plan()
        self.apply(plan)
        self.assertFalse(folder.exists())
        self.assertFalse((self.fairy / "One/old sub.meta").exists())

    def test_fresh_guid_collision_in_an_unrelated_assets_directory_refuses_without_writes(self):
        other = self.client / "Assets/Other"; other.mkdir()
        collision = self.guid()
        (other / "other.asset.meta").write_bytes(metadata(collision, "descriptor"))
        self.write_export("One", "pack0001", extra=[{"id": "sound", "kind": 2, "file": "clip.mp3"}])
        (self.staging / "One_clip.mp3").write_bytes(b"sound")
        self.snapshot = snapshot_export(self.staging, {"One": "pack0001"})
        before = self.contents()
        for guid in (collision, "bad", "F" * 33):
            with self.subTest(guid=guid), self.assertRaisesRegex(LedgerError, "fresh unique GUID"):
                install.prepare_install(self.snapshot, self.client, ["One"], new_guid=lambda: guid)
        self.assertEqual(before, self.contents())

    def test_malformed_existing_metadata_and_duplicate_affected_guid_refuse(self):
        path = self.fairy / "One/One_atlas0.png.meta"
        original = path.read_bytes()
        path.write_bytes(original.replace(b"  textureSettings:", b"  omitted:"))
        with self.assertRaisesRegex(LedgerError, "incomplete"):
            self.plan()
        path.write_bytes(original)
        other = self.client / "Assets/duplicate.meta"; other.write_bytes(original)
        with self.assertRaisesRegex(LedgerError, "globally unique"):
            self.plan()

    def test_missing_templates_or_unmodeled_misc_metadata_refuse_before_mutation(self):
        for path in self.staging.iterdir():
            path.unlink()
        self.write_export("Two", "pack0002", extra=[{"id": "data", "kind": 7, "file": "blob.bin"}])
        (self.staging / "Two_blob.bin").write_bytes(b"opaque")
        snapshot = snapshot_export(self.staging, {"Two": "pack0002"})
        with self.assertRaisesRegex(LedgerError, "metadata model"):
            install.prepare_install(snapshot, self.client, ["Two"], new_guid=self.guid)
        (self.staging / "Two_blob.bin").unlink()
        self.write_export("Two", "pack0002")
        snapshot = snapshot_export(self.staging, {"Two": "pack0002"})
        for path in self.fairy.rglob("*.png.meta"):
            path.unlink()
        with self.assertRaisesRegex(LedgerError, "template"):
            install.prepare_install(snapshot, self.client, ["Two"], new_guid=self.guid)

    def test_existing_missing_metadata_and_unmodeled_sound_suffix_refuse(self):
        path = self.fairy / "One/One_atlas0.png.meta"
        original = path.read_bytes(); path.unlink()
        with self.assertRaisesRegex(LedgerError, "missing its metadata"):
            self.plan()
        path.write_bytes(original)
        self.write_export("One", "pack0001", extra=[{"id": "sound", "kind": 2, "file": "payload.dll"}])
        (self.staging / "One_payload.dll").write_bytes(b"unmodeled sound")
        self.snapshot = snapshot_export(self.staging, {"One": "pack0001"})
        with self.assertRaisesRegex(LedgerError, "importer suffix"):
            self.plan()

    def test_unhydrated_client_files_invalid_changed_sets_and_export_tampering_refuse(self):
        path = self.fairy / "One/One_atlas0.png"; original = path.read_bytes()
        path.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
        with self.assertRaisesRegex(LedgerError, "hydrated"):
            self.plan()
        path.write_bytes(original)
        for changed in ([], ["One", "One"], ["Other"], "One"):
            with self.assertRaises(LedgerError):
                install.prepare_install(self.snapshot, self.client, changed)
        artifact = dataclasses.replace(self.snapshot.artifacts[-1], data=b"changed descriptor")
        altered = dataclasses.replace(self.snapshot, artifacts=(*self.snapshot.artifacts[:-1], artifact))
        with self.assertRaises(LedgerError):
            install.prepare_install(altered, self.client, ["One"])

    def test_delete_count_and_ratio_caps_refuse_a_scope_ruling_instead_of_partial_mirror(self):
        before = self.contents()
        for constant, value in (("MAX_DELETE_COUNT", 1), ("MAX_DELETE_RATIO", 0.1)):
            with patch.object(install, constant, value), self.assertRaisesRegex(LedgerError, "scope ruling"):
                self.plan()
        self.assertEqual(before, self.contents())

    def test_links_hardlinks_case_collisions_and_file_directory_replacement_refuse(self):
        alias = self.private / "alias"
        alias.symlink_to(self.client, target_is_directory=True)
        with self.assertRaisesRegex(LedgerError, "links"):
            install.prepare_install(self.snapshot, alias, ["One"])
        link = self.fairy / "One/linked.bin"; link.hardlink_to(self.fairy / "One/One_atlas0.png")
        with self.assertRaisesRegex(LedgerError, "unlinked"):
            self.plan()
        link.unlink()
        path = self.fairy / "One/One_atlas0.png"; path.unlink(); path.mkdir()
        with self.assertRaisesRegex(LedgerError, "file with a directory"):
            self.plan()

    def test_plan_staleness_refuses_before_recovery_or_client_mutation(self):
        plan = self.plan()
        (self.fairy / "One/One_atlas0.png").write_bytes(b"new unrelated work")
        before = self.contents()
        with self.assertRaisesRegex(LedgerError, "changed after"):
            self.apply(plan)
        self.assertFalse(self.recovery.exists())
        self.assertEqual(before, self.contents())

    def test_global_guid_inventory_change_after_planning_refuses_before_mutation(self):
        plan = self.plan()
        (self.client / "Assets/new.asset.meta").write_bytes(metadata(self.guid(), "descriptor"))
        before = self.contents()
        with self.assertRaisesRegex(LedgerError, "global metadata"):
            self.apply(plan)
        self.assertFalse(self.recovery.exists())
        self.assertEqual(before, self.contents())

    def test_new_metadata_preserves_every_template_byte_except_the_guid_even_with_crlf(self):
        template_path = self.fairy / "One/One_atlas0.png.meta"
        template = template_path.read_bytes().replace(b"\n", b"\r\n")
        template_path.write_bytes(template)
        self.write_export("One", "pack0001", extra=[{"id": "atlas1", "kind": 4, "file": "atlas1.png"}])
        with Image.new("RGBA", (2, 2)) as image:
            image.save(self.staging / "One_atlas1.png")
        self.snapshot = snapshot_export(self.staging, {"One": "pack0001"})
        result = dict(self.plan().after_files)["One/One_atlas1.png.meta"]
        old_guid = install._GUID.search(template.decode("utf-8")).group(1).encode("ascii")
        new_guid = install._GUID.search(result.decode("utf-8")).group(1).encode("ascii")
        self.assertEqual(result.replace(new_guid, old_guid, 1), template)

    def test_destination_change_at_the_second_fence_is_preserved_and_refuses_replacement(self):
        plan = self.plan()
        path = self.fairy / "One/One_atlas0.png"

        def race():
            if list((self.fairy / "One").glob(".farmbot-install-*")):
                path.write_bytes(b"other writer during temp")

        with self.assertRaisesRegex(LedgerError, "during temporary"):
            self.apply(plan, race)
        self.assertEqual(path.read_bytes(), b"other writer during temp")
        self.assertEqual(json.loads((self.recovery / "journal.json").read_text(encoding="utf-8"))["state"], "interrupted")

    def test_stop_before_first_mutation_changes_neither_client_nor_recovery(self):
        plan = self.plan(); before = self.contents()

        def stop():
            raise LedgerError("withdrawn")

        with self.assertRaisesRegex(LedgerError, "withdrawn"):
            self.apply(plan, stop)
        self.assertEqual(before, self.contents())
        self.assertFalse(self.recovery.exists())

    def test_stop_during_replacement_retains_before_bytes_journal_and_uncertain_temporary(self):
        plan = self.plan(); before = self.contents()

        def stop():
            if list((self.fairy / "One").glob(".farmbot-install-*")):
                raise LedgerError("withdrawn during copy")

        with self.assertRaisesRegex(LedgerError, "withdrawn during copy"):
            self.apply(plan, stop)
        journal = json.loads((self.recovery / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "interrupted")
        self.assertTrue(list((self.fairy / "One").glob(".farmbot-install-*")))
        self.assertEqual((self.fairy / "One/One_atlas0.png").read_bytes(), before["Assets/GameRes/FairyRes/One/One_atlas0.png"])
        for n in journal["before"]:
            self.assertEqual((self.recovery / n["backup"]).read_bytes(), dict(plan.before_files)[n["file"]])

    def test_io_failure_after_a_completed_write_keeps_progress_and_recovery(self):
        plan = self.plan(); original = install.os.replace
        calls = 0

        def fail(src, dst):
            nonlocal calls
            if str(src).split(".farmbot-install-")[-1] != str(src):
                calls += 1
                if calls == 2:
                    raise OSError("synthetic destination failure")
            return original(src, dst)

        with patch.object(install.os, "replace", side_effect=fail), self.assertRaises(OSError):
            self.apply(plan)
        journal = json.loads((self.recovery / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "interrupted")
        self.assertEqual(len([entry for entry in journal["completed"] if "write" in entry]), 1)

    def test_new_unplanned_file_during_apply_is_preserved_and_success_refused(self):
        plan = self.plan()
        added = False

        def race():
            nonlocal added
            path = self.fairy / "One/One_atlas0.png"
            if path.read_bytes() != dict(plan.before_files)["One/One_atlas0.png"] and not added:
                added = True
                (self.fairy / "One/new-owned-by-someone-else.bin").write_bytes(b"other writer")

        with self.assertRaisesRegex(LedgerError, "does not match"):
            self.apply(plan, race)
        self.assertTrue((self.fairy / "One/new-owned-by-someone-else.bin").exists())
        self.assertEqual(json.loads((self.recovery / "journal.json").read_text(encoding="utf-8"))["state"], "interrupted")

    def test_existing_or_in_checkout_recovery_and_forged_out_of_scope_plan_refuse(self):
        plan = self.plan(); self.recovery.mkdir()
        for root in (self.recovery, self.client / "private recovery", self.private):
            with self.assertRaisesRegex(LedgerError, "new private directory"):
                install.apply_install(plan, root, fence=lambda: None)
        self.recovery.rmdir()
        altered = dataclasses.replace(plan, after_files=((*plan.after_files, ("Template/evil.bin", b"bad"))))
        with self.assertRaisesRegex(LedgerError, "escapes"):
            self.apply(altered)
        self.assertFalse(self.recovery.exists())
