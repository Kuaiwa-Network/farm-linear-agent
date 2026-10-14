"""Prepare a separate npm Codex candidate; never activate it or load FarmBot state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z")
_TARGETS = {(system, arch) for system in ("win32", "darwin", "linux") for arch in ("x64", "arm64")}
RECEIPT = "codex-runtime.json"


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _directory(path):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError("candidate directory must be absolute")
    for ancestor in (path, *path.parents):
        if _linked(ancestor):
            raise ValueError("candidate directory must not traverse a link or junction")
    return path.resolve()


def _regular(path):
    info = path.lstat()
    if _linked(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("candidate package files must be regular files without links")


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _inventory(root):
    """Hash the lock, package metadata and complete native bundle, including helpers."""
    files = [root / "package.json", root / "package-lock.json"]
    modules = root / "node_modules"
    if _linked(modules) or not modules.is_dir():
        raise ValueError("candidate node_modules must be a regular directory")
    for current, directories, names in os.walk(modules, followlinks=False):
        for name in directories:
            if _linked(Path(current) / name):
                raise ValueError("candidate package directories must not contain links or junctions")
        files.extend(Path(current) / name for name in names)
    pins = {}
    for path in sorted(files):
        _regular(path)
        pins[str(path)] = _digest(path)
    return pins


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _native(root, target):
    wrapper = root / "node_modules" / "@openai" / "codex"
    package = _read(wrapper / "package.json")
    if (not isinstance(package, dict) or package.get("name") != "@openai/codex"
            or not isinstance(package.get("version"), str) or not _VERSION.fullmatch(package["version"])):
        raise ValueError("installed Codex package identity is invalid")
    system, arch = target
    suffix = f"{system}-{arch}"
    candidates = [root / "node_modules" / "@openai" / f"codex-{suffix}",
                  wrapper / "node_modules" / "@openai" / f"codex-{suffix}"]
    native = next((path for path in candidates if path.is_dir()), None)
    if native is None:
        raise ValueError("npm did not install the native Codex platform package")
    native_package = _read(native / "package.json")
    if not isinstance(native_package, dict) or native_package.get("version") != f"{package['version']}-{suffix}":
        raise ValueError("native Codex package version differs from the wrapper")
    executable = "codex.exe" if system == "win32" else "codex"
    binaries = list((native / "vendor").glob(f"*/bin/{executable}"))
    if len(binaries) != 1:
        raise ValueError("native Codex executable is missing or ambiguous")
    binary = binaries[0]
    vendor = binary.parent.parent
    path_prepend = [binary.parent, vendor / "codex-path", vendor / "codex-resources"]
    return package["version"], binary, [str(path) for path in path_prepend if path.is_dir()]


def _npm_tools(node=None, npm_cli=None):
    """Invoke npm through Node on both hosts, without executing a Windows shell shim."""
    node = node or shutil.which("node")
    if not node:
        raise ValueError("Node.js is required; select its executable with --node")
    node = Path(node).expanduser().resolve(strict=True)
    if not node.is_file():
        raise ValueError("Node executable must be a file")
    if npm_cli is None:
        npm = shutil.which("npm")
        if not npm:
            raise ValueError("npm is required; select npm-cli.js with --npm-cli")
        npm = Path(npm)
        npm_cli = (npm.parent / "node_modules/npm/bin/npm-cli.js"
                   if npm.suffix.lower() in (".cmd", ".ps1", ".exe") else npm.resolve())
    npm_cli = Path(npm_cli).expanduser().resolve(strict=True)
    if not npm_cli.is_file():
        raise ValueError("npm CLI entry point must be a file")
    return str(node), str(npm_cli)


def prepare(directory, *, version="latest", node=None, npm_cli=None, runner=subprocess.run):
    """Install into a fresh operator-owned directory and record the resolved version."""
    if version != "latest" and not _VERSION.fullmatch(version):
        raise ValueError("version must be latest or one explicit release version")
    root = _directory(directory)
    if root.exists():
        raise ValueError("candidate directory already exists; choose a fresh directory")
    if any((path / ".git").exists() for path in root.parents):
        raise ValueError("candidate directory must be outside repository checkouts")
    node, npm_cli = _npm_tools(node, npm_cli)
    target_result = runner([node, "-p", "JSON.stringify({platform:process.platform,arch:process.arch})"],
                           check=True, capture_output=True, text=True, encoding="utf-8", timeout=30)
    target_info = json.loads(target_result.stdout)
    target = (target_info["platform"], target_info["arch"])
    if target not in _TARGETS:
        raise ValueError("unsupported native Codex platform or architecture")
    root.mkdir(parents=True, exist_ok=False)
    # Fresh cwd excludes checkout .npmrc files. Scripts and bin links are unnecessary for native launching.
    try:
        installed = runner([node, npm_cli, "install", "--prefix", str(root), "--global=false", "--include=optional",
                            "--registry=https://registry.npmjs.org", "--@openai:registry=https://registry.npmjs.org",
                            "--no-audit", "--no-fund", "--ignore-scripts", "--no-bin-links", "--package-lock",
                            f"@openai/codex@{version}"], cwd=root, check=True, capture_output=True,
                           text=True, encoding="utf-8", timeout=600)
    except subprocess.CalledProcessError as exc:
        (root / "npm-install.stdout.log").write_text(exc.stdout or "", encoding="utf-8")
        (root / "npm-install.stderr.log").write_text(exc.stderr or "", encoding="utf-8")
        raise
    (root / "npm-install.stdout.log").write_text(installed.stdout, encoding="utf-8")
    (root / "npm-install.stderr.log").write_text(installed.stderr, encoding="utf-8")
    pins = _inventory(root)
    resolved, binary, path_prepend = _native(root, target)
    if version != "latest" and resolved != version:
        raise ValueError("npm resolved a different explicit Codex version")
    result = runner([str(binary), "--version"], cwd=root, check=True, capture_output=True,
                    text=True, encoding="utf-8", timeout=30)
    if result.stdout.strip() != f"codex-cli {resolved}":
        raise ValueError("native executable does not report the installed Codex version")
    receipt = {"schema_version": 1, "directory": str(root), "requested_version": version,
               "version": resolved, "target": list(target), "binary": str(binary),
               "path_prepend": path_prepend, "pins": pins}
    (root / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def verify(receipt_path):
    """Read package hashes only; this command never starts a CLI or service."""
    path = Path(receipt_path).expanduser()
    root = _directory(path.parent)
    if path.name != RECEIPT:
        raise ValueError(f"receipt must be named {RECEIPT}")
    _regular(path)
    receipt = _read(path)
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("directory") != str(root):
        raise ValueError("runtime receipt does not identify this candidate directory")
    target = tuple(receipt.get("target", ()))
    if target not in _TARGETS:
        raise ValueError("runtime receipt has an unsupported target")
    if receipt.get("pins") != _inventory(root):
        raise ValueError("candidate package files changed, were added, or are missing")
    version, binary, path_prepend = _native(root, target)
    if (receipt.get("version") != version or receipt.get("binary") != str(binary)
            or receipt.get("path_prepend") != path_prepend):
        raise ValueError("runtime receipt differs from the installed package layout")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("prepare", help="Install a separate npm runtime candidate")
    install.add_argument("--directory", required=True)
    install.add_argument("--version", default="latest")
    install.add_argument("--node")
    install.add_argument("--npm-cli")
    check = commands.add_parser("verify", help="Verify a candidate's complete package hashes")
    check.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = (prepare(args.directory, version=args.version, node=args.node, npm_cli=args.npm_cli)
                   if args.command == "prepare" else verify(args.receipt))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # npm failure output may contain private host configuration; retain logs, report only the type.
        parser.exit(1, f"Codex runtime {args.command} failed ({type(exc).__name__}).\n")
    print(json.dumps({"version": receipt["version"], "binary": receipt["binary"],
                      "path_prepend": receipt["path_prepend"], "files": len(receipt["pins"]),
                      "receipt": str(Path(receipt["directory"]) / RECEIPT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
