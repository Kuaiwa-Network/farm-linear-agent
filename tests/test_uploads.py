"""Linear upload downloads: names, archives, image sizes and manifests (spec §5.5), with a fake transport."""
import io
import json
import os
import stat
import struct
import subprocess
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from agent import uploads
from agent.ledger import LedgerError
from agent.linear_api import UploadError, save_stream

ISSUE_ID = "10000000-0000-4000-8000-000000000001"
ORG = "https://uploads.linear.app/7b0c6c4e-2f7a-4c55-9d0e-3a1f5e6d7c8b"
SHOT, LOG, ART, EXTRA = (f"{ORG}/{n}1111111-0000-4000-8000-00000000000{n}/{n}2222222-0000-4000-8000-00000000000{n}"
                         for n in range(1, 5))


def png(width, height):
    header = struct.pack(">I4sIIBBBBB", 13, b"IHDR", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + header + struct.pack(">I", zlib.crc32(header[4:]))
            + struct.pack(">I4sI", 0, b"IEND", zlib.crc32(b"IEND")))


def jpeg(width, height, frame=0xC0, exif=b""):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif if exif else b""
    sof = bytes([0xFF, frame]) + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + app1 + sof + b"\xff\xd9"


def archive(members, *, raw_names=None, links=(), extras=None):
    """A zip of {name: bytes}. `raw_names` swaps a placeholder ASCII name for raw bytes stored without the UTF-8
    flag, `links` are stored as symlinks, and `extras` gives a member's extra field."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name in links:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            info.extra = (extras or {}).get(name, b"")
            zipped.writestr(info, data)
    data = buffer.getvalue()
    for placeholder, raw in (raw_names or {}).items():
        assert len(placeholder.encode("ascii")) == len(raw) and data.count(placeholder.encode("ascii")) == 2
        data = data.replace(placeholder.encode("ascii"), raw)
    return data


def human(comment_id, body, created="2026-09-18T08:00:00Z"):
    return {"id": comment_id, "body": body, "author_kind": "human", "created_at": created, "updated_at": created}


def an_issue(description="", comments=(), issue_id=ISSUE_ID):
    return {"id": issue_id, "identifier": "FARM-1", "description": description, "comments": list(comments)}


class FakeUploads:
    """Stands in for LinearAPI.download_upload: serves bytes by URL and records every download."""

    def __init__(self, files, types=None, errors=None):
        self.files, self.types, self.errors, self.calls = dict(files), dict(types or {}), dict(errors or {}), []

    def download_upload(self, url, destination, *, max_bytes):
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        size, digest = save_stream(io.BytesIO(self.files[url]).read, destination, max_bytes=max_bytes)
        return {"size": size, "sha256": digest, "content_type": self.types.get(url, "application/octet-stream")}


class NameTests(unittest.TestCase):
    def test_safe_names_keep_readable_text_and_drop_every_hazard(self):
        cases = {"效果图 1.png": "效果图 1.png", "../../etc/passwd": "passwd", "D:\\art\\shot.png": "shot.png",
                 "con.png": "_con.png", "NUL": "_NUL", "LPT¹.txt": "_LPT¹.txt", "report. ": "report",
                 "a:b.png": "a_b.png", 'bad<>|?*".png': "bad______.png", "\u202egnp.exe": "_gnp.exe",
                 "tab\tname.png": "tab_name.png", "  .hidden.png": "hidden.png", "...": "", "": "",
                 "长" * 100 + ".png": "长" * 38 + ".png"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(uploads.safe_name(text), expected)

    def test_names_are_unique_ignoring_case_and_numbered_before_the_suffix(self):
        taken = set()
        self.assertEqual([uploads._unique(name, taken) for name in ("a.png", "A.PNG", "a.png", "切图", "切图")],
                         ["a.png", "A-2.PNG", "a-3.png", "切图", "切图-2"])

    def test_member_paths_refuse_every_spec_hazard_in_both_separator_styles(self):
        cases = {"/etc/passwd": "absolute", "\\Windows\\win.ini": "absolute", "\\\\server\\share\\x.png": "absolute",
                 "C:\\x.png": "drive", "c:x.png": "drive", "a/../../b.png": "parent", "a\\..\\b.png": "parent",
                 "..": "parent", "CON": "device", "nul.txt": "device", "Res/COM1.png": "device",
                 "aux .png": "device", "lpt9": "device", "COM¹": "device", "slice.png.": "trailing",
                 "slice.png ": "trailing", "dir./a.png": "trailing", "a.png:hidden": "stream",
                 "a.png::$DATA": "stream", "bad|name.png": "forbids", "tab\tname.png": "forbids",
                 "/".join(["d"] * 17): "deeply", "x" * 256: "too long", "d/" + "x" * 190: "path too long",
                 "": "empty", "./": "empty"}
        for name, reason in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(uploads.Hazard, reason):
                uploads.member_parts(name)
        self.assertEqual(uploads.member_parts("切图\\按钮 关闭.png"), ["切图", "按钮 关闭.png"])
        self.assertEqual(uploads.member_parts("./Res//a.png"), ["Res", "a.png"])


class ImageSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def size(self, data):
        path = Path(self.tmp.name) / "image"
        path.write_bytes(data)
        return uploads.image_size(path)

    def test_png_and_jpeg_sizes_come_from_their_own_headers(self):
        self.assertEqual(self.size(png(1080, 1920)), {"width": 1080, "height": 1920})
        self.assertEqual(self.size(jpeg(640, 480)), {"width": 640, "height": 480})
        self.assertEqual(self.size(jpeg(32, 16, frame=0xC2, exif=b"Exif\x00\x00" + bytes(300))),
                         {"width": 32, "height": 16})

    def test_anything_else_has_no_size(self):
        for data in (b"GIF89a\x01\x00\x01\x00", png(1, 1)[:20], jpeg(0, 0), b"\xff\xd8\xff\xd9", b"", b"not an image",
                     b"\x89PNG\r\n\x1a\n" + struct.pack(">I4s", 13, b"IDAT") + bytes(8)):
            with self.subTest(data=data[:12]):
                self.assertIsNone(self.size(data))


class UploadDirectory(unittest.TestCase):
    """An issue with a screenshot and a log in its description and an art zip in a human comment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "状态 目录" / "inputs linear"
        self.out.mkdir(parents=True)
        self.issue = an_issue(f"见截图 ![截图 1.png]({SHOT})，日志 [日志]({LOG})",
                              [human("c2", f'<linear-image src="{ART}" title="切图.zip"></linear-image>',
                                     created="2026-09-18T09:00:00Z"),
                               {**human("c3", f"![bot]({EXTRA})"), "author_kind": "bot"},
                               human("c1", f"again ![dup.png]({SHOT})", created="2026-09-18T07:00:00Z")])
        self.art = archive({"切图/按钮 关闭.png": png(40, 20), "Res/bg.jpg": jpeg(8, 4)})
        self.api = FakeUploads({SHOT: png(1080, 1920), LOG: "登录失败".encode("utf-8"), ART: self.art},
                               types={SHOT: "image/png", LOG: "text/plain", ART: "application/zip"})

    def run_once(self, api=None, issue=None, **options):
        return uploads.download_issue_uploads(api or self.api, issue or self.issue, self.out, **options)

    def manifest(self):
        return json.loads((self.out / uploads.MANIFEST).read_text(encoding="utf-8"))

    def by_name(self):
        return {entry["name"]: entry for entry in self.manifest()["files"] if entry["name"]}


class DownloadTests(UploadDirectory):
    def test_uploads_come_from_the_description_and_human_comments_only(self):
        found = uploads.issue_uploads(self.issue)
        self.assertEqual(sorted(found), [SHOT, LOG, ART])
        self.assertEqual(found[SHOT], {"sources": ["description", "comment:c1"], "title": "截图 1.png"})
        self.assertEqual(found[LOG], {"sources": ["description"], "title": "日志"})
        self.assertEqual(found[ART], {"sources": ["comment:c2"], "title": "切图.zip"})

    def test_every_upload_is_stored_under_a_safe_name_with_its_facts_in_the_manifest(self):
        summary = self.run_once()
        self.assertEqual(summary["counts"], {"downloaded": 3, "unchanged": 0, "failed": 0,
                                             "bytes_downloaded": sum(map(len, self.api.files.values())), "files": 5})
        files = self.by_name()
        self.assertEqual(sorted(files), ["切图.zip", "切图/Res/bg.jpg", "切图/切图/按钮 关闭.png", "截图 1.png", "日志.txt"])
        shot = files["截图 1.png"]
        self.assertEqual((shot["status"], shot["size"], shot["content_type"], shot["pixels"], shot["sources"]),
                         ("ok", len(png(1, 1)), "image/png", {"width": 1080, "height": 1920},
                          ["description", "comment:c1"]))
        self.assertEqual(shot["url_path"], SHOT[len("https://uploads.linear.app"):])
        self.assertEqual((self.out / "截图 1.png").read_bytes(), png(1080, 1920))
        self.assertEqual(files["日志.txt"]["content_type"], "text/plain")
        button = files["切图/切图/按钮 关闭.png"]
        self.assertEqual((button["source_zip"], button["zip_member"], button["pixels"], button["sources"]),
                         ("切图.zip", "切图/按钮 关闭.png", {"width": 40, "height": 20}, ["comment:c2"]))
        self.assertEqual(files["切图/Res/bg.jpg"]["pixels"], {"width": 8, "height": 4})
        self.assertEqual(summary["uploads"][2]["members"], {"ok": 2, "refused": 0, "failed": 0})
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(["manifest.json", "切图", "切图.zip",
                                                                          "截图 1.png", "日志.txt"]))

    def test_a_second_run_fetches_nothing_and_writes_the_same_manifest(self):
        self.run_once()
        first = (self.out / uploads.MANIFEST).read_bytes()
        again = self.run_once()
        self.assertEqual(len(self.api.calls), 3)
        self.assertEqual([upload["result"] for upload in again["uploads"]], ["unchanged"] * 3)
        self.assertEqual((self.out / uploads.MANIFEST).read_bytes(), first)

    def test_a_changed_file_or_member_is_fetched_or_extracted_again_under_its_own_name(self):
        self.run_once()
        (self.out / "截图 1.png").write_bytes(b"edited")
        (self.out / "切图" / "Res" / "bg.jpg").unlink()
        again = self.run_once()
        self.assertEqual([upload["result"] for upload in again["uploads"]], ["downloaded", "unchanged", "unchanged"])
        self.assertEqual(self.api.calls.count(SHOT), 2)
        self.assertEqual((self.out / "截图 1.png").read_bytes(), png(1080, 1920))
        self.assertEqual((self.out / "切图" / "Res" / "bg.jpg").read_bytes(), jpeg(8, 4))
        self.assertNotIn("截图 1-2.png", self.by_name())

    def test_a_new_upload_is_fetched_alone_and_existing_names_stay(self):
        self.run_once()
        issue = {**self.issue, "comments": [*self.issue["comments"], human("c9", f"![截图 1.png]({EXTRA})")]}
        self.api.files[EXTRA] = png(2, 2)
        summary = self.run_once(issue=issue)
        self.assertEqual(self.api.calls[3:], [EXTRA])
        results = {upload["name"]: upload["result"] for upload in summary["uploads"]}
        self.assertEqual(results["截图 1-2.png"], "downloaded")
        self.assertEqual(self.by_name()["截图 1.png"]["url_path"], SHOT[len("https://uploads.linear.app"):])

    def test_a_failed_download_is_recorded_the_run_goes_on_and_a_later_run_retries_it(self):
        self.api.errors[LOG] = UploadError("HTTP 503")
        summary = self.run_once()
        self.assertEqual([upload["result"] for upload in summary["uploads"]], ["downloaded", "failed", "downloaded"])
        failed = [entry for entry in self.manifest()["files"] if entry["status"] == "failed"]
        self.assertEqual([(entry["name"], entry["error"]) for entry in failed], [(None, "HTTP 503")])
        del self.api.errors[LOG]
        again = self.run_once()
        self.assertEqual([upload["result"] for upload in again["uploads"]], ["unchanged", "downloaded", "unchanged"])
        self.assertEqual(self.api.calls.count(LOG), 2)

    def test_a_web_page_instead_of_the_file_is_a_failed_download(self):
        self.api.types[SHOT] = "text/html"
        summary = self.run_once(urls=[SHOT])
        self.assertEqual((summary["uploads"][0]["result"], summary["uploads"][0]["error"]),
                         ("failed", "Linear answered with a web page, not the file"))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["manifest.json"])

    def test_only_the_named_urls_are_fetched_and_the_rest_of_the_manifest_is_kept(self):
        self.run_once()
        (self.out / "日志.txt").unlink()
        summary = self.run_once(urls=[SHOT])
        self.assertEqual([(upload["name"], upload["result"]) for upload in summary["uploads"]],
                         [("截图 1.png", "unchanged")])
        log_path = LOG[len("https://uploads.linear.app"):]
        gone = [entry for entry in self.manifest()["files"] if entry["url_path"] == log_path]
        self.assertEqual([(entry["status"], entry["name"]) for entry in gone], [("failed", None)])
        self.assertIn("run again", gone[0]["error"])
        with self.assertRaisesRegex(LedgerError, "only uploads of the claimed issue"):
            self.run_once(urls=[f"{ORG}/other/upload"])

    def test_an_upload_removed_from_the_issue_stays_listed_as_no_longer_on_it(self):
        self.run_once()
        issue = {**self.issue, "description": f"![截图 1.png]({SHOT})"}
        self.run_once(issue=issue)
        entry = next(entry for entry in self.manifest()["files"] if entry["name"] == "日志.txt")
        self.assertEqual((entry["status"], entry["on_issue"]), ("ok", False))

    def test_run_limits_fail_the_uploads_beyond_them(self):
        capped = self.run_once(limits=uploads.LIMITS._replace(file_bytes=len(png(1, 1)) - 1), urls=[SHOT])
        self.assertIn("larger than", capped["uploads"][0]["error"])
        summary = self.run_once(limits=uploads.LIMITS._replace(uploads=1))
        self.assertEqual([upload["result"] for upload in summary["uploads"]], ["downloaded", "failed", "failed"])
        self.assertIn("download limit", summary["uploads"][1]["error"])

    def test_a_claim_lost_mid_run_stops_the_downloads_and_still_writes_the_manifest(self):
        renewals = []

        def renew():
            renewals.append(True)
            if len(renewals) > 1:
                raise LedgerError("running claim and matching token required")
        summary = self.run_once(renew=renew)
        self.assertEqual([upload["result"] for upload in summary["uploads"]], ["downloaded", "failed", "failed"])
        self.assertIn("claim was lost", summary["uploads"][1]["error"])
        self.assertEqual((self.api.calls, len(renewals)), ([SHOT], 2))
        self.assertEqual(len(self.manifest()["files"]), 3)

    def test_a_manifest_for_another_issue_is_refused_and_worker_files_are_never_overwritten(self):
        (self.out / "截图 1.png").write_bytes(b"the worker's own notes")
        self.run_once(urls=[SHOT])
        self.assertEqual((self.out / "截图 1.png").read_bytes(), b"the worker's own notes")
        self.assertEqual(self.by_name()["截图 1-2.png"]["pixels"], {"width": 1080, "height": 1920})
        with self.assertRaisesRegex(LedgerError, "another issue"):
            self.run_once(issue=an_issue(f"![x.png]({SHOT})", issue_id="10000000-0000-4000-8000-000000000002"))

    def test_an_edited_manifest_cannot_point_outside_the_directory(self):
        self.run_once(urls=[SHOT])
        document = self.manifest()
        document["files"][0]["name"] = "../escape.png"
        (self.out / uploads.MANIFEST).write_text(json.dumps(document), encoding="utf-8")
        self.run_once(urls=[SHOT])
        self.assertFalse((self.out.parent / "escape.png").exists())
        # The dropped entry no longer vouches for 截图 1.png, so that file is left alone and the upload gets a new name.
        self.assertEqual(self.by_name()["截图 1-2.png"]["status"], "ok")


class ArchiveTests(UploadDirectory):
    def extract(self, data, **options):
        self.api.files[ART] = data
        summary = self.run_once(urls=[ART], **options)
        return summary, [entry for entry in self.manifest()["files"] if entry["source_zip"]]

    def test_every_member_hazard_is_refused_and_listed_and_nothing_leaves_the_directory(self):
        # zipfile turns a backslash into "/" when it writes on Windows, so backslashed names go in as raw bytes.
        _, members = self.extract(archive(
            {"ok/按钮.png": png(1, 1), "../escape.png": b"x", "B" * 14: b"x", "/abs.png": b"x", "D" * 11: b"x",
             "CON.png": b"x", "a.png:stream": b"x", "trail.png.": b"x", "Res/A.png": b"1", "res/a.png": b"2",
             "x": b"file", "x/y.png": b"under a file", "link.png": b"/etc/passwd"},
            raw_names={"B" * 14: b"..\\escape2.png", "D" * 11: b"C:\\evil.png"}, links=("link.png",)))
        outcome = {entry["zip_member"]: (entry["status"], entry["error"]) for entry in members}
        self.assertEqual(outcome["ok/按钮.png"], ("ok", None))
        self.assertEqual(outcome["Res/A.png"], ("ok", None))
        for member, reason in (("../escape.png", "parent"), ("..\\escape2.png", "parent"), ("/abs.png", "absolute"),
                               ("C:\\evil.png", "drive"), ("CON.png", "device"), ("a.png:stream", "stream"),
                               ("trail.png.", "trailing"), ("res/a.png", "collides"), ("x/y.png", "collides"),
                               ("link.png", "link")):
            with self.subTest(member=member):
                self.assertEqual(outcome[member][0], "refused")
                self.assertIn(reason, outcome[member][1])
        written = sorted(path.relative_to(self.out).as_posix() for path in self.out.rglob("*") if path.is_file())
        self.assertEqual(written, ["manifest.json", "切图.zip", "切图/Res/A.png", "切图/ok/按钮.png", "切图/x"])
        self.assertFalse(any((Path(self.tmp.name) / name).exists() for name in ("escape.png", "escape2.png")))

    @staticmethod
    def names(members):
        return [(entry["zip_member"], entry["zip_member_encoding"], entry["zip_member_raw"], entry["name"])
                for entry in members]

    def test_a_flagged_utf8_name_is_read_as_utf8(self):
        _, members = self.extract(archive({"切图/按钮.png": png(1, 1)}))
        self.assertEqual(self.names(members), [("切图/按钮.png", "utf-8", None, "切图/切图/按钮.png")])

    def test_a_mac_zip_name_is_read_as_utf8_without_the_flag(self):
        # macOS Archive Utility writes UTF-8 names without the flag. These bytes are valid GBK too, and read as
        # GBK they would become 鍒囧浘/鎸夐挳.png, so strict UTF-8 must be tried first.
        raw = "切图/按钮.png".encode("utf-8")
        self.assertEqual(raw.decode("gbk"), "鍒囧浘/鎸夐挳.png")
        _, members = self.extract(archive({"M" * 13 + ".png": png(1, 1)}, raw_names={"M" * 13 + ".png": raw}))
        self.assertEqual(self.names(members), [("切图/按钮.png", "utf-8", raw.hex(), "切图/切图/按钮.png")])

    def test_a_windows_zip_name_is_read_as_gbk_without_the_flag(self):
        raw = "切图/按钮.png".encode("gbk")
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("utf-8")
        _, members = self.extract(archive({"W" * 9 + ".png": png(1, 1)}, raw_names={"W" * 9 + ".png": raw}))
        self.assertEqual(self.names(members), [("切图/按钮.png", "gbk", raw.hex(), "切图/切图/按钮.png")])

    def test_a_unicode_path_field_wins_and_an_undecodable_name_is_refused_with_its_raw_bytes(self):
        legacy, named, undecodable = "图.png".encode("gbk"), "蓝图.png".encode("utf-8"), b"bad-\xff.png"
        extra = struct.pack("<HHBL", 0x7075, 5 + len(named), 1, zlib.crc32(legacy)) + named
        _, members = self.extract(archive({"RR.png": b"x", "UUUUU.png": b"y"},
                                          raw_names={"RR.png": legacy, "UUUUU.png": undecodable},
                                          extras={"RR.png": extra}))
        self.assertEqual(self.names(members), [("蓝图.png", "utf-8", legacy.hex(), "切图/蓝图.png"),
                                               (None, None, undecodable.hex(), None)])
        self.assertEqual((members[1]["status"], members[1]["error"]), ("refused", "name is neither UTF-8 nor GBK"))

    def test_a_zip_bomb_is_stopped_while_it_expands(self):
        summary, members = self.extract(archive({"zeros.bin": bytes(8 << 20), "small.txt": b"fine"}),
                                        limits=uploads.LIMITS._replace(bomb_floor=64 << 10))
        self.assertEqual([(entry["zip_member"], entry["status"]) for entry in members],
                         [("zeros.bin", "failed"), ("small.txt", "ok")])
        self.assertIn("zip-bomb", members[0]["error"])
        self.assertEqual(summary["uploads"][0]["members"], {"ok": 1, "refused": 0, "failed": 1})
        self.assertEqual(sorted(path.name for path in (self.out / "切图").iterdir()), ["small.txt"])

    def test_archive_count_and_run_extraction_caps(self):
        many = archive({f"{number}.txt": b"x" for number in range(4)})
        summary, members = self.extract(many, limits=uploads.LIMITS._replace(zip_entries=3))
        self.assertEqual(members, [])
        self.assertIn("more than 3 entries", summary["uploads"][0]["error"])
        self.assertFalse((self.out / "切图").exists())
        (self.out / "切图.zip").unlink()
        _, members = self.extract(archive({"a.txt": b"12345678", "b.txt": b"12345678"}),
                                  limits=uploads.LIMITS._replace(extract_bytes=10))
        self.assertEqual([(entry["zip_member"], entry["status"]) for entry in members],
                         [("a.txt", "ok"), ("b.txt", "failed")])
        self.assertIn("extraction limit", members[1]["error"])

    def test_a_nested_archive_stays_a_file_and_a_broken_one_is_recorded(self):
        _, members = self.extract(archive({"inner.zip": archive({"deep.png": png(1, 1)})}))
        self.assertEqual([(entry["name"], entry["content_type"]) for entry in members],
                         [("切图/inner.zip", "application/zip")])
        (self.out / "切图.zip").unlink()
        summary, members = self.extract(b"PK\x03\x04 not really a zip")
        self.assertEqual(members, [])
        self.assertIn("not a readable zip archive", summary["uploads"][0]["error"])


class OutputDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_out_must_be_absolute_and_is_created_with_spaces_and_chinese(self):
        for raw in ("inputs/linear", str(self.root / "a" / ".." / "b")):
            with self.subTest(raw=raw), self.assertRaisesRegex(LedgerError, "absolute path"):
                uploads.output_directory(raw)
        made = uploads.output_directory(str(self.root / "状态 目录" / "inputs linear"))
        self.assertTrue(made.is_dir())
        (self.root / "file").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "directory"):
            uploads.output_directory(str(self.root / "file"))

    def test_a_symlinked_out_is_refused(self):
        target, link = self.root / "target", self.root / "link"
        target.mkdir()
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            self.skipTest("creating symlinks needs a privilege on this host")
        with self.assertRaisesRegex(LedgerError, "symlink"):
            uploads.output_directory(str(link))

    @unittest.skipUnless(os.name == "nt", "junctions exist on Windows only")
    def test_a_junction_is_refused_as_out(self):
        target, link = self.root / "target", self.root / "junction"
        target.mkdir()
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
        with self.assertRaisesRegex(LedgerError, "junction"):
            uploads.output_directory(str(link))


@unittest.skipUnless(os.name == "nt", "Windows file-name semantics are checked on Windows")
class WindowsNameTests(UploadDirectory):
    """On NTFS these names would alias another file, open a device or write an alternate stream."""

    def written(self, members):
        self.api.files[ART] = archive({**members, "ok.png": png(1, 1)})
        self.run_once(urls=[ART])
        return sorted(path.relative_to(self.out).as_posix() for path in self.out.rglob("*"))

    def test_reserved_device_names_are_never_opened(self):
        self.assertEqual(self.written({"NUL.png": b"n", "con": b"c", "Res/COM1.txt": b"m", "aux .png": b"a"}),
                         ["manifest.json", "切图", "切图.zip", "切图/ok.png"])

    def test_trailing_dots_and_spaces_do_not_alias(self):
        self.assertEqual(self.written({"ok.png.": b"d", "ok.png ": b"s", "dir./x.png": b"x"}),
                         ["manifest.json", "切图", "切图.zip", "切图/ok.png"])
        self.assertEqual((self.out / "切图" / "ok.png").read_bytes(), png(1, 1))

    def test_colon_names_create_no_stream(self):
        self.assertEqual(self.written({"a.png:ads": b"s", "b.png::$DATA": b"d"}),
                         ["manifest.json", "切图", "切图.zip", "切图/ok.png"])
