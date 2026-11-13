"""Process-scoped leases for one pinned, configurable ordinary session pool.

Modified 2026-11-13: serialize bounded pool admission and preserve live generations.
"""

from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
import time
from pathlib import Path
import uuid

DEFAULT_SESSIONS = 2
MAXIMUM_SESSIONS = 8  # Local safety ceiling, not provider/account entitlement.
ADMISSION_WAIT_SECONDS = .05  # Separate bounded admission, not a task execution deadline.


def identifier(value):
    if str(uuid.UUID(value)) != value:
        raise ValueError("Expected a canonical UUID")
    return value


def lease(path, shared=False, *, wait=False):
    """Acquire immediately, or retry nonblocking admission for at most 50 ms."""
    path = Path(path)
    if path.parent.is_symlink():
        raise ValueError("Lease directory must not be a symlink")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    stream = os.fdopen(fd, "a")
    try:
        deadline = time.monotonic() + (ADMISSION_WAIT_SECONDS if wait else 0)
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                return stream
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if not wait or remaining <= 0:
                    raise RuntimeError("Resource is leased by another worker; bounded admission refused") from None
                time.sleep(min(.002, remaining))
    except BaseException:
        stream.close()
        raise


def _record(stream):
    raw = os.pread(stream.fileno(), 4097, 0)
    if not raw:
        return None
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate pool policy field")
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique)
        if (len(raw) > 4096 or not isinstance(value, dict)
                or set(value) != {"schema_version", "generation", "maximum_sessions"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["maximum_sessions"]) is not int
                or not 1 <= value["maximum_sessions"] <= MAXIMUM_SESSIONS
                or not isinstance(value["generation"], str) or len(value["generation"]) != 64
                or any(c not in '0123456789abcdef' for c in value["generation"])):
            raise ValueError("Invalid pool policy record")
        return value
    except (ValueError, TypeError, UnicodeError):
        raise RuntimeError("Session pool generation record is invalid; no automatic repair") from None


def pool_generation(root, settings, *, daemon_owned=False, worker=False):
    """Pin policy until every participating daemon/worker explicitly releases it.

    Only Daemon may set daemon_owned, after taking daemon.lock exclusively.
    Standalone users retain shared daemon admission. Workers may join an active
    new daemon marker, never an idle legacy daemon with no pool protocol.
    """
    root = Path(root)
    with ExitStack() as stack, lease(root / "leases" / "admission.lock", wait=True):
        can_rebind = daemon_owned
        if daemon_owned:
            stack.enter_context(lease(root / "leases" / "pool-daemon.lock"))
        else:
            try:
                admission = lease(root / "daemon.lock", shared=True)
            except RuntimeError:
                try:
                    unmarked = lease(root / "leases" / "pool-daemon.lock", shared=True)
                except RuntimeError:
                    pass  # A current daemon holds the marker exclusively.
                else:
                    unmarked.close()
                    raise RuntimeError("Legacy or unconfirmed daemon owns admission; pool adoption blocked") from None
            else:
                stack.enter_context(admission)
                if worker:
                    raise RuntimeError("Dispatch daemon ownership is absent; worker pool adoption blocked")
                can_rebind = True
        stack.enter_context(_pin_generation(root, settings, can_rebind=can_rebind))
        return stack.pop_all()


def _pin_generation(root, settings, *, can_rebind):
    expected = {"schema_version": 1, "generation": settings["pool_generation"],
                "maximum_sessions": settings["maximum_sessions"]}
    try:
        held = lease(root / "leases" / "pool-generation.lock")
    except RuntimeError:
        held = lease(root / "leases" / "pool-generation.lock", shared=True)
        try:
            if _record(held) != expected:
                raise RuntimeError("Session pool generation remains held; policy change requires idle adoption")
            return held
        except BaseException:
            held.close()
            raise
    try:
        if not can_rebind:
            raise RuntimeError("Current daemon pool ownership is unconfirmed; no worker may rebind it")
        previous = _record(held)
        with ExitStack() as quiet:
            quiet.enter_context(lease(root / "runner.lock"))
            for number in range(MAXIMUM_SESSIONS):
                quiet.enter_context(lease(root / "leases" / f"slot-{number}.lock"))
            if previous != expected:
                encoded = (json.dumps(expected, sort_keys=True) + "\n").encode('ascii')
                os.ftruncate(held.fileno(), 0)
                os.lseek(held.fileno(), 0, os.SEEK_SET)
                os.write(held.fileno(), encoded)
                os.fsync(held.fileno())
            try:
                fcntl.flock(held.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Session pool generation conversion is leased; adoption refused") from None
        return held
    except BaseException:
        held.close()
        raise


def _session_slot(root, maximum):
    """Count every physical slot and retain exactly one under serialized admission."""
    free = []
    try:
        with lease(root / "leases" / "admission.lock", wait=True):
            occupied = 0
            for number in range(MAXIMUM_SESSIONS):
                try:
                    held = lease(root / "leases" / f"slot-{number}.lock")
                except RuntimeError:
                    occupied += 1
                else:
                    free.append((number, held))
            eligible = [held for number, held in free if number < maximum]
            if occupied >= maximum or not eligible:
                raise RuntimeError("Session pool capacity is held; task remains queued")
            selected = eligible[0]
            for _, held in free:
                if held is not selected:
                    held.close()
            free = []
            return selected
    finally:
        for _, held in free:
            held.close()


@contextmanager
def guard(root, task, *, slot=True, settings=None):
    root = Path(root)
    with ExitStack() as stack:
        if slot:
            import scheduler
            dispatched = any(name in os.environ for name in
                             ('LAF_SESSION_POOL_GENERATION', 'LAF_SESSION_POOL_CODEX_PIPELINE'))
            if settings is None:
                settings = scheduler.worker_configuration() if dispatched else scheduler.configuration()
            else:
                scheduler.require_pool_generation(settings)
                if dispatched and scheduler.worker_configuration()['pool_generation'] != settings['pool_generation']:
                    raise RuntimeError("Worker session pool generation does not match pinned daemon policy")
            stack.enter_context(pool_generation(root, settings, worker=dispatched))
        stack.enter_context(lease(root / "runner.lock", shared=True))
        from codex_host import pipeline_held
        if pipeline_held(root): raise RuntimeError('Pipeline execution or cleanup remains unresolved')
        stack.enter_context(lease(root / "leases" / (identifier(task["id"]) + ".lock")))
        stack.enter_context(lease(root / "leases" / ("workspace-" + identifier(task["workspace_id"]) + ".lock")))
        if slot:
            stack.enter_context(_session_slot(root, settings['maximum_sessions']))
        yield


def recover_abandoned(root, store):
    """Never infer death from another worker's live RUNNING database row."""
    recovered = []
    rows = store.db.execute("SELECT * FROM tasks WHERE state IN ('RUNNING','VERIFYING')").fetchall()
    for row in rows:
        task = dict(row)
        try:
            # Shared admission proves there is no legacy exclusive owner,
            # while allowing recovery alongside unrelated isolated workers.
            held = guard(root, task, slot=False) if task.get("workspace_id") else lease(Path(root) / "runner.lock", shared=True)
            with held:
                recovered.extend(store.recover(task["id"]))
        except RuntimeError:
            continue
    return recovered
