# Android VM dispenser

A Linux service that gives each MCP client an isolated, ephemeral QEMU VM running Android 14 (API 34, x86_64). It works with stdio MCP clients.

The dispenser requires Python 3.10+, QEMU/KVM, qemu-img, and Android platform tools (`adb`) on the host. It also needs a configured base image: an Android 14 (API 34) x86_64 build (for example Android-x86 or Bliss OS) with root ADB over TCP (adbd listening on TCP port 5555) and the guest Android driver installed at `/data/local/tmp/android-driver`. Automatic host viewers additionally require a graphical session and remote-viewer. The CLI starts a detached dispenser on demand; no system service installation is required.

## Connect an MCP client

Configure your client to launch this executable. Replace the executable path with your installation path:

```json
{
  "command": "/absolute/path/android-vm-dispenser/android-vm-dispenser",
  "args": ["mcp"]
}
```

This shows the command and arguments, not a universal client configuration format. Adapt the surrounding configuration to your MCP client. Each client connection generates its own random identity and gets an isolated VM. Disconnecting deletes the MCP connection's VM; reconnecting creates a fresh one.

Configure startup and tool timeouts in your client to allow for VM boot. The dispenser's ADB readiness deadline defaults to 280 seconds, but the client may impose a shorter deadline. A fresh Android VM can take longer than 15 seconds to become ready.

## Transport and lifecycle

The transport is newline-delimited JSON-RPC over stdio. stdout contains MCP only, stderr contains lifecycle diagnostics. Guest initialize, notifications, tools/list and tools/call pass through over `adb exec-out`; tools/list includes the dispenser tools below. Requests run serially so upload/exec cannot overlap guest MCP input. Client EOF or termination cancels pending startup or in-flight work and releases the VM for immediate deletion. The lease socket ties ownership to transport lifetime; dispenser death terminates the guest MCP connection.

Exactly one lease per instance is accepted. VMs are ephemeral: after 60 seconds without MCP activity, the dispenser powers off QEMU and deletes the writable overlay and all per-VM artifacts. Closing the MCP connection immediately triggers VM shutdown and deletion. Reconnecting after disconnect starts fresh from the immutable base. Export any files you need before the VM expires.

## Dispenser MCP tools (within the existing lease)

- `upload`: `{ "local_path": "/absolute/host/file", "guest_path": "/absolute/guest/file", "executable": true }`. `executable` defaults false. Parent directory must exist. Uses `adb push` followed by `chmod`. Returns `{sha256, bytes, guest_path}`. Upload timeout is 120 seconds plus transport allowance.
- `exec`: `{ "argv": ["/system/bin/echo", "hello"], "timeout_sec": 60 }`. Timeout is integer 1-300. Returns `{exit_code, stdout, stderr, truncated, timed_out}`. Streams are independently capped at 64KiB; nonzero exit is a tool error. Shell metacharacters in arguments are literal; request an explicit shell in argv if shell behavior is intended. Guest toybox `timeout` also bounds the command if ADB is lost.

The tools accept no instance ID. They inherit the guest user ADB connects as (root on a properly prepared base image). This is a trusted local agent interface, not a multi-user security boundary.

## Command-line operations

The `upload` CLI command creates its own temporary VM and lease. `start`, `stop`, and `restart` manage the dispenser itself:

```bash
./android-vm-dispenser upload --executable /absolute/example-file /data/local/tmp/example-file
./android-vm-dispenser start
./android-vm-dispenser stop
./android-vm-dispenser restart
```

`stop` refuses active leases and stops and deletes all remaining dispenser VMs. `restart` performs the same safe shutdown and starts a fresh daemon, preserving background mode.

## Storage, capacity and recovery

`config.json` specifies an immutable base image path. Keep credentials and state out of version control. There are no host mounts. Each overlay inherits the base's guest data and installed driver; do not put secrets into a reusable base.

Defaults allow four VMs, each with 2 vCPUs / 4096MiB, ADB over loopback TCP ports 25000-25003 (forwarded to guest adbd port 5555), separate QMP sockets, serial and QEMU logs, and writable overlays backed by the read-only base. Virtual disk size comes from the base image. State lives in `state/`, runtime sockets in a private UID/state-hash directory under `/tmp`. A thread lock serializes allocations; ownership checks verify the QEMU PID's command line before stopping or reusing it. On dispenser restart, orphaned owned VMs are stopped and removed, so every new allocation uses a fresh overlay.

New allocation requires 10GiB free space. A periodic low-space check closes leases below that reserve. Every removal deletes the overlay, instance logs, PID and record files, QMP socket, and viewer marker/lock. Cleanup after an abrupt dispenser crash runs when the dispenser next starts. Do not change the base, state directory, port range or capacity while instances exist.

Override config or state using absolute paths in `ANDROID_VM_DISPENSER_CONFIG` / `ANDROID_VM_DISPENSER_STATE`. The startup ADB readiness deadline defaults to 280 seconds. Dispenser logs: `state/dispenser.log`; instance logs and metadata: `state/instances/<hash>/`.

## Preparing a base

For a supplied **offline** Android x86_64 image, set the config base destination to a nonexistent path and run:

```bash
./android-vm-dispenser prepare-base /absolute/android-source.qcow2
```

It converts to a new standalone image, runs `qemu-img check`, and publishes it read-only without overwriting an existing base. Never chmod or mutate a base with dependent overlays.

## Validation


## Viewing a guest on the host desktop

VMs automatically open a host viewer unless the dispenser was started with `--background` or `auto_view` is false in config.json. MCP disconnect closes the owned viewer; reconnecting starts a fresh VM and viewer.

Viewers form a 2x2 grid in the upper half of the rightmost quarter of the desktop work area, with no margins or gaps between visible frames. Placement accounts for window-frame shadows. Guest auto-resizing is disabled. Placement checks only viewer windows owned by this project's dispenser state. This host layout requires `DISPLAY`, `xwininfo`, `xprop`, `libX11`, and `libXext`. Viewers are display-only: their input region is empty (clicks pass through), keyboard focus is disabled.

Start the dispenser with `./android-vm-dispenser start --background` to disable automatic viewers for its entire lifetime. `auto_view` defaults to `true` in config.json; set it to `false` for intentionally headless operation.

Newly launched guests have loopback-only VNC listeners using `vnc_port_start + slot`, default ports 5930-5933 (QEMU displays 30-33). Connections are local-user trusted, without a VNC password; never forward or expose the ports to an untrusted network. Viewer logs are in `state/viewer.log`.
