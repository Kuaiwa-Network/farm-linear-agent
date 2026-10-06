"""Build an optional native callback into a NEW private tools directory; no auth setup."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


def lfs_origin(value):
    """Pin a server, without a repository path or embedded credential."""
    if any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) or any(c in value for c in "\\?#"):
        raise ValueError("LFS origin must contain only an HTTP scheme and server")
    parsed = urlsplit(value)
    host, port = parsed.hostname, parsed.port
    if (parsed.scheme not in ("http", "https") or not host or parsed.username is not None or
            parsed.password is not None or parsed.path not in ("", "/") or host.rstrip(".") == "github.com" or port == 0):
        raise ValueError("LFS origin must contain only a non-GitHub HTTP scheme and server")
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != {"http": 80, "https": 443}[parsed.scheme]:
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


def ordinary_path(path):
    for entry in (path, *path.parents):
        if entry.exists():
            info = entry.lstat()
            if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("tools path must not contain reparse points")


def selector(path):
    """A whitespace-free selector avoids Git routing askpass through its shell."""
    if not any(c.isspace() for c in str(path)):
        return str(path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    short = kernel.GetShortPathNameW
    short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    short.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    size = short(str(path), buffer, len(buffer))
    if not size or size >= len(buffer) or any(c.isspace() for c in buffer.value):
        raise RuntimeError("choose an owned tools directory with a whitespace-free native selector")
    if Path(buffer.value).read_bytes() != path.read_bytes():
        raise RuntimeError("native selector changed executable bytes")
    return buffer.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--lfs-origin", help="optional private HTTP(S) LFS server origin; no repository path")
    parser.add_argument("--gcm-executable", type=Path, help="existing native git-credential-manager.exe for that origin")
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("PYTHONUTF8") != "1":
        raise RuntimeError("requires native Windows and PYTHONUTF8=1 before Python starts")
    output = args.output_directory
    if not output.is_absolute() or output.exists() or not output.parent.is_dir():
        raise RuntimeError("output must be a new absolute directory with an existing parent")
    ordinary_path(output)
    source = Path(__file__).with_name("windows_git_askpass.cs")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    gh = shutil.which("gh.exe")
    if not compiler.is_file() or not gh:
        raise RuntimeError("requires the installed Windows C# compiler and native gh.exe")
    ordinary_path(Path(gh))
    origin = None
    gcm = args.gcm_executable
    if (args.lfs_origin is None) != (gcm is None):
        raise ValueError("LFS origin and native credential manager must be selected together")
    if gcm is not None:
        origin = lfs_origin(args.lfs_origin)
        if (not gcm.is_absolute() or not gcm.is_file() or gcm.name.lower() != "git-credential-manager.exe" or
                any(ord(c) < 32 for c in str(gcm))):
            raise ValueError("requires an explicit existing native git-credential-manager.exe")
        ordinary_path(gcm)
    output.mkdir()
    exe = output / "askpass.exe"
    sources = [str(source)]
    entry = []
    if origin is not None:
        sources.append(str(source.with_name("windows_lfs_askpass.cs")))
        entry = ["/main:FarmBotNativeGitAndLfsAskpass"]
    build = subprocess.run([str(compiler), "/nologo", "/target:exe", "/optimize+", *entry, "/out:" + str(exe), *sources],
                           capture_output=True, timeout=30)
    (output / "build-private.log").write_bytes(build.stdout + build.stderr)
    if build.returncode or not exe.is_file():
        raise RuntimeError("native callback build failed; private build evidence retained")
    selected = selector(exe)
    receipt = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "helper_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
               "gh_sha256": hashlib.sha256(Path(gh).read_bytes()).hexdigest(),
               "askpass_selector": selected, "gh_executable": gh}
    if origin is not None:
        digest = hashlib.sha256(gcm.read_bytes()).hexdigest()
        settings = exe.with_suffix(".lfs")
        settings.write_text(f"{origin}\n{gcm}\n{digest}\n", encoding="utf-8", newline="\n")
        receipt.update(lfs_source_sha256=hashlib.sha256(Path(sources[1]).read_bytes()).hexdigest(),
                       lfs_origin=origin, gcm_executable=str(gcm), gcm_sha256=digest,
                       lfs_settings_sha256=hashlib.sha256(settings.read_bytes()).hexdigest())
    (output / "receipt-private.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"native_callback_built": True, "credentials_configured": False,
                      "helper_sha256": receipt["helper_sha256"], "private_receipt_retained": True,
                      "lfs_origin_pinned": origin is not None}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"build_stopped": True, "error_type": type(error).__name__,
                          "credentials_configured": False}), file=sys.stderr)
        raise SystemExit(1)
