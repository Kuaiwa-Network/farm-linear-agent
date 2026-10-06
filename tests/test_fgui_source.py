"""Actual local Git fixtures for owned UI source and scoped LFS identity."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent import fgui_source as source
from agent.ledger import LedgerError
from agent.worktrees import READ_ONLY_GIT, WorktreeError, Worktrees
import agent.worktrees


PACKAGES = {"Notice": "notice01"}


def git(path, *args):
    return subprocess.check_output(["git", *READ_ONLY_GIT, "-c", "user.name=test", "-c", "user.email=test@test",
                                    *args], cwd=path, text=True, encoding="utf-8", stderr=subprocess.PIPE).strip()


def pointer(data):
    return ("version https://git-lfs.github.com/spec/v1\noid sha256:" + hashlib.sha256(data).hexdigest()
            + "\nsize " + str(len(data)) + "\n").encode("ascii")


class SourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="UI source 中文 ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        global_config = self.root / "global.gitconfig"; global_config.write_text("", encoding="utf-8")
        env = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start(); self.addCleanup(env.stop)
        self.global_config = global_config
        self.origin = self.root / "origin"; self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        for name, data in {"UI.fairy": b"project\n", ".gitignore": b".objs/\n",
                           "empty.txt": b"", "assets/Notice/说明 file.xml": "内容\n".encode("utf-8")}.items():
            file = self.origin / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(data)
        self.commit(self.origin)
        self.trees = Worktrees(self.root / "repos", self.root / "worktrees", {"farmgui": str(self.origin)})
        self.item, self.branch = "item-1", "farmbot/farm-1"
        self.path = self.trees.add("farmgui", self.item, self.branch)
        self.head = git(self.path, "rev-parse", "HEAD")

    def commit(self, path=None):
        path = path or self.path
        git(path, "add", "--all"); git(path, "commit", "-qm", "fixture")
        self.head = git(path, "rev-parse", "HEAD")

    def snapshot(self, **changes):
        return source.owned_source(self.trees, self.item, self.branch, PACKAGES,
                                   **{"expected_head": self.head, **changes})

    def lfs(self, name="assets/Notice/icon.png", data=b"hydrated image", materialized=True):
        (self.path / ".gitattributes").write_text("*.png filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
        file = self.path / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(pointer(data))
        self.commit()
        if materialized:
            file.write_bytes(data)
        return file

    def test_owned_clean_unicode_and_empty_inputs_have_exact_byte_hashes(self):
        result = self.snapshot()
        self.assertEqual(result["head"], self.head)
        self.assertEqual(result["files"], {name: hashlib.sha256((self.path / name).read_bytes()).hexdigest()
                                         for name in ("UI.fairy", ".gitignore", "empty.txt", "assets/Notice/说明 file.xml")})

    def test_git_crlf_normalization_accepts_native_text_but_keeps_materialized_hash(self):
        file = self.path / "UI.fairy"
        file.write_bytes(b"project\r\n")
        with patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "true"}):
            # Source subprocesses deliberately ignore inherited -c selectors;
            # use a reviewed per-host global setting instead of clone config.
            self.global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
            result = self.snapshot()
        self.assertEqual(result["files"]["UI.fairy"], hashlib.sha256(file.read_bytes()).hexdigest())

    def test_scoped_hydrated_lfs_matches_raw_committed_pointer(self):
        file = self.lfs()
        self.assertEqual(self.snapshot()["files"][file.relative_to(self.path).as_posix()], hashlib.sha256(file.read_bytes()).hexdigest())

    def test_unselected_dependency_pointer_may_remain(self):
        file = self.lfs("assets/Other/icon.png", materialized=False)
        self.assertIn(file.relative_to(self.path).as_posix(), self.snapshot()["files"])

    def test_selected_pointer_is_pending_not_clean_hydrated_source(self):
        self.lfs(materialized=False)
        with self.assertRaisesRegex(LedgerError, "must be hydrated"):
            self.snapshot()

    def test_wrong_materialized_lfs_bytes_refuse_acceptance(self):
        self.lfs().write_bytes(b"wrong content")
        with self.assertRaisesRegex(LedgerError, "committed pointer"):
            self.snapshot()

    def test_lfs_attribute_on_nonpointer_blob_is_refused(self):
        self.lfs().write_bytes(b"not a pointer"); self.commit()
        with self.assertRaisesRegex(LedgerError, "pointer"):
            self.snapshot()

    def test_tracked_edit_index_change_and_untracked_files_are_refused(self):
        file = self.path / "UI.fairy"; original = file.read_bytes()
        file.write_bytes(b"edit")
        with self.assertRaisesRegex(LedgerError, "uncommitted"):
            self.snapshot()
        git(self.path, "add", "UI.fairy")
        with self.assertRaisesRegex(LedgerError, "index differs"):
            self.snapshot()
        git(self.path, "reset", "-q", "HEAD"); file.write_bytes(original)
        (self.path / "untracked.txt").write_bytes(b"edit")
        with self.assertRaisesRegex(LedgerError, "untracked"):
            self.snapshot()

    def test_ignored_publisher_cache_is_not_source(self):
        (self.path / ".objs").mkdir(); (self.path / ".objs/cache").write_bytes(b"ignored")
        self.assertNotIn(".objs/cache", self.snapshot()["files"])

    def test_changed_review_head_or_foreign_branch_refuses(self):
        with self.assertRaisesRegex(LedgerError, "reviewed commit"):
            self.snapshot(expected_head="0" * 40)
        git(self.path, "switch", "-qc", "foreign")
        with self.assertRaisesRegex(LedgerError, "issue branch"):
            self.snapshot()

    def test_controller_refuses_modified_clone_and_worktree_pointers(self):
        clone = self.trees.clone_path("farmgui")
        git(clone, "config", "core.sshCommand", "never-run")
        with self.assertRaises(WorktreeError):
            self.snapshot()
        git(clone, "config", "--unset", "core.sshCommand")
        # Simulate the exact stored pointer read without changing Git's native
        # hidden .git file attributes to make this fixture writable on Windows.
        read = agent.worktrees._read_worker_text
        with patch.object(agent.worktrees, "_read_worker_text",
                          side_effect=lambda p: "gitdir: foreign\n" if p == self.path / ".git" else read(p)), \
                self.assertRaises(WorktreeError):
            self.snapshot()

    def test_unknown_filter_refuses_before_any_program_execution(self):
        (self.path / ".gitattributes").write_text("*.fairy filter=unreviewed\n", encoding="utf-8"); self.commit()
        self.global_config.write_text('[filter "unreviewed"]\n\tclean = definitely-not-an-executable\n\trequired = true\n', encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "unreviewed Git clean filter"):
            self.snapshot()

    def test_inherited_lfs_program_is_disabled_for_all_git_reads(self):
        self.lfs()
        self.global_config.write_text('[filter "lfs"]\n\tclean = definitely-not-an-executable\n\tprocess = definitely-not-an-executable\n\trequired = true\n', encoding="utf-8")
        self.snapshot()

    def test_links_and_hardlinks_are_refused(self):
        file = self.path / "UI.fairy"; data = file.read_bytes(); file.unlink()
        file.symlink_to(self.origin / "UI.fairy")
        with self.assertRaises(LedgerError):
            self.snapshot()
        file.unlink(); file.write_bytes(data)
        os.link(file, self.root / "hardlink")
        with self.assertRaisesRegex(LedgerError, "unlinked"):
            self.snapshot()

    def test_bounded_file_count_file_bytes_and_aggregate_bytes(self):
        for symbol, cap in (("MAX_SOURCE_FILES", 2), ("MAX_SOURCE_FILE_BYTES", 2), ("MAX_SOURCE_BYTES", 3)):
            with self.subTest(symbol=symbol), patch.object(source, symbol, cap), self.assertRaises(LedgerError):
                self.snapshot()

    def test_snapshot_change_during_git_normalization_is_refused(self):
        real = source._git
        def changing(trees, path, args, *extra, **kw):
            result = real(trees, path, args, *extra, **kw)
            if args[0] == "hash-object":
                (path / "UI.fairy").write_bytes(b"changed")
            return result
        with patch.object(source, "_git", side_effect=changing), self.assertRaisesRegex(LedgerError, "changed during"):
            self.snapshot()

    def test_snapshot_change_during_regular_read_is_refused(self):
        real = source._read_regular
        def changing(path, cap):
            data = real(path, cap)
            if path.name == "UI.fairy":
                path.write_bytes(b"changed")
            return data
        with patch.object(source, "_read_regular", side_effect=changing), self.assertRaisesRegex(LedgerError, "changed during snapshot"):
            self.snapshot()

    def test_portable_tree_parser_refuses_aliases_links_submodules_and_bad_paths(self):
        oid = b"a" * 40
        rows = [b"120000 blob " + oid + b"\tlink\0", b"160000 commit " + oid + b"\tsubmodule\0",
                b"100644 blob " + oid + b"\tName\0" + b"100644 blob " + oid + b"\tname\0"]
        rows += [b"100644 blob " + oid + b"\t" + name.encode("utf-8") + b"\0"
                 for name in ("../escape", "C:/escape", "con.txt", "foo./bar", "quote\"", ".git/config", "line\nname")]
        for row in rows:
            with self.subTest(row=row), self.assertRaises(LedgerError):
                source._tree(row)

    def test_unmerged_index_and_duplicate_inventory_refuse(self):
        row = b"100644 " + b"a" * 40 + b" 0\tname\0"
        for data in (row + row, row.replace(b" 0\t", b" 1\t")):
            with self.subTest(data=data), self.assertRaises(LedgerError):
                source._index(data)

    def test_raw_lfs_object_frames_are_bounded_and_exact(self):
        entries = {"icon.png": ("100644", "a" * 40)}
        with patch.object(source, "_git", return_value=("a" * 40 + " blob 1025\n").encode("ascii")), self.assertRaisesRegex(LedgerError, "bounded pointer"):
            source._pointers(None, None, entries, ["icon.png"])
        data = pointer(b"x")
        header = ("a" * 40 + " blob " + str(len(data)) + "\n").encode("ascii")
        for batch in (header + data, header + data + b"\nx", header + data[:-1] + b"x\n"):
            with self.subTest(batch=batch), patch.object(source, "_git", side_effect=[header, batch]), self.assertRaises(LedgerError):
                source._pointers(None, None, entries, ["icon.png"])

    def package_diff(self, base):
        return source.changed_packages(self.trees, self.item, self.branch, head=self.head, base=base)

    def manifests(self):
        for name, identity in {"Notice": "notice01", "Common": "common01"}.items():
            path = self.path / "assets" / name; path.mkdir(exist_ok=True)
            (path / "package.xml").write_text('<packageDescription id="' + identity + '"/>', encoding="utf-8")
        self.commit(); return self.head

    def test_actual_source_diff_includes_changed_shared_package_and_other_source_evidence(self):
        base = self.manifests()
        for name in ("Notice", "Common"):
            (self.path / "assets" / name / "view.xml").write_bytes(b"changed source")
        (self.path / "UI.fairy").write_bytes(b"project metadata changed"); self.commit()
        result = self.package_diff(base)
        self.assertEqual(result["packages"], {"Common": "common01", "Notice": "notice01"})
        self.assertEqual(result["merge_base"], base)
        self.assertEqual(result["other_source_changes"], ["UI.fairy"])

    def test_unchanged_shared_package_is_not_added_to_changed_set(self):
        base = self.manifests(); (self.path / "assets/Notice/new.xml").write_bytes(b"new"); self.commit()
        self.assertEqual(self.package_diff(base)["packages"], {"Notice": "notice01"})

    def test_package_rename_uses_both_actual_paths_not_rename_similarity(self):
        base = self.manifests()
        (self.path / "assets/Notice/说明 file.xml").rename(self.path / "assets/Common/moved.xml"); self.commit()
        self.assertEqual(set(self.package_diff(base)["packages"]), {"Common", "Notice"})

    def test_removed_package_manifest_cannot_be_treated_as_installable_export(self):
        base = self.manifests(); (self.path / "assets/Common/package.xml").unlink(); self.commit()
        with self.assertRaisesRegex(LedgerError, "Git read failed"):
            self.package_diff(base)

    def test_unknown_baseline_and_nonpackage_only_diff_remain_pending(self):
        base = self.manifests(); (self.path / "UI.fairy").write_bytes(b"metadata"); self.commit()
        with self.assertRaisesRegex(LedgerError, "no changed package"):
            self.package_diff(base)
        with self.assertRaisesRegex(LedgerError, "Git read failed"):
            self.package_diff("0" * 40)


if __name__ == "__main__":
    unittest.main()
