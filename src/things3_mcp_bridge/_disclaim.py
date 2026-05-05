"""Spawn a child process that disclaims responsibility for its parent.

Background
----------
On macOS Tahoe (26+), TCC attributes permission requests to the *responsible
process*, which by default is inherited from the parent. For a LaunchAgent
bridge whose responsibility chain has been "poisoned" — e.g. originally
bootstrapped under Bun (OpenClaw) — every child the bridge spawns inherits
Bun as its responsible code. tccd then asks "does Bun have FDA / Automation?"
instead of "does the bridge have FDA / Automation?", and silently denies
or queues indefinitely.

The undocumented but widely-used `responsibility_spawnattrs_setdisclaim`
private API tells the kernel "spawn this child as its own responsible code,
not inheriting from me." Once the worker is its own RESP, TCC checks against
the worker's own code identity (the signed bridge bundle binary), and the
FDA / Automation grant we wired up in System Settings finally matches.

Reference: https://github.com/torarnv/disclaim ; steipete CLI guide.

This module provides one function, ``spawn_disclaimed``, with a shape similar
to ``subprocess.run`` but using ``posix_spawn`` directly via ctypes so we can
set the disclaim spawnattr.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass

_LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


# ---- posix_spawn types and prototypes --------------------------------------

# posix_spawnattr_t and posix_spawn_file_actions_t are opaque pointer-sized
# handles in the public headers. We store them as void pointers.
_SpawnAttr = ctypes.c_void_p
_FileActions = ctypes.c_void_p

_LIBC.posix_spawnattr_init.argtypes = [ctypes.POINTER(_SpawnAttr)]
_LIBC.posix_spawnattr_init.restype = ctypes.c_int

_LIBC.posix_spawnattr_destroy.argtypes = [ctypes.POINTER(_SpawnAttr)]
_LIBC.posix_spawnattr_destroy.restype = ctypes.c_int

_LIBC.posix_spawn_file_actions_init.argtypes = [ctypes.POINTER(_FileActions)]
_LIBC.posix_spawn_file_actions_init.restype = ctypes.c_int

_LIBC.posix_spawn_file_actions_destroy.argtypes = [ctypes.POINTER(_FileActions)]
_LIBC.posix_spawn_file_actions_destroy.restype = ctypes.c_int

_LIBC.posix_spawn_file_actions_adddup2.argtypes = [
    ctypes.POINTER(_FileActions),
    ctypes.c_int,
    ctypes.c_int,
]
_LIBC.posix_spawn_file_actions_adddup2.restype = ctypes.c_int

_LIBC.posix_spawn_file_actions_addclose.argtypes = [
    ctypes.POINTER(_FileActions),
    ctypes.c_int,
]
_LIBC.posix_spawn_file_actions_addclose.restype = ctypes.c_int

_LIBC.posix_spawn.argtypes = [
    ctypes.POINTER(ctypes.c_int),  # pid_t *
    ctypes.c_char_p,  # path
    ctypes.POINTER(_FileActions),  # file_actions
    ctypes.POINTER(_SpawnAttr),  # attrp
    ctypes.POINTER(ctypes.c_char_p),  # argv
    ctypes.POINTER(ctypes.c_char_p),  # envp
]
_LIBC.posix_spawn.restype = ctypes.c_int


# ---- responsibility_spawnattrs_setdisclaim ---------------------------------

# The symbol lives in libsystem (resolved at runtime, weak_import in Apple's
# headers). ctypes.util.find_library("c") loads libSystem which re-exports it.
#
# Calling convention: ``posix_spawnattr_t`` is an opaque pointer-sized handle.
# ``init``/``destroy`` take ``posix_spawnattr_t *`` (pointer to the variable
# holding the handle) so they can write or free it. ``setdisclaim`` takes
# ``posix_spawnattr_t`` directly (the handle value itself), matching the C
# signature ``int responsibility_spawnattrs_setdisclaim(posix_spawnattr_t, int)``.
# Getting this wrong silently no-ops the call — the kernel never sees disclaim.
try:
    _DISCLAIM_FN = _LIBC.responsibility_spawnattrs_setdisclaim
    _DISCLAIM_FN.argtypes = [_SpawnAttr, ctypes.c_int]
    _DISCLAIM_FN.restype = ctypes.c_int
    DISCLAIM_AVAILABLE = True
except AttributeError:
    DISCLAIM_AVAILABLE = False


# ---- Public API ------------------------------------------------------------


@dataclass
class SpawnResult:
    """Captured stdout + stderr + exit status of a disclaimed child."""

    returncode: int
    stdout: str
    stderr: str


class DisclaimError(RuntimeError):
    """Raised when posix_spawn or one of its helpers fails."""


def _check(rc: int, op: str) -> None:
    if rc != 0:
        raise DisclaimError(f"{op} failed: errno={rc} ({os.strerror(rc)})")


def spawn_disclaimed(
    argv: list[str],
    *,
    capture_stdout: bool = True,
    capture_stderr: bool = False,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> SpawnResult:
    """Spawn ``argv[0]`` with the disclaim spawnattr set.

    The child process is its own responsible code as far as TCC is concerned,
    rather than inheriting whatever poisoned identity our parent has.

    ``capture_stdout=True`` captures the child's stdout into the result;
    stderr is by default left inheriting (so it lands in the bridge's
    ``bridge.err.log`` for debugging). Set ``capture_stderr=True`` to capture
    it too.

    ``timeout`` works like ``subprocess.run``: if the child hasn't exited
    after ``timeout`` seconds, it is SIGKILLed and ``DisclaimError`` is raised.
    """
    import threading

    if not DISCLAIM_AVAILABLE:
        raise DisclaimError("responsibility_spawnattrs_setdisclaim not available on this OS")
    if not argv:
        raise ValueError("argv must not be empty")

    # Encode argv and envp as null-terminated arrays of C strings.
    argv_bytes = [a.encode() for a in argv]
    argv_array = (ctypes.c_char_p * (len(argv_bytes) + 1))(*argv_bytes, None)

    if env is None:
        env_pairs = [f"{k}={v}".encode() for k, v in os.environ.items()]
    else:
        env_pairs = [f"{k}={v}".encode() for k, v in env.items()]
    env_array = (ctypes.c_char_p * (len(env_pairs) + 1))(*env_pairs, None)

    attr = _SpawnAttr()
    actions = _FileActions()
    _check(_LIBC.posix_spawnattr_init(ctypes.byref(attr)), "posix_spawnattr_init")
    if attr.value is None:
        raise DisclaimError("posix_spawnattr_init returned 0 but the handle is still NULL")
    try:
        _check(_LIBC.posix_spawn_file_actions_init(ctypes.byref(actions)), "posix_spawn_file_actions_init")
        try:
            # Pass the pointer value explicitly to avoid ctypes auto-wrapping the
            # c_void_p instance (which on some platforms hands a pointer-to-pointer
            # to the C function and yields EINVAL).
            _check(_DISCLAIM_FN(attr.value, 1), "responsibility_spawnattrs_setdisclaim")

            stdout_r = stdout_w = stderr_r = stderr_w = -1
            if capture_stdout:
                stdout_r, stdout_w = os.pipe()
                _check(
                    _LIBC.posix_spawn_file_actions_adddup2(ctypes.byref(actions), stdout_w, 1),
                    "adddup2 stdout",
                )
                _check(
                    _LIBC.posix_spawn_file_actions_addclose(ctypes.byref(actions), stdout_r),
                    "addclose stdout_r",
                )
            if capture_stderr:
                stderr_r, stderr_w = os.pipe()
                _check(
                    _LIBC.posix_spawn_file_actions_adddup2(ctypes.byref(actions), stderr_w, 2),
                    "adddup2 stderr",
                )
                _check(
                    _LIBC.posix_spawn_file_actions_addclose(ctypes.byref(actions), stderr_r),
                    "addclose stderr_r",
                )

            pid = ctypes.c_int(0)
            rc = _LIBC.posix_spawn(
                ctypes.byref(pid),
                argv_bytes[0],
                ctypes.byref(actions),
                ctypes.byref(attr),
                argv_array,
                env_array,
            )
            # Always close our copy of the write ends so EOF is signaled when child exits.
            if stdout_w != -1:
                os.close(stdout_w)
            if stderr_w != -1:
                os.close(stderr_w)
            if rc != 0:
                if stdout_r != -1:
                    os.close(stdout_r)
                if stderr_r != -1:
                    os.close(stderr_r)
                raise DisclaimError(f"posix_spawn failed: errno={rc} ({os.strerror(rc)})")

            # Drain pipes in background threads so we don't deadlock against a
            # child that hangs without producing output (e.g. tccd queueing a
            # protected-file open). The threads exit when the child closes its
            # end of the pipe — which always happens once the child exits or
            # we SIGKILL it.
            stdout_buf: list[str] = [""]
            stderr_buf: list[str] = [""]

            def _drain_into(fd: int, buf: list[str]) -> None:
                buf[0] = _drain(fd)

            stdout_thread: threading.Thread | None = None
            stderr_thread: threading.Thread | None = None
            if stdout_r != -1:
                stdout_thread = threading.Thread(target=_drain_into, args=(stdout_r, stdout_buf), daemon=True)
                stdout_thread.start()
            if stderr_r != -1:
                stderr_thread = threading.Thread(target=_drain_into, args=(stderr_r, stderr_buf), daemon=True)
                stderr_thread.start()

            wait_pid, status = _waitpid_with_timeout(pid.value, timeout)
            if wait_pid == 0:
                # Timed out — escalate to SIGKILL and reap so the drain threads see EOF.
                try:
                    os.kill(pid.value, 9)
                except ProcessLookupError:
                    pass
                os.waitpid(pid.value, 0)
                if stdout_thread is not None:
                    stdout_thread.join(timeout=2.0)
                if stderr_thread is not None:
                    stderr_thread.join(timeout=2.0)
                raise DisclaimError(f"child {argv[0]} did not exit within {timeout}s")

            if stdout_thread is not None:
                stdout_thread.join(timeout=5.0)
            if stderr_thread is not None:
                stderr_thread.join(timeout=5.0)

            if os.WIFEXITED(status):
                returncode = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                returncode = -os.WTERMSIG(status)
            else:
                returncode = -1

            return SpawnResult(returncode=returncode, stdout=stdout_buf[0], stderr=stderr_buf[0])
        finally:
            _LIBC.posix_spawn_file_actions_destroy(ctypes.byref(actions))
    finally:
        _LIBC.posix_spawnattr_destroy(ctypes.byref(attr))


def _drain(fd: int) -> str:
    """Read everything from ``fd`` until EOF, then close it."""
    chunks: list[bytes] = []
    try:
        while True:
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
    finally:
        os.close(fd)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _waitpid_with_timeout(pid: int, timeout: float | None) -> tuple[int, int]:
    """Wait up to ``timeout`` seconds for ``pid``. Returns (waited_pid, status).

    If timeout elapses, returns (0, 0) without reaping the child.
    """
    if timeout is None:
        return os.waitpid(pid, 0)

    # Poll using a short sleep loop; macOS doesn't have pidfd_open like Linux.
    import time

    deadline = time.monotonic() + timeout
    while True:
        try:
            wait_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return pid, 0
        if wait_pid != 0:
            return wait_pid, status
        if time.monotonic() >= deadline:
            return 0, 0
        time.sleep(0.05)


__all__ = ["spawn_disclaimed", "SpawnResult", "DisclaimError", "DISCLAIM_AVAILABLE"]
