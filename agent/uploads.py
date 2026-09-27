"""Linear uploads for the worker CLI (spec §5.5, §9.7): which uploads the claimed issue carries, a safe local
name for each, safe zip extraction, PNG and JPEG pixel sizes, and the manifest of a download directory.

`download-uploads` runs this in the worker's own process; no controller step reads or writes the directory.
Titles, archive member names and archive contents come from uploaders: they are data, never trusted as paths.
Standard library only.
"""
from collections import namedtuple
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import unicodedata
import urllib.parse
from uuid import uuid4
import zipfile
import zlib

from .ledger import LedgerError
from .linear_api import UPLOADS_ORIGIN, TooLarge, UploadError, save_stream, upload_urls

MANIFEST = "manifest.json"
MANIFEST_FORMAT = 1
MANIFEST_LIMIT = 64 << 20        # the least a manifest may grow to before a read refuses it
MANIFEST_ENTRY_BYTES = 16 << 10  # more than one entry takes with the longest names and a legacy member's raw bytes
# Per run: `uploads` downloads, `file_bytes` for one upload and `download_bytes` in all; at most `zip_entries`
# entries in one archive; `extract_bytes` extracted in all, one member expanding to at most
# max(`bomb_floor`, `bomb_ratio` times its compressed size).
Limits = namedtuple("Limits", "uploads file_bytes download_bytes zip_entries extract_bytes bomb_ratio bomb_floor")
LIMITS = Limits(uploads=100, file_bytes=256 << 20, download_bytes=1 << 30, zip_entries=2000,
                extract_bytes=1 << 30, bomb_ratio=100, bomb_floor=1 << 20)
NAME_BYTES = 120          # a local name made from uploader text
MEMBER_PART_BYTES = 255   # one component of an archive member's path
MEMBER_PATH_CHARS = 180   # a member's whole path, so that DIR plus it stays inside Windows' MAX_PATH
MEMBER_DEPTH = 16
CENTRAL_BYTES_PER_ENTRY = 1024  # an archive's central directory may average this much per allowed entry
_FORBIDDEN = frozenset('<>:"/\\|?*')
_RESERVED = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                       *(f"{kind}{digit}" for kind in ("com", "lpt") for digit in "0123456789¹²³")})
_SUFFIX = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}")
_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
          ".zip": "application/zip", ".pdf": "application/pdf", ".txt": "text/plain", ".log": "text/plain",
          ".json": "application/json", ".csv": "text/csv", ".xml": "application/xml",
          ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
# The first suffix listed for a type names it, so image/jpeg gets .jpg.
_SUFFIXES = {**{kind: suffix for suffix, kind in reversed(_TYPES.items())}, "application/x-zip-compressed": ".zip"}
_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))
_JPEG_FRAMES = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
# Never follow a link or wait on a FIFO the directory may hold; Windows needs O_BINARY and has neither flag.
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
FIELDS = ("name", "status", "error", "sources", "on_issue", "url_path", "sha256", "size", "content_type", "pixels",
          "extract_dir", "source_zip", "zip_member", "zip_member_encoding", "zip_member_raw")
_ARCHIVE_ERRORS = (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError, RuntimeError, zlib.error)


class Hazard(ValueError):
    """A name that must not become a path; the message says why."""


def _key(name):
    """How a case-insensitive, normalization-insensitive file system compares two names."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def _unprintable(char):
    return unicodedata.category(char)[0] == "C"


def _split_suffix(name):
    """(stem, suffix): a suffix is a dot, a letter and up to seven letters or digits, such as ".png"."""
    stem, dot, suffix = name.rpartition(".")
    return (stem, "." + suffix) if dot and stem and _SUFFIX.fullmatch("." + suffix) else (name, "")


def _fit(name, limit):
    """`name` cut to `limit` UTF-8 bytes, keeping its suffix."""
    if len(name.encode("utf-8")) <= limit:
        return name
    stem, suffix = _split_suffix(name)
    stem = stem.encode("utf-8")[:limit - len(suffix)].decode("utf-8", "ignore").rstrip(" .")
    return (stem or "upload") + suffix


def safe_name(text):
    """A portable local file name from uploader text, or "" when nothing usable is left.

    Keeps the last path component in either separator style, replaces characters Windows forbids and
    unprintable ones with "_", drops leading and trailing dots and spaces, puts "_" before a Windows device
    name and fits NAME_BYTES of UTF-8.
    """
    parts = [part for part in re.split(r"[\\/]", unicodedata.normalize("NFC", text or "")) if part.strip(" .")]
    if not parts:
        return ""
    name = "".join("_" if char in _FORBIDDEN or _unprintable(char) else char for char in parts[-1])
    name = _fit(name.strip(" ."), NAME_BYTES)
    if name.split(".", 1)[0].rstrip(" ").casefold() in _RESERVED:
        name = "_" + name
    return name


def _unique(name, taken):
    """`name`, or `name` numbered before its suffix, unused in `taken` ignoring case; it is then taken."""
    stem, suffix = _split_suffix(name)
    candidate, number = name, 1
    while _key(candidate) in taken:
        number += 1
        candidate = f"{stem}-{number}{suffix}"
    taken.add(_key(candidate))
    return candidate


def _check_part(part):
    if part == "..":
        raise Hazard("parent directory reference")
    if ":" in part:
        raise Hazard("':' names a drive or an NTFS stream")
    if any(char in _FORBIDDEN or _unprintable(char) for char in part):
        raise Hazard("character Windows forbids")
    if part != part.rstrip(" ."):
        raise Hazard("trailing dot or space")
    if part.split(".", 1)[0].rstrip(" ").casefold() in _RESERVED:
        raise Hazard("Windows device name")
    if len(part.encode("utf-8")) > MEMBER_PART_BYTES:
        raise Hazard("name too long")


def member_parts(name):
    """The NFC components of an archive member's relative path, or Hazard naming what it carries (spec §5.5).

    Both separators count. Refused: absolute, drive and UNC paths; `..`; a `:` (a drive or an NTFS stream);
    characters Windows forbids and unprintable ones; a trailing dot or space; Windows device names (CON, NUL,
    AUX, COM1 ...); more than MEMBER_DEPTH components or MEMBER_PATH_CHARS characters.
    """
    if name.startswith(("/", "\\")):
        raise Hazard("absolute path")
    if re.match(r"[A-Za-z]:", name):
        raise Hazard("drive path")
    parts = [unicodedata.normalize("NFC", part) for part in re.split(r"[\\/]", name) if part not in ("", ".")]
    if not parts:
        raise Hazard("empty name")
    if len(parts) > MEMBER_DEPTH:
        raise Hazard("nested too deeply")
    for part in parts:
        _check_part(part)
    if len("/".join(parts)) > MEMBER_PATH_CHARS:
        raise Hazard("path too long")
    return parts


def _safe_relative(name):
    """Whether a manifest name is one this module could have written: NFC components, none of them hazards."""
    parts = name.split("/")
    try:
        for part in parts:
            if part in ("", ".") or unicodedata.normalize("NFC", part) != part:
                return False
            _check_part(part)
    except Hazard:
        return False
    return True


def _unicode_path_field(extra, raw):
    """The name in an Info-ZIP Unicode Path extra field (0x7075) written for these raw bytes, or None."""
    while len(extra) >= 4:
        kind, length = struct.unpack("<HH", extra[:4])
        data, extra = extra[4:4 + length], extra[4 + length:]
        if (kind == 0x7075 and len(data) > 5 and data[0] == 1
                and struct.unpack("<L", data[1:5])[0] == zlib.crc32(raw)):
            try:
                return data[5:].decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def member_name(info):
    """(name, encoding, raw hex) of an archive member; the name is None when it cannot be decoded.

    UTF-8 when the member sets the UTF-8 flag or carries an Info-ZIP Unicode Path field. Otherwise strict UTF-8
    first, because macOS Archive Utility writes UTF-8 names without the flag, then GBK, which Chinese Windows
    tools write and which strict UTF-8 almost never accepts. The raw bytes are kept whenever a legacy name was
    decoded. zipfile decoded such names as cp437, which maps every byte, so encoding back recovers them.
    """
    if info.flag_bits & 0x800:
        return info.orig_filename, "utf-8", None
    raw = info.orig_filename.encode("cp437")
    unicode_path = _unicode_path_field(info.extra, raw)
    if unicode_path is not None:
        return unicode_path, "utf-8", raw.hex()
    if raw.isascii():
        return info.orig_filename, "ascii", None
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding), encoding, raw.hex()
        except UnicodeDecodeError:
            continue
    return None, None, raw.hex()


def _member_kind(info):
    """"file" or "directory", or why the member is neither: a link, or another special file."""
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind == stat.S_IFLNK or info.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return "link"
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        return "special file"
    return "directory" if info.is_dir() or kind == stat.S_IFDIR else "file"


def _is_link(path):
    """A symlink, or on Windows a junction or another reparse point: an entry that redirects a path."""
    try:
        status = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return (stat.S_ISLNK(status.st_mode)
            or bool(getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def output_directory(raw, *, create=True):
    """The directory `--out` names, created if missing unless `create` is false: absolute, without `..`, and not a
    link."""
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise LedgerError("--out must be an absolute path without '..'")
    if _is_link(path):
        raise LedgerError("--out must not be a symlink, junction or other reparse point")
    if path.exists() and not path.is_dir():
        raise LedgerError("--out must name a directory")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


class _TagTitles(HTMLParser):
    """Collects (attribute text, title) for every <linear-image> and <linear-embed> tag."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        if tag in ("linear-image", "linear-embed"):
            values = dict(attrs)
            self.found.append((" ".join(value for value in values.values() if value), values.get("title")))


_MARKDOWN_LINK = re.compile(r"!?\[((?:\\.|[^\]\\\n]){1,300})\]\(\s*<?(https://uploads\.linear\.app/[^\s)>]+)")


def _titles(text):
    """{unsigned upload URL: title} from <linear-image> titles, then from Markdown link and image texts."""
    titles = {}
    parser = _TagTitles()
    parser.feed(text)
    parser.close()
    for attributes, title in parser.found:
        urls = upload_urls(attributes)
        if len(urls) == 1 and title and title.strip():
            titles.setdefault(urls[0], title.strip())
    for match in _MARKDOWN_LINK.finditer(text):
        label = re.sub(r"\\(.)", r"\1", match.group(1)).strip()
        urls = upload_urls(match.group(2))
        if len(urls) == 1 and label:
            titles.setdefault(urls[0], label)
    return titles


def issue_uploads(issue):
    """{unsigned URL: {"sources", "title"}} for the uploads in the issue's description and human comments.

    A source is "description" or "comment:<id>". The title is the first <linear-image> title or Markdown link
    text naming the upload, reading the description first and then comments in the order they were posted.
    """
    comments = sorted((comment for comment in issue.get("comments") or [] if comment.get("author_kind") == "human"),
                      key=lambda comment: (comment.get("created_at") or "", comment.get("id") or ""))
    texts = [("description", issue.get("description") or "")]
    texts += [(f"comment:{comment['id']}", comment.get("body") or "") for comment in comments]
    found = {}
    for source, text in texts:
        titles = _titles(text)
        for url in upload_urls(text):
            upload = found.setdefault(url, {"sources": [], "title": None})
            if source not in upload["sources"]:
                upload["sources"].append(source)
            if upload["title"] is None:
                upload["title"] = titles.get(url)
    return found


def _dimensions(width, height):
    return {"width": width, "height": height} if 0 < width < 1 << 31 and 0 < height < 1 << 31 else None


def _jpeg_size(stream):
    """Walk JPEG segments to the first frame header. A stream that is not well formed gives None."""
    for _ in range(4096):
        if stream.read(1) != b"\xff":
            return None
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker or marker[0] in (0xD9, 0xDA):  # end of image, or scan data before any frame header
            return None
        if marker[0] == 0x01 or 0xD0 <= marker[0] <= 0xD8:  # markers that carry no length
            continue
        length = struct.unpack(">H", stream.read(2))[0]
        if length < 2:
            return None
        if marker[0] in _JPEG_FRAMES:
            height, width = struct.unpack(">HH", stream.read(5)[1:5])
            return _dimensions(width, height)
        stream.seek(length - 2, os.SEEK_CUR)
    return None


def image_size(path):
    """{"width", "height"} of a PNG or JPEG file, read from its own headers, or None for anything else."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(24)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[8:16] == b"\x00\x00\x00\x0dIHDR":
                return _dimensions(*struct.unpack(">II", head[16:24]))
            if head[:2] == b"\xff\xd8":
                stream.seek(2)
                return _jpeg_size(stream)
    except (OSError, struct.error):
        return None
    return None


def _declared(path):
    """(entries, central directory bytes) an archive's end record declares, read before zipfile builds one
    object per central directory entry."""
    with open(path, "rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(size - min(size, 22 + 0xFFFF + 20))
        tail = stream.read()
        end = tail.rfind(b"PK\x05\x06")
        if end < 0 or len(tail) - end < 22:
            raise zipfile.BadZipFile("no end of central directory record")
        entries, central = struct.unpack_from("<HL", tail, end + 10)
        locator = end - 20
        if locator >= 0 and tail[locator:locator + 4] == b"PK\x06\x07":
            stream.seek(struct.unpack_from("<Q", tail, locator + 8)[0])
            record = stream.read(56)
            if len(record) == 56 and record[:4] == b"PK\x06\x06":
                entries = max(entries, struct.unpack_from("<Q", record, 32)[0])
                central = max(central, struct.unpack_from("<Q", record, 40)[0])
        return entries, central


def _read_regular(path, limit):
    """The bytes of a regular file of at most `limit` bytes, opened without following a link or waiting."""
    if _is_link(path):
        raise OSError(f"{Path(path).name} is a link")
    fd = os.open(path, _READ_FLAGS)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode) or status.st_size > limit:
            raise OSError(f"{Path(path).name} is not a regular file of at most {limit} bytes")
        with open(fd, "rb", closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(fd)


def _usable(entry):
    """A previous manifest entry this run may build on: every field present, every name a safe relative path, and
    an upload's own name, its archive's directory and its source archive one component each, the only form this
    module writes. A multi-part one could lead a later write through a link inside the directory."""
    return (isinstance(entry, dict) and set(FIELDS) <= entry.keys() and isinstance(entry["url_path"], str)
            and entry["status"] in ("ok", "failed", "refused") and isinstance(entry["sources"], list)
            and all(entry[key] is None or (isinstance(entry[key], str) and _safe_relative(entry[key]))
                    for key in ("name", "extract_dir", "source_zip"))
            and all("/" not in (entry[key] or "") for key in ("extract_dir", "source_zip"))
            and (entry["source_zip"] is not None or "/" not in (entry["name"] or ""))
            and (entry["status"] != "ok" or (isinstance(entry["sha256"], str) and type(entry["size"]) is int)))


def manifest_limit(limits):
    """The most bytes a manifest can hold within `limits`, so that one this module wrote is always read back: a
    run records at most `uploads` files and `zip_entries` members each, every entry under MANIFEST_ENTRY_BYTES."""
    return max(MANIFEST_LIMIT, limits.uploads * (limits.zip_entries + 1) * MANIFEST_ENTRY_BYTES)


def _read_manifest(directory, issue_id, limits):
    """The usable entries of the directory's manifest, or [] when there is none it can use.

    A directory holding another issue's uploads is refused. Entries naming unsafe paths are dropped, so an edited
    manifest can never steer a later write outside the directory.
    """
    try:
        data = json.loads(_read_regular(directory / MANIFEST, manifest_limit(limits)).decode("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT or not isinstance(data.get("files"), list):
        return []
    if data.get("issue_id") != issue_id:
        raise LedgerError("--out holds another issue's uploads; name a directory for this item")
    return [entry for entry in data["files"] if _usable(entry)]


def _local(directory, name):
    """directory/name for a manifest name, or None when an entry on the way is a link."""
    path = directory
    for part in name.split("/"):
        path = path / part
        if _is_link(path):
            return None
    return path


def _intact(directory, entry):
    """Whether an entry's file is still the regular file the manifest recorded, by size and sha256."""
    path = _local(directory, entry["name"]) if entry["status"] == "ok" and entry["name"] else None
    if path is None:
        return False
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError:
        return False
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode) or status.st_size != entry["size"]:
            return False
        with open(fd, "rb", closefd=False) as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"]
    except OSError:
        return False
    finally:
        os.close(fd)


def _directory(root, parts):
    """root/parts..., each created if missing; an entry on the way that is a link raises OSError."""
    path = root
    for part in parts:
        path = path / part
        if _is_link(path):
            raise OSError("a directory on the path is a link")
        path.mkdir(exist_ok=True)
    return path


def _entry(**fields):
    return {**dict.fromkeys(FIELDS), "status": "failed", "sources": [], "on_issue": True, **fields}


def _failed(entry, error, status="failed"):
    entry.update(status=status, error=error)
    return entry


def _gone(entry):
    """A kept entry whose local file no longer matches: failed until a run fetches it again."""
    entry.update(status="failed", error="local file missing or changed; run again to fetch it",
                 name=None, sha256=None, size=None, content_type=None, pixels=None)
    return entry


def _head(path):
    with open(path, "rb") as stream:
        return stream.read(16)


def _candidate(url_path, title, content_type, head):
    """The name an upload asks for: its title, else its URL's last segment; a suffix is added when it has none."""
    name = safe_name(title) or safe_name(urllib.parse.unquote(url_path.rsplit("/", 1)[-1])) or "upload"
    if not _split_suffix(name)[1]:
        name += next((suffix for signature, suffix in _SIGNATURES if head.startswith(signature)),
                     _SUFFIXES.get(content_type or "", ""))
    return name


class _Run:
    """One download-uploads run: its directory, what it has used of its limits, and the names already taken."""

    def __init__(self, api, directory, limits, renew, kept_names):
        self.api, self.directory, self.limits, self.renew = api, directory, limits, renew
        self.downloads = self.downloaded = 0
        self.extract_room = limits.extract_bytes
        self.claim_lost = False
        # Nothing already in the directory is overwritten, and a kept entry keeps its name even if its file went.
        self.taken = {_key(MANIFEST), *(_key(child.name) for child in directory.iterdir()),
                      *(_key(name) for name in kept_names)}

    def keep_claim(self):
        """Renew the claim, or note that it is lost; every download after a lost claim is skipped."""
        if self.renew is not None and not self.claim_lost:
            try:
                self.renew()
            except LedgerError:
                self.claim_lost = True
        return not self.claim_lost

    def keepalive(self):
        """The renewal a long transfer makes every KEEPALIVE_SECONDS; a lost claim ends the transfer."""
        if not self.keep_claim():
            raise UploadError("interrupted: the claim was lost")

    def fetch(self, url, title, old):
        """The top-level entry for one upload, downloaded now; `old` is its previous entry or None."""
        entry = _entry(url_path=url[len(UPLOADS_ORIGIN):],
                       extract_dir=old["extract_dir"] if old else None)
        if not self.keep_claim():
            return _failed(entry, "not downloaded: the claim was lost")
        room = min(self.limits.file_bytes, self.limits.download_bytes - self.downloaded)
        if self.downloads >= self.limits.uploads or room <= 0:
            return _failed(entry, "not downloaded: this run's download limit was reached")
        self.downloads += 1
        staging = self.directory / f".incoming-{uuid4().hex}"
        try:
            result = self.api.download_upload(url, staging, max_bytes=room, keepalive=self.keepalive)
        except TooLarge:
            return _failed(entry, f"larger than {room} bytes, the most one file may use in this run")
        except UploadError as exc:
            return _failed(entry, str(exc))
        try:
            self.downloaded += result["size"]
            name = old["name"] if old and old["name"] else None
            wanted = name or _candidate(entry["url_path"], title, result["content_type"], _head(staging))
            if result["content_type"] == "text/html" and _split_suffix(wanted)[1].lower() not in (".html", ".htm"):
                return _failed(entry, "Linear answered with a web page, not the file")
            name = name or _unique(wanted, self.taken)
            os.replace(staging, self.directory / name)
        except OSError as exc:
            return _failed(entry, f"could not store the file ({type(exc).__name__})")
        finally:
            staging.unlink(missing_ok=True)
        entry.update(status="ok", name=name, sha256=result["sha256"], size=result["size"],
                     content_type=result["content_type"], pixels=image_size(self.directory / name))
        return entry

    def extract(self, entry):
        """Members of the zip `entry` names, extracted under its own directory. A refusal of the whole archive is
        recorded in the entry's error."""
        entry["error"] = None
        if not self.keep_claim():
            entry["error"] = "not extracted: the claim was lost"
            return []
        try:
            entries, central = _declared(self.directory / entry["name"])
            if entries > self.limits.zip_entries:
                entry["error"] = f"not extracted: the archive holds more than {self.limits.zip_entries} entries"
                return []
            if central > self.limits.zip_entries * CENTRAL_BYTES_PER_ENTRY:
                entry["error"] = "not extracted: the archive's central directory is larger than its entry limit allows"
                return []
            with zipfile.ZipFile(self.directory / entry["name"]) as archive:
                infos = archive.infolist()
                if len(infos) > self.limits.zip_entries:
                    entry["error"] = f"not extracted: the archive holds more than {self.limits.zip_entries} entries"
                    return []
                if entry["extract_dir"] is None:
                    entry["extract_dir"] = _unique(safe_name(_split_suffix(entry["name"])[0]) or "archive", self.taken)
                try:
                    _directory(self.directory, [entry["extract_dir"]])
                except OSError:
                    entry["error"] = "not extracted: its directory could not be created"
                    return []
                seen = (set(), set())
                members = [self.member(archive, info, entry, seen) for info in infos]
        except _ARCHIVE_ERRORS as exc:
            entry["error"] = f"not extracted: not a readable zip archive ({type(exc).__name__})"
            return []
        return [member for member in members if member is not None]

    def member(self, archive, info, entry, seen):
        """One member's entry, or None for a directory; every hazard of spec §5.5 is refused, never written."""
        name, encoding, raw = member_name(info)
        record = _entry(url_path=entry["url_path"], source_zip=entry["name"], zip_member=name,
                        zip_member_encoding=encoding, zip_member_raw=raw)
        if name is None:
            return _failed(record, "name is neither UTF-8 nor GBK", "refused")
        kind = _member_kind(info)
        if kind not in ("file", "directory"):
            return _failed(record, kind, "refused")
        try:
            parts = member_parts(name)
        except Hazard as exc:
            return _failed(record, str(exc), "refused")
        files, folders = seen
        key = _key("/".join(parts))
        above = [_key("/".join(parts[:depth])) for depth in range(1, len(parts))]
        if key in files or any(folder in files for folder in above) or (kind == "file" and key in folders):
            return _failed(record, "collides with another member when case is ignored", "refused")
        folders.update(above)
        if kind == "directory":
            folders.add(key)
            return None
        files.add(key)
        if info.flag_bits & 0x1:
            return _failed(record, "encrypted", "refused")
        if self.extract_room <= 0:
            return _failed(record, "not extracted: this run's extraction limit was reached")
        bomb = max(self.limits.bomb_floor, self.limits.bomb_ratio * info.compress_size)
        relative = [entry["extract_dir"], *parts]
        try:
            parent = _directory(self.directory, relative[:-1])
            with archive.open(info) as stream:
                size, digest = save_stream(stream.read, parent / relative[-1], max_bytes=min(bomb, self.extract_room))
        except TooLarge:
            return _failed(record, "not extracted: it expands past the zip-bomb limit" if bomb <= self.extract_room
                           else "not extracted: this run's extraction limit was reached")
        except UploadError as exc:  # save_stream names a storage failure by its kind, never by a path
            return _failed(record, f"not extracted: {exc}")
        except _ARCHIVE_ERRORS as exc:
            return _failed(record, f"not extracted ({type(exc).__name__})")
        self.extract_room -= size
        record.update(status="ok", name="/".join(relative), sha256=digest, size=size,
                      content_type=_TYPES.get(_split_suffix(parts[-1])[1].lower()),
                      pixels=image_size(self.directory.joinpath(*relative)))
        return record


def download_issue_uploads(api, issue, directory, *, urls=None, limits=LIMITS, renew=None):
    """Download the issue's uploads, or the `urls` among them, into `directory` and rewrite its manifest.

    An upload the manifest already holds whose files still match is not fetched again. A failed download is
    recorded and the run goes on. `limits` apply to this run, and `renew` runs before each download. Returns
    the summary `download-uploads` prints.
    """
    directory = Path(directory)
    available = issue_uploads(issue)
    wanted = set(available if urls is None else urls)
    if wanted - available.keys():
        raise LedgerError("only uploads of the claimed issue can be downloaded")
    previous = _read_manifest(directory, issue["id"], limits)
    uploads = {entry["url_path"]: entry for entry in previous if entry["source_zip"] is None}
    members = {}
    for entry in previous:
        if entry["source_zip"] is not None and entry["url_path"] in uploads:
            members.setdefault(entry["url_path"], []).append(entry)
    current = {url[len(UPLOADS_ORIGIN):]: url for url in available}
    run = _Run(api, directory, limits, renew,
               [name for entry in uploads.values() for name in (entry["name"], entry["extract_dir"]) if name])
    files, report = [], []
    for url_path in sorted(current.keys() | uploads.keys()):
        url, old = UPLOADS_ORIGIN + url_path, uploads.get(url_path)
        found = available.get(url)
        extracted = [dict(member) for member in members.get(url_path, [])]
        if url in wanted:
            if old is not None and _intact(directory, old):
                entry, result = dict(old), "unchanged"
                if entry["extract_dir"] and not all(member["status"] == "refused" or _intact(directory, member)
                                                    for member in extracted):
                    extracted = run.extract(entry)
            else:
                entry = run.fetch(url, found["title"], old)
                result = "downloaded" if entry["status"] == "ok" else "failed"
                extracted = (run.extract(entry)
                             if entry["status"] == "ok" and _split_suffix(entry["name"])[1].lower() == ".zip" else [])
            report.append((entry, result, extracted))
        elif old is not None:
            entry = dict(old) if old["status"] != "ok" or _intact(directory, old) else _gone(dict(old))
            extracted = [member if member["status"] != "ok" or _intact(directory, member) else _gone(member)
                         for member in extracted]
        else:
            continue
        entry.update(sources=found["sources"] if found else old["sources"], on_issue=found is not None)
        for member in extracted:
            member.update(sources=entry["sources"], on_issue=entry["on_issue"])
        files += [entry, *extracted]
    manifest = directory / MANIFEST
    payload = (json.dumps({"format": MANIFEST_FORMAT, "issue_id": issue["id"], "identifier": issue["identifier"],
                           "files": files}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    save_stream(io.BytesIO(payload).read, manifest, max_bytes=len(payload))  # the module's own output, never refused
    summary = [{"url_path": entry["url_path"], "name": entry["name"], "result": result, "error": entry["error"],
                "size": entry["size"], "content_type": entry["content_type"], "pixels": entry["pixels"],
                "members": None if entry["status"] != "ok" or not entry["extract_dir"] else
                {status: sum(member["status"] == status for member in extracted)
                 for status in ("ok", "refused", "failed")}}
               for entry, result, extracted in report]
    return {"manifest": str(manifest), "identifier": issue["identifier"], "uploads": summary,
            "counts": {"downloaded": sum(item["result"] == "downloaded" for item in summary),
                       "unchanged": sum(item["result"] == "unchanged" for item in summary),
                       "failed": sum(item["result"] == "failed" for item in summary),
                       "bytes_downloaded": run.downloaded,
                       "files": sum(entry["status"] == "ok" for entry in files)}}
