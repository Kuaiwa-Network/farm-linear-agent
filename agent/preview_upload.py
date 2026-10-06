"""Bounded UI image snapshots and Linear's signed PUT; no credential/URL logging.

Pillow is imported only when an optional UI image is validated. Controller/fix
imports need no imaging dependency. The caller authenticates/rechecks the claim
and supplies only its own configured state directory, never an issue-supplied root.
"""
from dataclasses import dataclass
import hashlib
import http.client
import io
import ipaddress
import os
from pathlib import Path
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import warnings

from .ledger import LedgerError
from .linear_api import UploadError, upload_opener
from .uploads import _is_link, _read_regular, safe_name

MAX_BYTES = 20 << 20
MAX_SIDE = 8192
MAX_PIXELS = 16_000_000
UPLOAD_MUTATION = """mutation FarmBotPreviewUpload($type: String!, $name: String!, $size: Int!) {
    fileUpload(contentType: $type, filename: $name, size: $size) {
        success uploadFile { uploadUrl assetUrl headers { key value } }
    }
}"""


@dataclass(frozen=True)
class PreviewImage:
    name: str
    content_type: str
    width: int
    height: int
    data: bytes

    def evidence(self):
        return {"size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest(),
                "content_type": self.content_type, "pixels": {"width": self.width, "height": self.height}}


def _plain_path(path):
    # Checking only the final entry would miss a linked/junction ancestor.
    if not path.is_absolute() or ".." in path.parts or any(_is_link(p) for p in (path, *path.parents)):
        raise LedgerError("preview paths must be absolute and contain no links or parent traversal")


def read_image(raw, state_root):
    """One immutable, fully decoded PNG/JPEG snapshot inside this item's state root."""
    path, root = Path(raw), Path(state_root)
    _plain_path(root)
    _plain_path(path)
    if path == root or not path.resolve().is_relative_to(root.resolve()):
        raise LedgerError("preview image must be inside this item's state directory")
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise LedgerError("preview image must be an unlinked regular file")
        data = _read_regular(path, MAX_BYTES)
        after = path.stat()
        if after.st_nlink != 1:
            raise LedgerError("preview image acquired a hardlink during reading")
        if (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns):
            raise LedgerError("preview image changed during reading")
        _plain_path(path)
    except OSError:
        raise LedgerError("preview image cannot be read as a bounded regular file") from None
    if not data or len(data) > MAX_BYTES:
        raise LedgerError("preview image exceeds the byte limit or is empty")
    try:
        from PIL import Image
    except ImportError:
        raise LedgerError("UI image validation requires the prepared pinned Pillow dependency") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                kind, width, height = image.format, *image.size
                if kind not in ("PNG", "JPEG") or getattr(image, "n_frames", 1) != 1:
                    raise LedgerError("preview must be a single-frame PNG or JPEG")
                if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS):
                    raise LedgerError("preview image exceeds the dimension limit")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()  # Dimensions/signature alone cannot establish decodable pixels.
    except LedgerError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise LedgerError("preview image is malformed or cannot be decoded within limits") from None
    suffixes = {"PNG": (".png",), "JPEG": (".jpg", ".jpeg")}
    if path.suffix.lower() not in suffixes[kind] or safe_name(path.name) != path.name:
        raise LedgerError("preview filename must be portable and match its PNG/JPEG format")
    return PreviewImage(path.name, "image/png" if kind == "PNG" else "image/jpeg", width, height, data)


def _destination(value, *, asset=False):
    if not isinstance(value, str) or len(value) > 16_384 or any(ord(c) < 33 for c in value):
        raise UploadError("Linear returned an invalid upload destination")
    try:
        url = urllib.parse.urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https" or url.username is not None or url.password is not None
                or url.port not in (None, 443) or url.fragment or not url.path.startswith("/")
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)
                or not re.search(r"\.[A-Za-z][A-Za-z0-9-]{1,62}$", host)
                or host.lower().endswith((".localhost", ".local", ".internal"))):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        if asset and (url.netloc != "uploads.linear.app" or url.query
                      or any(p in (".", "..") for p in urllib.parse.unquote(url.path).split("/"))
                      or not re.fullmatch(r"(?:/[A-Za-z0-9._~%-]+)+", url.path)):
            raise ValueError()
    except ValueError:
        raise UploadError("Linear returned an invalid upload destination") from None
    return value


def _headers(raw, content_type):
    if not isinstance(raw, list) or len(raw) > 32:
        raise UploadError("Linear returned invalid upload headers")
    headers = {"Content-Type": content_type, "Cache-Control": "public, max-age=31536000"}
    seen = set()
    for field in raw:
        if not isinstance(field, dict) or set(field) != {"key", "value"}:
            raise UploadError("Linear returned invalid upload headers")
        key, value = field["key"], field["value"]
        folded = key.lower() if isinstance(key, str) else ""
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", key)
                or not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or folded in seen or folded in {"authorization", "proxy-authorization", "cookie", "host",
                                              "content-length", "transfer-encoding", "connection", "expect"}
                or folded == "content-type" and value != content_type):
            raise UploadError("Linear returned invalid upload headers")
        seen.add(folded)
        for previous in tuple(headers):
            if previous.lower() == folded:
                del headers[previous]
        headers[key] = value
    return headers


def upload_image(graphql, image, *, keepalive=lambda: None, opener=None, timeout=30):
    """Upload the validated snapshot; signed URLs stay local and no bearer goes to PUT.

    Only the authenticated GraphQL response chooses the destination. Callers
    cannot supply a URL/header through issue text or a CLI option. PUT follows
    no redirects. A claim loss propagates without a success result.
    """
    if not isinstance(image, PreviewImage):
        raise LedgerError("upload requires a validated preview image snapshot")
    keepalive()
    try:
        payload = graphql(UPLOAD_MUTATION, {"type": image.content_type, "name": image.name,
                                            "size": len(image.data)})
        result = payload.get("fileUpload") if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        exc.close()
        raise UploadError(f"Linear refused preview upload (HTTP {exc.code})") from None
    except (OSError, http.client.HTTPException, ValueError, KeyError):
        raise UploadError("Linear preview upload request failed") from None
    if not isinstance(result, dict) or result.get("success") is not True or not isinstance(result.get("uploadFile"), dict):
        raise UploadError("Linear did not confirm preview upload allocation")
    upload = result["uploadFile"]
    target = _destination(upload.get("uploadUrl"))
    asset = _destination(upload.get("assetUrl"), asset=True)
    headers = _headers(upload.get("headers"), image.content_type)
    keepalive()  # Stop during GraphQL allocation fences the actual PUT.
    try:
        request = urllib.request.Request(target, data=image.data, headers=headers, method="PUT")
        with (opener or upload_opener()).open(request, timeout=timeout) as response:
            if response.status not in (200, 201, 204):
                raise UploadError(f"preview PUT refused (HTTP {response.status})")
    except urllib.error.HTTPError as exc:
        exc.close()
        raise UploadError(f"preview PUT refused (HTTP {exc.code})") from None
    except UploadError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        raise UploadError("preview PUT failed") from None
    keepalive()
    return {"asset_url": asset, **image.evidence()}
