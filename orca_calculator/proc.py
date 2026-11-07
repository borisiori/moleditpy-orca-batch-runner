"""Process-tree termination (ORCA spawns helper processes).

Linux is the fully implemented and tested path (/proc). Windows (taskkill)
and macOS (pgrep/pkill) are best-effort ports, not covered by the test suite.
"""

import logging
import os
import signal
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

GRACE_SECONDS = 5  # time between SIGTERM and SIGKILL

IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform.startswith("win")


# --------------------------------------------------------------- Linux (/proc)
def children_of(pid):
    """Direct child PIDs of pid, read from /proc."""
    children = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as f:
                # The comm field may contain spaces; ppid comes after it
                fields = f.read().rsplit(")", 1)[1].split()
            if int(fields[1]) == pid:
                children.append(int(entry))
        except (OSError, IndexError, ValueError):
            continue
    return children


def tree_of(pid):
    """Every PID in the tree (pid plus all descendants: orca, prterun...)."""
    seen, stack = set(), [pid]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children_of(current))
    return seen


def is_alive(pid):
    """True only if the process really lives (a zombie counts as dead)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def signal_tree(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


# ----------------------------------------------------------------- dispatcher
def terminate_tree_sync(pid, grace=GRACE_SECONDS):
    """Kill the whole process tree, best suited to the running platform."""
    if IS_WINDOWS:
        _windows_terminate_tree(pid)
    elif IS_LINUX:
        _linux_terminate_tree(pid, grace)
    else:
        _posix_tools_terminate_tree(pid, grace)


def _linux_terminate_tree(pid, grace):
    """SIGTERM the whole tree, then SIGKILL the survivors after the grace."""
    pids = tree_of(pid)
    signal_tree(pids, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        if not any(is_alive(p) for p in pids):
            return
        time.sleep(0.2)
    signal_tree([p for p in pids if is_alive(p)], signal.SIGKILL)


def _windows_terminate_tree(pid):
    """taskkill IS the Windows tree killer: /T walks children, /F forces."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except OSError as e:
        logger.warning("taskkill failed for %s: %s", pid, e)


def _posix_tools_terminate_tree(pid, grace):
    """macOS fallback: recursive child lookup through pgrep, TERM then KILL."""
    pids = {pid}
    stack = [pid]
    while stack:
        current = stack.pop()
        try:
            result = subprocess.run(
                ["pgrep", "-P", str(current)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            children = [int(x) for x in result.stdout.split()]
        except (OSError, ValueError):
            children = []
        for child in children:
            if child not in pids:
                pids.add(child)
                stack.append(child)

    for target in pids:
        try:
            os.kill(target, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if not any(_probe_alive(p) for p in pids):
            return
        time.sleep(0.2)
    for target in pids:
        try:
            os.kill(target, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _probe_alive(pid):
    """Zero-signal probe: works wherever os.kill does (no /proc needed)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but is not ours to signal
