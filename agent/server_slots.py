"""Controller-owned local Docker services, fenced by the ordinary resource ledger.

Game binaries and tests run in the resumed worker, never via Docker exec or outside
worker containment. No production endpoint, bind mount, shell or credentials are used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from .launcher import _read_worker_text, _write_worker_file
from .ledger import Ledger, LedgerError
from .server_test_plan import FULL_PLAN, profile, scope, test_argv, timeout_seconds, validate_plan

ROLES = ("mongo", "redis", "etcd", "nats")
IMAGE_ROLES = (*ROLES, "proxy")
LIMITS = {"mongo": 1536, "redis": 256, "etcd": 512, "nats": 256}
IMAGE = re.compile(r"(?:sha256:[0-9a-f]{64}|[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64})\Z")


def relay_source_digest():
    source = Path(__file__).parent / "probes" / "server-proxy"
    return hashlib.sha256(b"".join((source / name).read_bytes().replace(b"\r\n", b"\n")
                                  for name in ("main.go", "go.mod", "Dockerfile"))).hexdigest()


class ServerSlotError(RuntimeError):
    pass


def validate_settings(raw):
    if raw == {}:
        return {}
    if (not isinstance(raw, dict) or set(raw) - {"count", "images", "docker", "context", "timeout_seconds"}
            or type(raw.get("count")) is not int or not 1 <= raw["count"] <= 16):
        raise ValueError("server_slots needs count (1-16), pinned images and an absolute Docker executable")
    images = raw.get("images")
    if (not isinstance(images, dict) or set(images) != set(IMAGE_ROLES)
            or any(not isinstance(image, str) or not IMAGE.fullmatch(image) for image in images.values())):
        raise ValueError("server_slots images must pin mongo, redis, etcd, nats and proxy by SHA-256")
    if not isinstance(raw.get("docker"), str) or not Path(raw["docker"]).is_absolute():
        raise ValueError("server_slots docker must be an absolute executable path")
    context = raw.get("context", "default")
    if not isinstance(context, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", context):
        raise ValueError("server_slots context must name a local Docker context")
    timeout = raw.get("timeout_seconds", 1800)
    if type(timeout) is not int or not 30 <= timeout <= 7200:
        raise ValueError("server_slots timeout_seconds must be 30-7200")
    return {**raw, "context": context, "timeout_seconds": timeout}


def _safe_directory(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ServerSlotError("server slot path contains a link")
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_resource(slot, reservation, root):
    """Read only the controller's journal. The worker-writable ledger cannot redirect this read."""
    if root is None or not re.fullmatch(r"server_slot:[1-9][0-9]*", slot["slot_id"]):
        raise ServerSlotError("server slot has no configured controller root")
    folder = Path(root).absolute() / ("slot-" + slot["slot_id"].split(":")[1])
    if Path(slot["folder"]).absolute() != folder:
        raise ServerSlotError("server resource folder does not match host configuration")
    _safe_directory(folder)
    lease = json.loads(_read_worker_text(folder / "lease.json"))
    if (lease.get("state") != "ready" or lease.get("reservation_id") != reservation["reservation_id"]
            or lease.get("item_id") != reservation["item_id"] or lease.get("commit") != reservation["commit_sha"]):
        raise ServerSlotError("server resource journal is not this reservation's ready lease")
    if type(lease.get("expires_at")) not in (int, float) or lease["expires_at"] <= time.time():
        raise ServerSlotError("server resource lease expired")
    ports = lease["ports"]
    if set(ports) != set(ROLES) or any(type(p) is not int or not 1 <= p <= 65535 for p in ports.values()):
        raise ServerSlotError("invalid server resource endpoints")
    plan = validate_plan(lease.get("test_plan", FULL_PLAN))
    if reservation.get("server_test_plan") is not None and plan != reservation["server_test_plan"]:
        raise ServerSlotError("server test plan does not match its reservation")
    return {"kind": "server_slot", "mode": "batch", "slot": slot["slot_id"],
            "test_plan": plan, "memory_profile": profile(plan), "test_argv": test_argv(plan),
            "verification_scope": scope(plan), "ready_at": lease.get("ready_at"),
            "commit": lease["commit"], "expires_at": lease["expires_at"], "ports": ports,
            "environment": {"MONGO_URI": f"mongodb://127.0.0.1:{ports['mongo']}/farmbot?replicaSet=rs0",
                            "REDIS_ADDR": f"127.0.0.1:{ports['redis']}",
                            "REDIS_URI": f"127.0.0.1:{ports['redis']}",
                            "ETCD_ENDPOINTS": f"http://127.0.0.1:{ports['etcd']}",
                            "NATS_URL": f"nats://127.0.0.1:{ports['nats']}",
                            "MONGO_REQUIRED": "1", "REDIS_REQUIRED": "1", "CLUSTER_REQUIRED": "1"}}


class DockerServices:
    """Only fixed service commands. Cleanup targets exact names and verified ownership labels."""
    def __init__(self, settings, *, run=subprocess.run, clock=time.monotonic, sleep=time.sleep):
        self.settings = validate_settings(settings)
        self.run, self.clock, self.sleep = run, clock, sleep
        self.endpoint = None

    def command(self, *args, timeout=30, missing=False):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("DOCKER_", "COMPOSE_", "FARMBOT_", "FAKE_CLI_"))
               and key.upper() not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}}
        try:
            selector = (["--host", self.endpoint] if self.endpoint else ["--context", self.settings["context"]])
            result = self.run([self.settings["docker"], *selector, *args],
                              env=env, text=True, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ServerSlotError("local Docker command unavailable or timed out: " + args[0]) from exc
        if result.returncode:
            absent = any(message in result.stderr.lower()
                         for message in ("no such object", "no such container", "no such network", "no such volume"))
            absent = absent or (args[0] == "network" and f"network {args[-1]} not found" in result.stderr)
            if missing and result.returncode == 1 and absent:
                return None
            # Raw CLI output may contain host paths. Private receipts retain identities instead.
            raise ServerSlotError("local Docker command failed: " + args[0])
        return result.stdout.strip()

    def binding(self):
        context = json.loads(self.command("context", "inspect", self.settings["context"]))[0]
        endpoint = context["Endpoints"]["docker"]["Host"]
        if not (endpoint.startswith("unix:///") or endpoint.startswith("npipe:////./pipe/")):
            raise ServerSlotError("server slots require a local Docker socket context")
        self.endpoint = endpoint  # every subsequent command pins the socket, never a mutable context alias
        info = json.loads(self.command("info", "--format", "{{json .}}"))
        if info.get("OSType") != "linux" or not isinstance(info.get("ID"), str) or not info["ID"]:
            raise ServerSlotError("server slots require a verified local Linux daemon identity")
        return {"daemon_id": info["ID"], "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest()}

    def preflight(self, count):
        self.binding()
        info = json.loads(self.command("info", "--format", "{{json .}}"))
        if info.get("OSType") != "linux" or info.get("MemTotal", 0) < (count * 2624 + 2048) * 1024**2:
            raise ServerSlotError("Linux Docker backend lacks the configured pool's memory headroom")
        for role, image in self.settings["images"].items():
            # No automatic pull: installing/verifying the host's pinned images is an operator step.
            found = json.loads(self.command("image", "inspect", image))[0]
            if found.get("Os") != "linux":
                raise ServerSlotError("server image must target Linux: " + role)
            expected = {"etcd": ["/etcd"], "nats": ["/nats-server"], "proxy": ["/server-proxy"]}.get(role)
            if expected and found.get("Config", {}).get("Entrypoint") != expected:
                raise ServerSlotError("server image lacks the supported pinned service entrypoint: " + role)
            if role == "proxy":
                digest = relay_source_digest()
                if (found.get("Config", {}).get("Labels") or {}).get("farmbot.server.proxy.source_sha256") != digest:
                    raise ServerSlotError("relay image provenance does not match this FarmBot revision")

    @staticmethod
    def labels(lease):
        return {"farmbot.server.owner": lease["owner"], "farmbot.server.reservation": lease["reservation_id"],
                "farmbot.server.slot": lease["slot"]}

    def inspect(self, kind, name, lease):
        output = self.command(kind, "inspect", name, missing=True)
        if output is None:
            return None
        value = json.loads(output)[0]
        labels = value.get("Config", {}).get("Labels") if kind == "container" else value.get("Labels")
        if not isinstance(labels, dict) or any(labels.get(k) != v for k, v in self.labels(lease).items()):
            raise ServerSlotError("Docker resource ownership mismatch; investigation required")
        return value

    def start(self, lease):
        if lease.get("docker_binding") != self.binding():
            raise ServerSlotError("Docker daemon/context changed before server setup")
        label_args = [word for key, value in self.labels(lease).items() for word in ("--label", key + "=" + value)]
        name = lease["project"]
        # The intent containing every name/port is durable before the first creation. A crash between
        # CLI completion and its return can therefore be reconciled without discovering arbitrary resources.
        targets = [("network", name), ("network", name + "-ingress"),
                   *[("container", name + "-" + role) for role in IMAGE_ROLES],
                   *[("volume", name + "-" + role) for role in ("mongo", "etcd", "nats")]]
        if any(self.inspect(kind, target, lease) is not None for kind, target in targets):
            raise ServerSlotError("new server lease already has Docker resources")
        self.command("network", "create", "--internal", *label_args, name)
        self.command("network", "create", "--driver", "bridge", "--opt",
                     "com.docker.network.bridge.host_binding_ipv4=127.0.0.1", *label_args, name + "-ingress")
        for role in ("mongo", "etcd", "nats"):
            self.command("volume", "create", *label_args, name + "-" + role)
        ports = lease["ports"]
        commands = {
            "mongo": ["mongod", "--replSet", "rs0", "--bind_ip_all", "--port", str(ports["mongo"]),
                      "--wiredTigerCacheSizeGB", "0.5"],
            "redis": ["redis-server", "--appendonly", "no", "--save", ""],
            "etcd": ["--name=slot", "--data-dir=/data", "--listen-client-urls=http://0.0.0.0:2379",
                     "--advertise-client-urls=http://127.0.0.1:" + str(ports["etcd"]),
                     "--listen-peer-urls=http://0.0.0.0:2380", "--initial-advertise-peer-urls=http://etcd:2380",
                     "--initial-cluster=slot=http://etcd:2380", "--initial-cluster-state=new"],
            "nats": ["-a", "0.0.0.0", "-p", "4222", "-js", "-sd", "/data"],
        }
        inside = {"mongo": ports["mongo"], "redis": 6379, "etcd": 2379, "nats": 4222}
        destinations = {"mongo": "/data/db", "etcd": "/data", "nats": "/data"}
        for role in ROLES:
            arguments = ["container", "run", "--pull", "never", "--detach", "--name", name + "-" + role,
                         "--network", name, "--network-alias", role, *label_args,
                         "--memory", str(LIMITS[role]) + "m", "--memory-swap", str(LIMITS[role]) + "m",
                         "--cpus", "2" if role == "mongo" else "1", "--pids-limit", "256",
                         "--restart", "no", "--security-opt", "no-new-privileges:true",
                         "--log-driver", "local", "--log-opt", "max-size=10m", "--log-opt", "max-file=3"]
            if role in destinations:
                arguments += ["--mount", f"type=volume,src={name}-{role},dst={destinations[role]}"]
            self.command(*arguments, self.settings["images"][role], *commands[role])
        proxy = name + "-proxy"
        bindings = [word for role in ROLES for word in
                    ("--publish", f"127.0.0.1:{ports[role]}:{inside[role]}")]
        self.command("container", "create", "--pull", "never", "--name", proxy, "--network", name + "-ingress", *label_args,
                     *bindings, "--memory", "64m", "--memory-swap", "64m", "--cpus", "0.5",
                     "--pids-limit", "128", "--read-only", "--cap-drop", "ALL",
                     "--security-opt", "no-new-privileges:true", "--restart", "no", "--user", "65534:65534",
                     "--env", "GOMEMLIMIT=48MiB", "--log-driver", "local", "--log-opt", "max-size=1m",
                     "--log-opt", "max-file=2", self.settings["images"]["proxy"],
                     *[f"{inside[role]}={role}:{inside[role]}" for role in ROLES])
        self.command("network", "connect", name, proxy)
        self.command("container", "start", proxy)
        deadline = self.clock() + 120
        while self.clock() < deadline:
            try:
                mongo = name + "-mongo"
                self.command("exec", mongo, "mongosh", "--quiet", "--port", str(ports["mongo"]), "--eval",
                             'try { rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:' +
                             str(ports["mongo"]) + '"}]}) } catch(e) { if(e.code !== 23) throw e }')
                self.command("exec", mongo, "mongosh", "--quiet", "--port", str(ports["mongo"]), "--eval",
                             'if (!db.hello().isWritablePrimary) quit(1)')
                if self.command("exec", name + "-redis", "redis-cli", "ping") != "PONG":
                    raise ServerSlotError("Redis not ready")
                self.command("exec", name + "-etcd", "/etcdctl", "endpoint", "health")
                with socket.create_connection(("127.0.0.1", ports["nats"]), timeout=2) as connection:
                    if not connection.recv(4096).startswith(b"INFO "):
                        raise ServerSlotError("NATS not ready")
                return
            except (ServerSlotError, OSError, subprocess.TimeoutExpired):
                self.sleep(1)
        raise ServerSlotError("server services did not become healthy within 120 seconds")

    def cleanup(self, lease):
        if lease.get("docker_binding") != self.binding():
            raise ServerSlotError("Docker daemon/context changed before server cleanup")
        # Inspect every target before deleting any. A foreign collision means leave all evidence intact.
        targets = [("container", lease["project"] + "-" + role) for role in IMAGE_ROLES]
        targets += [("volume", lease["project"] + "-" + role) for role in ("mongo", "etcd", "nats")]
        targets += [("network", lease["project"]), ("network", lease["project"] + "-ingress")]
        found = [(kind, name, self.inspect(kind, name, lease)) for kind, name in targets]
        for kind, name, value in found:
            if value is None:
                continue
            identity = value.get("Id") or value.get("Name")
            # Recheck immediately before removal. Names are unique per immutable reservation.
            current = self.inspect(kind, name, lease)
            if current is None or (current.get("Id") or current.get("Name")) != identity:
                raise ServerSlotError("Docker resource identity changed during cleanup")
            if kind == "container":
                self.command(kind, "stop", "--time", "10", identity)
                self.command(kind, "rm", identity)
            else:
                self.command(kind, "rm", identity)
        if any(self.inspect(kind, name, lease) is not None for kind, name in targets):
            raise ServerSlotError("Docker cleanup could not be verified")


class ServerSlotPool:
    def __init__(self, ledger_factory, settings, *, host, root, state_dir, quiescent, admission,
                 services=None, clock=time.time, single_only=False):
        self.single_only = single_only
        self.ledger = ledger_factory()
        self.settings = validate_settings(settings)
        self.host, self.root = host, Path(root).absolute()
        self.state_dir, self.quiescent, self.admission = state_dir, quiescent, admission
        self.services, self.clock = services or DockerServices(settings), clock
        self.owner = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()
        self.ready = False

    def folder(self, slot):
        if not isinstance(slot, str) or not re.fullmatch(r"server_slot:[1-9][0-9]*", slot):
            raise ServerSlotError("invalid configured server slot identifier")
        return self.root / ("slot-" + slot.split(":")[1])

    def ensure(self):
        _safe_directory(self.root)
        self.services.preflight(self.settings["count"])
        for index in range(1, self.settings["count"] + 1):
            slot = "server_slot:" + str(index)
            folder = _safe_directory(self.folder(slot))
            current = self.ledger.slot(slot)
            if current and (current["kind"] != "server_slot" or current["host"] != self.host
                            or Path(current["folder"]).absolute() != folder):
                raise ServerSlotError("server slot configuration changed; recovery required")
            self.ledger.ensure_slot(slot, kind="server_slot", host=self.host, folder=str(folder))
            # A journal means the previous controller may have created services. Never mark it free.
            if (folder / "lease.json").exists() and self.ledger.slot(slot)["state"] == "idle_closed":
                self.ledger.set_slot_state(slot, "held")
        self.ready = True

    def _path(self, slot):
        return _safe_directory(self.folder(slot)) / "lease.json"

    def _save(self, lease):
        _write_worker_file(self._path(lease["slot"]), json.dumps(lease, indent=2) + "\n", sync=True)

    def _lease(self, slot):
        lease = json.loads(_read_worker_text(self._path(slot)))
        if (lease.get("owner") != self.owner or lease.get("slot") != slot
                or not re.fullmatch(r"[0-9a-f-]{36}", lease.get("reservation_id", ""))
                or lease.get("project") != "farmbot-server-" + self.owner[:12] + "-" +
                   lease["reservation_id"].replace("-", "")):
            raise ServerSlotError("server lease journal ownership mismatch")
        reservation = self.ledger.reservation(lease["reservation_id"])
        if (not reservation or reservation["resource"] != slot or reservation["kind"] != "server_slot"
                or reservation["item_id"] != lease.get("item_id")
                or reservation["commit_sha"] != lease.get("commit")):
            raise ServerSlotError("server lease journal does not match the fenced reservation")
        return lease, reservation

    def _hold(self, slot, reason):
        self.ledger.set_slot_state(slot, "held")
        self.ledger.note(slot, "server_slot_held", reason[:200])

    def tick(self):
        if not self.ready:
            for request in self.ledger.reservations(states=("queued",)):
                if request["kind"] == "server_slot":
                    self.ledger.resource_wait_detail(request["reservation_id"],
                        {"observed_at": self.clock(), "blockers": ["server_pool_unavailable"]})
            return {"settled": 0, "granted": 0, "unavailable": True}
        settled, granted = 0, 0
        for slot in self.ledger.slots(kind="server_slot", host=self.host):
            if slot["state"] in ("idle_closed", "held"):
                continue
            try:
                lease, reservation = self._lease(slot["slot_id"])
                item = self.ledger.item(lease["item_id"])
                if item["generation"] != reservation["generation"]:
                    raise ServerSlotError("server lease belongs to a different worker generation")
                expired = self.clock() >= lease["expires_at"]
                if expired and reservation["state"] == "active":
                    self.ledger.server_verification_timeout(reservation['reservation_id'], lease['expires_at'])
                    continue  # scheduler owns stopping the contained worker, not this loop
                if reservation["state"] == "active" and item["state"] in ("queued", "running"):
                    continue
                if item["state"] == "awaiting_resource" and reservation["state"] == "active":
                    self.ledger.fail_queued(item["id"], "server startup interrupted; retained lease will be cleaned")
                if not self.quiescent(item["id"]):
                    continue
                self.services.cleanup(lease)
                if reservation["state"] in ("active", "cancel_requested"):
                    self.ledger.release(reservation["reservation_id"], lease["token"], "server services settled")
                lease["state"] = "cleaned"
                self._save(lease)
                # Preserve every journal as recovery evidence; only the current pointer is removed.
                self._path(slot["slot_id"]).replace(self.folder(slot["slot_id"]) /
                                                    (lease["reservation_id"] + ".json"))
                self.ledger.set_slot_state(slot["slot_id"], "idle_closed")
                settled += 1
            except Exception as exc:
                self._hold(slot["slot_id"], "server settlement failed: " + type(exc).__name__)
        from .maintenance import Maintenance
        if Maintenance(self.ledger).requested():
            return {"settled": settled, "granted": 0}
        for _ in range(self.settings["count"]):
            pending = self.ledger.reservation_queue('server_slot', single_only=self.single_only)
            def waiting(request, blockers, **extra):
                self.ledger.resource_wait_detail(request["reservation_id"],
                    {"observed_at": self.clock(), "blockers": blockers,
                     "profile": profile(request["server_test_plan"]) if request["server_test_plan"] else "full",
                     "requested_mib": self.admission.cost(request), **extra})
            for position, request in enumerate(pending[1:], 2):
                waiting(request, ["fifo"], position=position)
            if not pending:
                break
            request = pending[0]
            from .execution import single, resource_ready
            requesting_item = self.ledger.item(request['item_id'])
            if single(requesting_item):
                if (requesting_item['state'] != 'running' or not requesting_item['worker_pid']
                        or requesting_item['lease_expires_at'] <= self.ledger.clock()
                        or requesting_item['withdraw_deadline'] is not None):
                    waiting(request, ['worker_unavailable'])
                    break
            elif not self.quiescent(request["item_id"]):
                waiting(request, ["worker_teardown"])
                break  # Preserve FIFO while the checkpointing attempt finishes exiting.
            if request["server_test_plan"] is None and self.ledger.replan_legacy_server_request(request["reservation_id"]):
                continue
            configured = {"server_slot:" + str(index) for index in range(1, self.settings["count"] + 1)}
            excluded = [s["slot_id"] for s in self.ledger.slots(kind="server_slot", host=self.host)
                        if s["slot_id"] not in configured]
            if not any(s["slot_id"] in configured and s["state"] in self.ledger.FREE_SLOT_STATES
                       for s in self.ledger.slots(kind="server_slot", host=self.host)):
                waiting(request, ["server_capacity"])
                break
            reservation = self.admission.acquire(self.ledger, "server_slot", host=self.host, owner="server-pool",
                                                  single_only=self.single_only,
                                                  exclude_resources=excluded, expected_reservation=request["reservation_id"])
            if reservation is None:
                break
            slot, item_id = reservation["resource"], reservation["item_id"]
            sockets, ports = [], {}
            try:
                for role in ROLES:
                    sock = socket.socket()
                    sock.bind(("127.0.0.1", 0))
                    sockets.append(sock)
                    ports[role] = sock.getsockname()[1]
            finally:
                for sock in sockets:
                    sock.close()
            lease = {"slot": slot, "owner": self.owner, "reservation_id": reservation["reservation_id"],
                     "item_id": item_id, "commit": reservation["commit_sha"], "token": reservation["token"],
                     "test_plan": reservation["server_test_plan"],
                     "memory_profile": profile(reservation["server_test_plan"]),
                     "project": "farmbot-server-" + self.owner[:12] + "-" + reservation["reservation_id"].replace("-", ""),
                     "ports": ports, "state": "starting", "expires_at": self.clock() + self.settings["timeout_seconds"]}
            try:
                if hasattr(self.services, "binding"):
                    lease["docker_binding"] = self.services.binding()
                self._save(lease)
                self.services.start(lease)
                lease["state"] = "ready"
                lease['ready_at'] = self.clock()
                lease['verification_scope'] = scope(reservation['server_test_plan'])
                lease['timeout_seconds'] = timeout_seconds(reservation['server_test_plan'], self.settings['timeout_seconds'])
                lease['expires_at'] = lease['ready_at'] + lease['timeout_seconds']
                self._save(lease)
                current = self.ledger.reservation(reservation["reservation_id"])
                if current["state"] != "active":
                    continue  # cancelled during startup; next tick settles after worker exit
                directory = Path(self.state_dir(item_id))
                directory.mkdir(parents=True, exist_ok=True)
                public = {key: lease[key] for key in ("slot", "reservation_id", "commit", "ports", "expires_at")}
                _write_worker_file(directory / "server-resource.json", json.dumps(public), sync=True)
                _write_worker_file(directory / "reservation.token", reservation["token"], mode=0o600, sync=True)
                self.ledger.set_slot_state(slot, "batch_busy", parked_commit=lease["commit"])
                if single(self.ledger.item(item_id)):
                    resource_ready(self.ledger, reservation)
                else:
                    self.ledger.resume(item_id, "isolated server services ready")
                granted += 1
            except Exception as exc:
                self._hold(slot, "server preparation failed: " + type(exc).__name__)
                if single(self.ledger.item(item_id)):
                    with self.ledger._transaction():
                        self.ledger.connection.execute("UPDATE work_items SET stage='resource-preparation-failed',updated_at=? "
                            "WHERE id=? AND state='running'", (self.clock(), item_id))
                        self.ledger._audit(item_id, 'resource_preparation_failed', type(exc).__name__)
                else:
                    self.ledger.fail_queued(item_id, "server preparation failed; slot held for investigation")
        return {"settled": settled, "granted": granted}

    def recover(self, slot, reason):
        """Trusted operator recovery never bypasses worker quiescence or Docker ownership."""
        if not isinstance(reason, str) or not reason.strip():
            raise ServerSlotError("server recovery requires a reason")
        current = self.ledger.slot(slot)
        if not current or current["kind"] != "server_slot" or current["state"] != "held":
            raise ServerSlotError("only a held server slot can be recovered")
        lease, reservation = self._lease(slot)
        if not self.quiescent(lease["item_id"]):
            raise ServerSlotError("server worker teardown is not verified")
        self.services.cleanup(lease)
        if reservation["state"] in ("active", "cancel_requested"):
            self.ledger.release(reservation["reservation_id"], lease["token"], "server recovery: " + reason)
        item = self.ledger.item(lease["item_id"])
        if item["generation"] == reservation["generation"] and item["state"] == "awaiting_resource":
            self.ledger.fail_queued(lease["item_id"], "server lease recovered; explicit retry required")
        lease["state"] = "recovered"
        self._save(lease)
        self._path(slot).replace(self.folder(slot) / (lease["reservation_id"] + ".json"))
        self.ledger.set_slot_state(slot, "idle_closed")
        self.ledger.note(slot, "server_slot_recovered", reason[:200])
        return self.ledger.slot(slot)

    def close(self):
        # Shutdown does not imply workers exited. Journals and services survive for verified reconciliation.
        self.ledger.close()
