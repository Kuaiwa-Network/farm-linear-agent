"""Real decoding and scripted GraphQL/PUT; no app credential or external request."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from PIL import Image

from agent.ledger import LedgerError
from agent.linear_api import UploadError, upload_opener
from agent.preview_upload import read_image, upload_image
from test_linear_api import FakeUploadServer


class PreviewUploadTests(unittest.TestCase):
    ASSET = "https://uploads.linear.app/example/image"
    SIGNED = "https://storage.example/object?signature=private-signature"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="UI state ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "预览 1.png"
        self.write_image()
        self.allocated = {"fileUpload": {"success": True, "uploadFile": {
            "uploadUrl": self.SIGNED, "assetUrl": self.ASSET,
            "headers": [{"key": "x-storage-content", "value": "header-value"}]}}}
        self.graphql = Mock(return_value=self.allocated)

    def write_image(self, kind="PNG", size=(3, 2)):
        with Image.new("RGB", size, "red") as image:
            image.save(self.path, format=kind)

    def snapshot(self):
        return read_image(self.path, self.root)

    def test_png_and_jpeg_are_fully_decoded_and_uploaded_with_exact_snapshot_and_metadata(self):
        for kind, suffix, mime in (("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg")):
            with self.subTest(kind=kind):
                self.path = self.root / ("预览" + suffix)
                self.write_image(kind)
                image = self.snapshot()
                server = FakeUploadServer((201, {}, b""))
                keepalive = Mock()
                result = upload_image(self.graphql, image, opener=upload_opener(server), keepalive=keepalive)
                self.assertEqual(result, {"asset_url": self.ASSET, **image.evidence()})
                self.assertEqual(result["pixels"], {"width": 3, "height": 2})
                self.assertEqual(self.graphql.call_args.args[1],
                                 {"type": mime, "name": self.path.name, "size": len(image.data)})
                request = server.requests[0]
                self.assertEqual((request.get_method(), request.data), ("PUT", self.path.read_bytes()))
                self.assertNotIn("Authorization", request.headers)
                self.assertNotIn("Authorization", request.unredirected_hdrs)
                self.assertNotIn("Cookie", request.headers)
                self.assertNotIn("Cookie", request.unredirected_hdrs)
                self.assertEqual(request.get_header("Content-type"), mime)
                self.assertEqual(request.get_header("X-storage-content"), "header-value")
                self.assertEqual(keepalive.call_count, 3)
                self.assertNotIn("signature", json.dumps(result))

    def test_prepared_snapshot_is_immutable_even_if_the_original_file_changes(self):
        image = self.snapshot()
        self.path.write_bytes(b"changed after snapshot")
        server = FakeUploadServer((204, {}, b""))
        result = upload_image(self.graphql, image, opener=upload_opener(server))
        self.assertEqual(server.requests[0].data, image.data)
        self.assertEqual(result["sha256"], image.evidence()["sha256"])

    def test_truncated_image_bad_signature_mismatched_suffix_and_animated_png_are_refused(self):
        original = self.path.read_bytes()
        for data in (b"", b"not an image", original[:33], original[:-20]):
            with self.subTest(bytes=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(LedgerError):
                    self.snapshot()
        self.path.write_bytes(original)
        self.path = self.path.rename(self.root / "false.jpg")
        with self.assertRaisesRegex(LedgerError, "filename"):
            self.snapshot()
        self.path = self.path.rename(self.root / "动画.png")
        with Image.new("RGB", (3, 2), "red") as one, Image.new("RGB", (3, 2), "blue") as two:
            one.save(self.path, format="PNG", save_all=True, append_images=[two], duration=10)
        with self.assertRaisesRegex(LedgerError, "single-frame"):
            self.snapshot()
        self.graphql.assert_not_called()

    def test_caps_are_checked_before_pixel_decode_and_network(self):
        with patch("agent.preview_upload.MAX_BYTES", 4), self.assertRaises(LedgerError):
            self.snapshot()
        for limit in (patch("agent.preview_upload.MAX_SIDE", 2), patch("agent.preview_upload.MAX_PIXELS", 5)):
            with limit, self.assertRaisesRegex(LedgerError, "dimension"):
                self.snapshot()
        self.graphql.assert_not_called()

    def test_parent_traversal_outside_root_directory_and_linked_ancestors_are_refused(self):
        outside = self.root.parent / "outside.png"
        for path in (outside, self.root, self.root / ".." / self.root.name / self.path.name,
                     Path(self.path.name)):
            with self.subTest(path=path.name), self.assertRaises(LedgerError):
                read_image(path, self.root)
        target = self.root / "images"
        target.mkdir()
        copy = target / self.path.name
        copy.write_bytes(self.path.read_bytes())
        link = self.root / "linked"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(LedgerError, "links"):
            read_image(link / copy.name, self.root)
        file_link = self.root / "link.png"
        file_link.symlink_to(copy)
        with self.assertRaisesRegex(LedgerError, "links"):
            read_image(file_link, self.root)

    def test_hardlinks_are_refused_as_a_route_to_another_file(self):
        link = self.root / "copy.png"
        os.link(self.path, link)
        with self.assertRaisesRegex(LedgerError, "unlinked regular"):
            read_image(link, self.root)

    def test_mutation_during_read_is_refused(self):
        from agent.preview_upload import _read_regular
        def changed(path, limit):
            result = _read_regular(path, limit)
            path.write_bytes(b"changed")
            return result
        with patch("agent.preview_upload._read_regular", side_effect=changed), self.assertRaisesRegex(
                LedgerError, "changed during reading"):
            self.snapshot()

    def test_hardlink_created_during_read_refuses_the_snapshot(self):
        from agent.preview_upload import _read_regular
        def linked(path, limit):
            result = _read_regular(path, limit)
            os.link(path, self.root/"late-copy.png")
            return result
        with patch("agent.preview_upload._read_regular", side_effect=linked), self.assertRaisesRegex(
                LedgerError, "hardlink during reading"):
            self.snapshot()
        self.graphql.assert_not_called()

    def test_stop_at_each_transfer_boundary_prevents_further_work_or_a_success_result(self):
        image = self.snapshot()
        for boundary in (1, 2, 3):
            self.graphql.reset_mock()
            server = FakeUploadServer((200, {}, b""))
            calls = 0
            def fence():
                nonlocal calls
                calls += 1
                if calls == boundary:
                    raise LedgerError("claim withdrawn")
            with self.subTest(boundary=boundary), self.assertRaisesRegex(LedgerError, "withdrawn"):
                upload_image(self.graphql, image, opener=upload_opener(server), keepalive=fence)
            self.assertEqual(self.graphql.call_count, 0 if boundary == 1 else 1)
            self.assertEqual(len(server.requests), 1 if boundary == 3 else 0)

    def test_all_redirects_fail_without_following_or_returning_signed_data(self):
        image = self.snapshot()
        for code in (301, 302, 303, 307, 308):
            server = FakeUploadServer((code, {"Location": "https://collector.example/secret?token=private"}, b""))
            with self.subTest(code=code), self.assertRaises(UploadError) as caught:
                upload_image(self.graphql, image, opener=upload_opener(server))
            self.assertEqual(len(server.requests), 1)
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("signature", str(caught.exception))

    def test_bad_destinations_and_headers_are_refused_before_put(self):
        image = self.snapshot()
        base = self.allocated["fileUpload"]["uploadFile"]
        changes = [{"uploadUrl": url} for url in (
            "http://storage.example/x", "https://user@storage.example/x", "https://storage.example:8443/x",
            "https://127.0.0.1/x", "https://127.1/x", "https://host.local/x", "https://storage.example/x#secret")]
        changes += [{"assetUrl": url} for url in (
            self.ASSET+"?signature=secret", "https://collector.example/x", "https://uploads.linear.app/../secret")]
        changes += [{"headers": headers} for headers in (
            [{"key": "Authorization", "value": "Bearer secret"}],
            [{"key": "Cookie", "value": "private"}],
            [{"key": "Content-Length", "value": "3"}],
            [{"key": "Host", "value": "collector.example"}],
            [{"key": "x-extra", "value": "ok\r\nAuthorization: secret"}],
            [{"key": "Content-Type", "value": "application/octet-stream"}],
            [{"key": "x-extra", "value": "one"}, {"key": "X-EXTRA", "value": "two"}],
            [{"key": "x-extra", "value": "secret", "extra": True}], "wrong")]
        for change in changes:
            server = FakeUploadServer()
            self.graphql.return_value = {"fileUpload": {"success": True, "uploadFile": {**base, **change}}}
            with self.subTest(field=next(iter(change))), self.assertRaises(UploadError) as caught:
                upload_image(self.graphql, image, opener=upload_opener(server))
            self.assertEqual(server.requests, [])
            self.assertNotIn("secret", str(caught.exception))

    def test_failed_graphql_or_put_is_sanitized_and_never_reports_success(self):
        image = self.snapshot()
        for result in ([], {}, {"fileUpload": None}, {"fileUpload": {"success": False}}, {"fileUpload": {"success": True}}):
            self.graphql.return_value = result
            server = FakeUploadServer()
            with self.assertRaises(UploadError):
                upload_image(self.graphql, image, opener=upload_opener(server))
            self.assertEqual(server.requests, [])
        self.graphql.return_value = self.allocated
        for response in ((403, {}, b"secret"), urllib.error.URLError("signed-url-secret")):
            server = FakeUploadServer(response)
            with self.assertRaises(UploadError) as caught:
                upload_image(self.graphql, image, opener=upload_opener(server))
            self.assertNotIn("secret", str(caught.exception))
        self.graphql.side_effect = urllib.error.HTTPError(self.SIGNED, 401, "secret", {}, io.BytesIO(b"secret"))
        with self.assertRaisesRegex(UploadError, "HTTP 401") as caught:
            upload_image(self.graphql, image, opener=upload_opener(FakeUploadServer()))
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
