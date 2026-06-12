"""Own the app lifecycle for verification of server-rendered deliverables.

A single-port inline server (e.g. an app that renders HTML inside a running
process) can't be verified by trusting each worker to restart it: the first
instance to bind the port holds it, so later commits never serve and the
verifier screenshots stale code (and instances pile up). So the orchestrator
restarts the app itself right before capturing: free the port, start the serve
command fresh (detached, from the current working tree = the goal branch), wait
for the port to answer. Net effect: exactly one instance, serving the code under
test.
"""

from __future__ import annotations

import shlex
import socket
import subprocess
import sys
import time

_DETACHED = 0x00000008  # DETACHED_PROCESS
_NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP


def port_pids(port: int) -> set[int]:
    import psutil

    pids: set[int] = set()
    for conn in psutil.net_connections("tcp"):
        if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN" and conn.pid:
            pids.add(conn.pid)
    return pids


def free_port(port: int) -> list[int]:
    """Kill whatever is LISTENING on ``port``. Returns the PIDs killed."""
    import psutil

    killed: list[int] = []
    procs = []
    for pid in port_pids(port):
        try:
            p = psutil.Process(pid)
            p.kill()
            procs.append(p)
            killed.append(pid)
        except Exception:
            pass
    if procs:
        try:
            psutil.wait_procs(procs, timeout=8)
        except Exception:
            pass
    return killed


def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.5)
        finally:
            try:
                s.close()
            except OSError:
                pass
    return False


def start_detached(serve_cmd: str | list[str]) -> int:
    """Launch the serve command detached so it outlives this process."""
    args = serve_cmd if isinstance(serve_cmd, list) else shlex.split(serve_cmd, posix=False)
    flags = (_DETACHED | _NEW_GROUP) if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        args,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    return proc.pid


def restart_app(serve_cmd: str | list[str], port: int, *, wait_timeout: float = 30.0, settle: float = 1.0) -> dict:
    """Free the port, start the serve command fresh, wait for it. Returns a status dict."""
    if not serve_cmd or not port:
        return {"ok": False, "reason": "no serve_cmd or port", "killed": []}
    killed = free_port(int(port))
    time.sleep(0.5)
    try:
        pid = start_detached(serve_cmd)
    except Exception as exc:
        return {"ok": False, "reason": f"start failed: {type(exc).__name__}: {exc}", "killed": killed}
    up = wait_for_port(int(port), wait_timeout)
    if up:
        time.sleep(settle)
    return {"ok": up, "killed": killed, "pid": pid, "port": int(port)}
