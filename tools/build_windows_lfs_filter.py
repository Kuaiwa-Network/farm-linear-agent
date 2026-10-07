"""Build an optional shell-free LFS filter into a NEW private native tools directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_windows_git_askpass import ordinary_path


def selector(path):
    """Git filter commands containing any of these characters invoke its helper shell."""
    value = str(path).replace("\\", "/")
    if (len(value) < 4 or value[1:3] != ":/" or not value[0].isascii() or not value[0].isalpha()
            or any(c.isspace() or ord(c) < 32 or c in "|&;<>()$`\"'*?[#~=%" for c in value)):
        raise ValueError("choose an absolute tools path without whitespace or Git shell characters")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--lfs-executable", type=Path, required=True, help="explicit existing native git-lfs.exe")
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("PYTHONUTF8") != "1":
        raise RuntimeError("requires native Windows and PYTHONUTF8=1 before Python starts")
    output, lfs = args.output_directory, args.lfs_executable
    if not output.is_absolute() or output.exists() or not output.parent.is_dir():
        raise ValueError("output must be a new absolute directory with an existing parent")
    ordinary_path(output)
    exe = output / "farmbot-lfs-filter.exe"
    selected = selector(exe)
    if not lfs.is_absolute() or not lfs.is_file() or lfs.name.lower() != "git-lfs.exe":
        raise ValueError("requires an explicit existing native git-lfs.exe")
    ordinary_path(lfs)
    lfs = lfs.resolve(strict=True)
    version = subprocess.check_output([str(lfs), "version"], stderr=subprocess.PIPE, timeout=10).decode("utf-8").strip()
    if not version.startswith("git-lfs/"):
        raise ValueError("selected executable did not identify Git LFS")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    ordinary_path(compiler)
    source = Path(__file__).with_name("windows_lfs_filter.cs")
    output.mkdir()
    build = subprocess.run([str(compiler), "/nologo", "/target:exe", "/optimize+", "/out:" + str(exe), str(source)],
                           capture_output=True, timeout=30)
    (output / "build-private.log").write_bytes(build.stdout + build.stderr)
    if build.returncode or not exe.is_file():
        raise RuntimeError("native filter build failed; private evidence retained")
    digest = hashlib.sha256(lfs.read_bytes()).hexdigest()
    settings = exe.with_suffix(".lfs-filter")
    settings.write_text(f"{lfs}\n{digest}\n", encoding="utf-8", newline="\n")
    receipt = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "helper_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "filter_selector": selected,
               "lfs_executable": str(lfs), "lfs_sha256": digest, "lfs_version": version,
               "settings_sha256": hashlib.sha256(settings.read_bytes()).hexdigest()}
    (output / "receipt-private.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"native_filter_built": True, "helper_sha256": receipt["helper_sha256"],
                      "credentials_configured": False, "git_configuration_modified": False}))
    return 0


if __name__ == "__main__":
    # Support the documented script-by-path entry as well as importing it in tests.
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"build_stopped": True, "error_type": type(error).__name__,
                          "credentials_configured": False, "git_configuration_modified": False}), file=sys.stderr)
        raise SystemExit(1)
