# Docker host recovery on Windows

Install the watchdog only under explicit host-operations authorization. The
controller's configured Docker executable, original Linux daemon ID, data disk
and existing Windows host task must already be verified. The watchdog runs
outside a packaged app's process job and registry/settings view. It does not
install Docker, repair arbitrary configuration, reset WSL or restart live workers.

Keep configuration and logs under private stable host state. The JSON fields are:

| Field | Required evidence |
| --- | --- |
| `state_root` | Absolute private watchdog directory |
| `docker`, `endpoint`, `daemon_id` | Pinned CLI, local named pipe and original daemon ID |
| `settings`, `data_root` | Normal Windows-user settings and original WSL data directory |
| `pins` | Absolute file paths mapped to SHA-256 for the CLI, backend, host launcher and interpreter |
| `host_task`, `user` | Existing `FarmBot-Docker-*` task and its exact user identity |
| `action` | Its exact `Execute`, `Arguments` and `WorkingDirectory` values |

The host task must launch the backend independently, retain its PID, creation
time and exit code, and return when the backend exits. No task may launch a
second backend while another exists. Keep the original registration/settings
visible to the normal Windows user; packaged-app registry or AppData copies
alone do not establish that an independent launch will use the original disk.

```powershell
./scripts/install-docker-watchdog.ps1 -ConfigPath C:\FarmBot\docker-watchdog\config.json -Python C:\FarmBot\tools\python.exe
```

The installer refuses an existing watchdog task or script. Task Scheduler runs
it at user logon and once per minute while logged in. A healthy engine is left
alone; an absent backend starts through its verified host task. Failed starts
back off for one, two, four, then five minutes. A live but unresponsive backend,
changed daemon/data location, changed pins or changed task action is reported,
never force-stopped or adopted. This does not provide recovery while logged out.

Read `status.json` for fresh health and `events.jsonl` for transitions/restarts.
The separate backend launch log/exit receipt supplies the next termination's
evidence. Preserve these and Docker/Windows logs during investigation. Avoid
interpreting Resource Saver's intentional engine sleep as a backend-process crash.

Create a file named `pause` in the watchdog directory for deliberate Docker
maintenance; remove that file when maintenance is complete. A paused watchdog
does not stop Docker. Disabling its Windows task also suspends checks. Reinstall
deliberately when pinned executables, the host task or the selected data root change.

Validate with offline watchdog/server tests first. A live restart drill needs
fresh FarmBot maintenance readiness, verified worker and slot quiescence, no
running Docker containers, and the original daemon identity. Stop Docker through
its supported shutdown path, then confirm the watchdog starts the original
engine, the same pinned images remain available, and maintenance can resume.
Never kill an active backend merely to demonstrate recovery.
