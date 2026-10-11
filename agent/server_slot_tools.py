"""Trusted operator packaging for the fixed relay. No bot config/state, pulls or credential setup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .server_slots import DockerServices, IMAGE_ROLES, _safe_directory, relay_source_digest


def prepare(go, docker, output, *, context="default"):
    go, docker, output = Path(go), Path(docker), Path(output)
    if not go.is_absolute() or not docker.is_absolute() or not output.is_absolute() or output.exists():
        raise ValueError("relay preparation needs absolute executables and a new absolute output directory")
    root = _safe_directory(output)
    services = DockerServices({"docker": str(docker), "context": context, "count": 1,
                               "images": dict.fromkeys(IMAGE_ROLES, "sha256:" + "0" * 64)})
    binding = services.binding()  # Reject remote aliases before building anything.
    info = json.loads(services.command("info", "--format", "{{json .}}"))
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(info.get("Architecture"))
    if arch is None:
        raise ValueError("unsupported local Linux Docker architecture")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("FARMBOT_", "FAKE_CLI_", "DOCKER_", "COMPOSE_", "LARKSUITE_CLI_"))
           and key.upper() not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}}
    env.update(GOENV="off", GOWORK="off", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off",
               GOCACHE=str(root / "go-cache"), GOTMPDIR=str(root / "tmp"))
    for name in ("GOOS", "GOARCH", "GOFLAGS", "GOEXPERIMENT", "CGO_ENABLED"):
        env.pop(name, None)
    (root / "tmp").mkdir()
    version = subprocess.check_output([str(go), "version"], env=env, text=True, encoding="utf-8", timeout=10).strip()
    if not version.startswith("go version go1.26.6 "):
        raise ValueError("relay preparation requires the pinned Go 1.26.6")
    source = Path(__file__).parent / "probes" / "server-proxy"
    digest = relay_source_digest()
    with (root / "go-test.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(go), "test", "-buildvcs=false", "-count=1", "-timeout=30s", "./..."], cwd=source,
                       env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90, check=True)
    build = root / "context"
    build.mkdir()
    binary = build / "server-proxy"
    with (root / "go-build.log").open("w", encoding="utf-8") as log:
        subprocess.run([str(go), "build", "-buildvcs=false", "-trimpath", "-o", str(binary), "."], cwd=source,
                       env=dict(env, GOOS="linux", GOARCH=arch, CGO_ENABLED="0"), stdout=log,
                       stderr=subprocess.STDOUT, timeout=180, check=True)
    if relay_source_digest() != digest:
        raise ValueError("relay source changed during build")
    shutil.copyfile(source / "Dockerfile", build / "Dockerfile")
    if services.binding() != binding:
        raise ValueError("local Docker identity changed during preparation")
    image = "farmbot-server-proxy:" + digest[:16]
    # Only the two-file context reaches Docker: Go cache and private reports remain outside it.
    services.command("build", "--network", "none", "--pull=false", "--label",
                     "farmbot.server.proxy.source_sha256=" + digest, "--tag", image, str(build), timeout=180)
    metadata = json.loads(services.command("image", "inspect", image))[0]
    receipt = {"image": metadata["Id"], "source_sha256": digest,
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "architecture": arch,
               "go_version": version, "native_test_passed": True, "image_bytes": metadata["Size"],
               "docker_binding": binding}
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", required=True)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--context", default="default")
    args = parser.parse_args(argv)
    receipt = prepare(args.go, args.docker, args.output, context=args.context)
    print(json.dumps({key: receipt[key] for key in ("image", "source_sha256", "binary_sha256", "architecture", "native_test_passed")}))


if __name__ == "__main__":
    main()
