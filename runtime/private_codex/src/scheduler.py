"""One persistent FIFO dispatcher; task execution remains in the existing runner.

Modified 2026-11-13: pin validated ordinary-pool policy and sample memory admission.
Modified 2026-11-15: fixed, redacted task wait codes for queue observability.
Modified 2026-11-15: immutable bootstrap resource-policy selection.
"""

from contextlib import ExitStack, closing, contextmanager
import copy
import json
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

from concurrency import lease, recover_abandoned, pool_generation, DEFAULT_SESSIONS, MAXIMUM_SESSIONS
from resources import policy, scope_released, ResourceExceeded
from resource_admission_core import scope_stat, AdmissionWait
from storage import _backup, runtime_lock
from transport import proc_identity

CONFIG = Path(__file__).resolve().parents[1] / "scheduler-config.json"
MEMINFO = Path('/proc/meminfo')

# Diagnostics only; none of these codes authorizes or releases an execution.
TASK_WAIT_REASONS = frozenset({
    'queue_pending', 'workspace_busy', 'unknown_workspace',
    'exclusive_task_wait', 'exclusive_queue_barrier', 'exclusive_worker_active',
    'session_pool_capacity', 'worker_pending', 'dispatch_record_retained',
    'scheduler_paused', 'scheduler_stopping', 'recovery_pending',
    'unreleased_unknown', 'unreconciled_unknown', 'storage_budget',
    'pipeline_operation_unknown', 'pipeline_held', 'pipeline_recovery_pending',
    'session_pool_policy_changed', 'session_pool_memory_headroom',
    'external_active_owner', 'sqlite_locked',
    'resource_state_identity_unknown', 'resource_legacy_state_pending', 'resource_owner_unknown',
    'resource_provider_account_unknown', 'resource_provider_workload_unknown',
    'resource_controller_required',
    'resource_container_unknown', 'resource_container_cpu_millicores_headroom', 'resource_container_ram_bytes_headroom',
    'resource_generation_held', 'resource_reservation_retained', 'resource_reservation_missing',
    'resource_scope_unknown', 'resource_observation_unknown', 'resource_observation_stale',
    'resource_usage_unknown', 'resource_gpu_topology_unknown',
    'resource_provider_quota', 'resource_provider_quota_unknown',
    *('resource_' + dimension + '_' + reason
      for dimension in ('cpu_millicores', 'ram_bytes', 'gpu_milliunits', 'vram_bytes')
      for reason in ('unknown', 'budget', 'headroom')),
})


class PoolPolicyError(RuntimeError):
    pass


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate scheduler configuration field")
        value[key] = item
    return value


def _selector_stat(path):
    # Only known absence permits legacy discovery; Python3.14 exists() hides errors.
    try: return path.lstat()
    except FileNotFoundError: return None


def configuration(*, codex_pipeline=False):
    """Select immutable bootstrap policy without changing packaged source."""
    from backend_state import FILE, load_state, scheduler_binding_policy, state_sha256
    project = CONFIG.parent
    runtime = project / '.runtime/main'
    marker = runtime / FILE
    if _selector_stat(marker) is not None:
        state, raw = load_state(runtime)
        if state is None:
            raise ValueError('Bootstrap scheduler binding disappeared')
        if state.get('scheduler') is not None:
            value = validate_configuration(scheduler_binding_policy(state['scheduler']),
                                           codex_pipeline=codex_pipeline)
            value.pop('pool_generation')
            value['bootstrap_bindings_sha256'] = state_sha256(raw)
            value['pool_generation'] = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
            return value
    else:
        selection = project / 'config/policy.json'
        selected_info = _selector_stat(selection)
        if selected_info is not None:
            if not stat.S_ISREG(selected_info.st_mode): raise ValueError('Unsafe scheduler policy selection')
            selected = json.loads(selection.read_text(), object_pairs_hook=_unique_object)
            if isinstance(selected, dict) and selected.get('source') == 'bundled_snapshot':
                raise ValueError('Bootstrap scheduler binding is missing')
    if CONFIG.is_symlink():
        raise ValueError("Scheduler configuration must not be a symlink")
    value = json.loads(CONFIG.read_text(), object_pairs_hook=_unique_object)
    return validate_configuration(value, codex_pipeline=codex_pipeline)


def validate_configuration(value, *, codex_pipeline=False):
    """Validate a decoded operator snapshot; never open resource state or a peer."""
    value = copy.deepcopy(value)
    if type(codex_pipeline) is not bool:
        raise ValueError("Pipeline budget selection must be boolean")
    if not isinstance(value, dict):
        raise ValueError("Scheduler configuration must be an object")
    required = {"poll_seconds", "task_timeout", "shutdown_seconds", "daemon", "worker"}
    optional = {"resource_admission", "maximum_sessions", "pipeline_daemon_memory_bytes", "pipeline_daemon_pids", "pipeline_daemon_file_bytes"}
    if not required <= set(value) or set(value) - required - optional:
        raise ValueError("Unknown scheduler configuration")
    maximum_explicit = "maximum_sessions" in value
    maximum_sessions = value.get("maximum_sessions", DEFAULT_SESSIONS)
    if type(maximum_sessions) is not int or not 1 <= maximum_sessions <= MAXIMUM_SESSIONS:
        raise ValueError("maximum_sessions must be an integer between 1 and 8")
    value["maximum_sessions"] = maximum_sessions
    if 'resource_admission' in value:
        from resource_admission import policy as admission_policy
        value['resource_admission'] = admission_policy(value['resource_admission'])
        from resource_admission import resource_only as selected_mode
        if selected_mode(value['resource_admission']):
            if maximum_explicit: raise ValueError('Resource-only mode cannot declare maximum_sessions')
            value['maximum_sessions'] = None
            if codex_pipeline and 'pipeline' not in value['resource_admission']:
                raise ValueError('Resource-only pipeline profile is not configured')
    runner_policy = policy()
    for name, maximum in [("poll_seconds", 5), ("task_timeout", 3600), ("shutdown_seconds", 30)]:
        v = value[name]
        if type(v) not in [float, int] or not 0 < v <= maximum or not math.isfinite(v):
            raise ValueError("Invalid scheduler timing")
    for role in ["daemon", "worker"]:
        if not isinstance(value[role], dict) or set(value[role]) != {"cpu_percent", "cpu_budget_ms", "memory_bytes", "pids"}:
            raise ValueError("Invalid controller resource policy")
        value[role] = policy({**runner_policy, **value[role]})
    pipeline_memory = value.get("pipeline_daemon_memory_bytes")
    if "pipeline_daemon_memory_bytes" in value:
        if type(pipeline_memory) is not int or pipeline_memory < value["daemon"]["memory_bytes"]:
            raise ValueError("Pipeline memory must be a finite integer at least the base daemon limit")
        policy({**value["daemon"], "memory_bytes": pipeline_memory})
    pipeline_pids = value.get("pipeline_daemon_pids")
    if "pipeline_daemon_pids" in value:
        if type(pipeline_pids) is not int or not value["daemon"]["pids"] <= pipeline_pids <= 3072:
            raise ValueError("Pipeline task budget must be a finite integer between the base limit and 3072")
    pipeline_file = value.get("pipeline_daemon_file_bytes", value["daemon"]["file_bytes"])
    if type(pipeline_file) is not int or not value["daemon"]["file_bytes"] <= pipeline_file <= 256*1024**2:
        raise ValueError("Pipeline file ceiling must be finite between the base limit and256MiB")
    value["runner_resource_policy"] = runner_policy
    if resource_only(value):
        reserve = value['resource_admission']['reserve']
        if (reserve['cpu_millicores'] < runner_policy['cpu_percent'] * 10
                or reserve['ram_bytes'] < runner_policy['memory_bytes']):
            raise ValueError('Resource-only recovery reserve must cover one bounded reconciliation process')
    if codex_pipeline:
        if pipeline_memory is None or pipeline_pids is None:
            raise ValueError("Pipeline memory and task budgets must be explicitly configured")
        value["daemon"]["memory_bytes"] = pipeline_memory
        value["daemon"]["pids"] = pipeline_pids
        value["daemon"]["file_bytes"] = pipeline_file
    value["codex_pipeline"] = codex_pipeline
    value["pool_generation"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
    return value


def require_pool_generation(settings, *, codex_pipeline=None):
    """A config edit never silently rebinds a live daemon or its queued children."""
    try:
        mode = settings['codex_pipeline']
        if type(mode) is not bool or (codex_pipeline is not None and codex_pipeline is not mode):
            raise ValueError("Pinned session pool mode is invalid or changed")
        current = configuration(codex_pipeline=mode)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise PoolPolicyError("Session pool policy is unreadable or invalid; new dispatch blocked") from exc
    if current['pool_generation'] != settings['pool_generation']:
        raise PoolPolicyError("Session pool policy changed; idle adoption is required")
    return current


def worker_configuration():
    """A dispatched worker needs explicit matching mode and generation, never defaults."""
    mode = os.environ.get('LAF_SESSION_POOL_CODEX_PIPELINE')
    if mode not in ('0', '1'):
        raise PoolPolicyError("Worker effective daemon budget is missing or invalid")
    current = configuration(codex_pipeline=mode == '1')
    if os.environ.get('LAF_SESSION_POOL_GENERATION') != current['pool_generation']:
        raise PoolPolicyError("Worker effective session pool generation is missing or changed")
    return current


def resource_only(settings):
    from resource_admission import resource_only as selected
    return selected(settings.get('resource_admission'))


def session_memory_envelope(settings):
    return (settings['daemon']['memory_bytes'] + settings['maximum_sessions'] *
            (settings['runner_resource_policy']['memory_bytes'] + settings['worker']['memory_bytes']))


def require_pool_headroom(settings):
    """Sample a conservative full-pool envelope, not a reservation or quota proof.

    Already-running workers may be counted again. Blocking early is preferable
    to enlarging their cgroups or interrupting an existing worker.
    """
    if resource_only(settings): return None  # Mandatory atomic request admission replaces this envelope.
    try:
        rows = [line.split() for line in MEMINFO.read_text().splitlines()
                if line.split() and line.split()[0] == 'MemAvailable:']
        if (len(rows) != 1 or len(rows[0]) != 3 or rows[0][2] != 'kB'
                or not rows[0][1].isascii() or not rows[0][1].isdigit()):
            raise ValueError('Invalid MemAvailable')
        available = int(rows[0][1]) * 1024
    except (OSError, ValueError, UnicodeError) as exc:
        raise ResourceExceeded("Session pool memory headroom is unknown; new dispatch blocked") from exc
    if available < session_memory_envelope(settings):
        raise ResourceExceeded("Session pool full-envelope memory headroom is insufficient; new dispatch blocked")
    return available


def reserve_task_resources(root, task_id, settings, token, *, worker, scopes=(), owner=None):
    if 'resource_admission' not in settings: return False
    from resource_admission import Ledger, ordinary_request, linux_observation
    with closing(Ledger(settings['resource_admission'])) as ledger:
        ledger.reserve(root, task_id, token, ordinary_request(settings, worker=worker),
                       linux_observation, scopes=scopes, owner=owner)
    return True


def register_resource_owner(root, settings, role):
    if not resource_only(settings): return None
    from resource_admission import Ledger
    with closing(Ledger(settings['resource_admission'])) as ledger:
        return ledger.register_owner(root, role)


def close_resource_owner(settings, owner):
    if owner is None: return
    from resource_admission import Ledger
    with closing(Ledger(settings['resource_admission'])) as ledger: ledger.close_owner(owner)


def verify_resource_account(settings, account):
    if not resource_only(settings): return
    from resource_admission import AdmissionWait, account_fingerprint
    quota = settings['resource_admission']['providers'].get('chatgpt')
    if (not quota or quota['valid_until'] <= time.time()
            or account_fingerprint(account) != quota['account_sha256']):
        raise AdmissionWait('resource_provider_account_unknown')


def authorize_task_provider(root, task_id, settings, token, account, *, observed_at=None):
    if not resource_only(settings): return
    from resource_admission import Ledger, account_fingerprint, linux_observation
    verify_resource_account(settings, account)
    with closing(Ledger(settings['resource_admission'])) as ledger:
        ledger.recheck(root, task_id, token, linux_observation)
        ledger.authorize_provider(root, task_id, token, 'chatgpt', account_fingerprint(account))
    if observed_at is not None and not 0 <= time.monotonic() - observed_at <= settings['resource_admission']['max_age_seconds']:
        from resource_admission import AdmissionWait
        raise AdmissionWait('resource_provider_account_unknown')


def require_charged_controller(root, settings):
    """An unbounded standalone coordinator cannot borrow a child-only grant."""
    if not resource_only(settings): return
    from resource_admission import Ledger, AdmissionWait, owner_identity, _scope_credit
    from daemon import verify_self_limits
    try:
        verify_self_limits(allow_codex_pipeline=settings.get('codex_pipeline', False))
        descriptor = controller_descriptor()
        identity = owner_identity()
        with closing(Ledger(settings['resource_admission'])) as ledger:
            for row in ledger.held():
                if row['runtime'] != str(Path(root).absolute()): continue
                demand = json.loads(row['demand'])
                if demand['kind'] != 'local_control' or demand['provider'] is not None: continue
                if not any(_scope_credit(item) == _scope_credit(descriptor) for item in json.loads(row['scopes'])): continue
                binding = ledger.db.execute('SELECT owner FROM reservation_owners WHERE token=?', (row['token'],)).fetchone()
                if binding and json.loads(ledger._owner(binding[0], root)['identity']) == identity:
                    limits = settings['daemon']
                    if (demand['resources']['cpu_millicores'] >= limits['cpu_percent'] * 10
                            and demand['resources']['ram_bytes'] >= limits['memory_bytes']):
                        return
    except (OSError, ValueError, RuntimeError, KeyError):
        pass
    raise AdmissionWait('resource_controller_required')


def reconcile_stopped_controllers(root, settings):
    """Known controller exit only; provider/task uncertainty stays charged."""
    if not resource_only(settings): return
    from resource_admission import Ledger, owner_live
    with closing(Ledger(settings['resource_admission'])) as ledger:
        owners = list(ledger.db.execute("SELECT id,identity FROM admission_owners WHERE runtime=? AND role IN ('daemon','standalone_controller') AND phase='HELD'",
                                       (str(Path(root).absolute()),)))
        for owner in owners:
            if owner_live(json.loads(owner['identity'])) is False:
                ledger.reconcile_owner(owner['id'], apply=True)


def controller_descriptor():
    relative = Path('/proc/self/cgroup').read_text().strip().split('::')[1]
    scope = Path('/sys/fs/cgroup') / relative.lstrip('/')
    return {'unit': scope.name, 'expected_cgroup': str(scope)}


def ensure_controller_resources(daemon):
    if not resource_only(daemon.settings): return
    from resource_admission import Ledger, linux_observation
    if getattr(daemon, 'resource_owner', None) is None:
        daemon.resource_owner = register_resource_owner(daemon.root, daemon.settings, 'daemon')
    task = str(uuid.uuid5(uuid.NAMESPACE_URL, str(daemon.root) + ':daemon:' + daemon.boot_id))
    if getattr(daemon, 'resource_controller_token', None):
        with closing(Ledger(daemon.settings['resource_admission'])) as ledger:
            ledger.join(daemon.root, task, daemon.resource_controller_token)
        return
    limits = daemon.settings['daemon']
    demand = {'kind': 'local_control', 'provider': None, 'resources':
              {'cpu_millicores': limits['cpu_percent'] * 10, 'ram_bytes': limits['memory_bytes'],
               'gpu_milliunits': 0, 'vram_bytes': 0}}
    token = str(uuid.uuid4())
    with closing(Ledger(daemon.settings['resource_admission'])) as ledger:
        ledger.reserve(daemon.root, task, token, demand, linux_observation,
                       scopes=[controller_descriptor()], owner=daemon.resource_owner)
    daemon.resource_controller_token = token


def join_task_resources(root, task_id, settings, token):
    if 'resource_admission' not in settings: return
    from resource_admission import Ledger, linux_observation
    with closing(Ledger(settings['resource_admission'])) as ledger:
        ledger.join(root, task_id, token)
        if resource_only(settings): ledger.recheck(root, task_id, token, linux_observation)


def bind_task_resource_scope(root, task_id, settings, token, descriptor):
    if 'resource_admission' not in settings: return
    from resource_admission import Ledger
    with closing(Ledger(settings['resource_admission'])) as ledger:
        ledger.add_scope(root, task_id, token, descriptor)


def finish_task_resources(root, store, task_id, settings, *, token=None,
                          worker_released=False, preclaim=False):
    """Only positive local cleanup AND provider completion release admission.

    UNKNOWN and missing execution/terminal evidence retain the entire grant.
    This never turns a retained task into a replayable queue entry.
    """
    if 'resource_admission' not in settings: return False
    from resource_admission import Ledger
    from recovery import resource_facts
    with closing(Ledger(settings['resource_admission'])) as ledger:
        rows = [r for r in ledger.held() if r['runtime'] == str(Path(root).absolute()) and r['task'] == task_id]
        if not rows: return False
        row = rows[0]
        if token is not None and row['token'] != token: return False
        task = store.get(task_id)
        if task['state'] in ('UNKNOWN', 'RUNNING', 'VERIFYING'): return False
        execution = store.execution(task['active_execution_id']) if task['active_execution_id'] else None
        if execution is None:
            if not (worker_released or (preclaim and not json.loads(row['scopes']))): return False
            local_clear = provider_clear = True  # No execution claim could start a turn.
        else:
            local_clear = resource_facts(execution, task)['released']
            terminal = json.loads(execution.get('terminal') or 'null')
            provider_clear = (not execution['dispatched'] or
                              isinstance(terminal, dict) and terminal.get('status') in ('completed', 'failed', 'interrupted'))
            if not preclaim: local_clear = local_clear and worker_released is True
        return ledger.release(root, task_id, row['token'], local_released=local_clear,
                              provider_released=provider_clear)


def reconcile_task_resources(runner, task_id, reconciliation_id, *, apply=False):
    """Paused same-UID operator path: release a grant, never replay or change task state."""
    settings = runner.scheduler_settings
    if 'resource_admission' not in settings: raise ValueError('Resource admission is not enabled')
    task = runner.store.get(task_id)
    if task['state'] in ('QUEUED', 'RUNNING', 'VERIFYING'): raise ValueError('Task remains active')
    row = runner.store.db.execute('SELECT * FROM reconciliations WHERE id=? AND task_id=?',
                                  (reconciliation_id, task_id)).fetchone()
    if not row or row['execution_id'] != task['active_execution_id'] or not 0 <= time.time() - row['created'] <= 60:
        raise ValueError('Fresh reconciliation for the exact execution required')
    facts = json.loads(row['facts']); session = facts.get('session', {})
    execution = runner.store.execution(task['active_execution_id']) if task['active_execution_id'] else None
    from recovery import resource_facts
    if (execution is None or resource_facts(execution, task)['released'] is not True
            or resource_only(settings) and session.get('resource_provider_account_verified') is not True
            or session.get('exists') is not True or session.get('ended') is not True
            or session.get('latest_turn_matches') is not True
            or session.get('thread_id') != execution['thread_id']
            or session.get('turn_id') != execution['turn_id']):
        raise ValueError('Exact provider terminal and current local release proof required')
    dispatch = runner.store.db.execute('SELECT resources,owner FROM scheduler_dispatches WHERE task_id=?', (task_id,)).fetchone()
    if dispatch and not worker_scope_clear(json.loads(dispatch['resources']), json.loads(dispatch['owner'])):
        raise ValueError('Worker scope release unconfirmed')
    from resource_admission import Ledger
    with closing(Ledger(settings['resource_admission'])) as ledger:
        grants = [r for r in ledger.held() if r['runtime'] == str(Path(runner.root).absolute()) and r['task'] == task_id]
        if len(grants) != 1: raise ValueError('Exact held reservation required')
        if apply:
            ledger.reconcile_release(runner.root, task_id, grants[0]['token'], reconciliation_id,
                {'execution_id': task['active_execution_id'], 'provider_ended': True,
                 'local_released': True, 'source_reconciliation': reconciliation_id})
    return {'task_id': task_id, 'status': 'RELEASED' if apply else 'READY', 'task_state': task['state'], 'automatic_retry': False}


@contextmanager
def resource_probe(runner, task_id):
    """Bounded read-only recovery uses reserved CPU/RAM, never an inference lease."""
    settings = getattr(runner, 'scheduler_settings', None) or configuration()
    if 'resource_admission' not in settings:
        yield
        return
    from resource_admission import Ledger, linux_observation
    require_charged_controller(runner.root, settings)
    owner = register_resource_owner(runner.root, settings, 'standalone')
    probe, token = str(uuid.uuid4()), str(uuid.uuid4())
    previous = (getattr(runner, 'resource_token', None), getattr(runner, 'resource_task_id', None),
                getattr(runner, 'resource_grant_task_id', None))
    granted = False
    try:
        limits = settings['runner_resource_policy']
        demand = {'kind': 'reconciliation', 'provider': None, 'resources':
                  {'cpu_millicores': limits['cpu_percent'] * 10, 'ram_bytes': limits['memory_bytes'],
                   'gpu_milliunits': 0, 'vram_bytes': 0}}
        with closing(Ledger(settings['resource_admission'])) as ledger:
            ledger.reserve(runner.root, probe, token, demand, linux_observation, owner=owner)
        granted = True
        runner.scheduler_settings = settings
        runner.resource_token, runner.resource_task_id, runner.resource_grant_task_id = token, task_id, probe
        yield
    finally:
        try:
            if granted:
                from resources import scope_released
                with closing(Ledger(settings['resource_admission'])) as ledger:
                    row = ledger.join(runner.root, probe, token)
                    scopes = json.loads(row['scopes'])
                    if not scopes or all(scope_released(descriptor) for descriptor in scopes):
                        ledger.release(runner.root, probe, token, local_released=True, provider_released=True)
        finally:
            runner.resource_token, runner.resource_task_id, runner.resource_grant_task_id = previous
            close_resource_owner(settings, owner)


def initialize(store):
    db = store.db
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='scheduler_state'").fetchone()
    if exists:
        if db.execute("SELECT schema_version FROM scheduler_state WHERE id=1").fetchone()[0] != 1:
            raise ValueError("Unsupported scheduler schema")
        return
    with runtime_lock(store.path.parent):
        _backup(db, store.path, "before-scheduler-v1")
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE scheduler_state (
                id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL,
                paused INTEGER NOT NULL, updated REAL NOT NULL)""")
            db.execute("INSERT INTO scheduler_state VALUES(1,1,0,?)", (time.time(),))
            db.execute("""CREATE TABLE scheduler_options (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id), timeout REAL NOT NULL)""")
            db.execute("""CREATE TABLE scheduler_dispatches (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id), token TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL, phase TEXT NOT NULL, processes TEXT,
                worker TEXT, resources TEXT NOT NULL, cleanup TEXT,
                created REAL NOT NULL, finished REAL)""")


def owner_identity():
    return {"boot_id": Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            "process": proc_identity(os.getpid())}


def live(identity):
    if identity["boot_id"] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        return False
    old = identity["process"]
    now = proc_identity(old["pid"])
    return bool(now and now["start"] == old["start"] and now["state"] not in ["Z", "X"])


def execution_admission(root, store, task_id, token=None):
    if token is None:
        if any(name in os.environ for name in
               ('LAF_SESSION_POOL_GENERATION', 'LAF_SESSION_POOL_CODEX_PIPELINE')):
            raise PoolPolicyError("A dispatch-bound worker requires its authorized token")
        settings = configuration()
        with ExitStack() as stack:
            stack.enter_context(lease(Path(root) / "daemon.lock", shared=True))
            stack.enter_context(pool_generation(root, settings))
            require_pool_headroom(settings)
            require_charged_controller(root, settings)
            token = str(uuid.uuid4())
            resource_owner = register_resource_owner(root, settings, 'standalone')
            if resource_owner is not None: stack.callback(close_resource_owner, settings, resource_owner)
            if 'resource_admission' in settings:
                stack.callback(finish_task_resources, root, store, task_id, settings,
                               token=token, preclaim=True)
            admitted = stack.pop_all()
            admitted.resource_token = token
            admitted.resource_owner = resource_owner
            admitted.resource_settings = settings
            return admitted
    deadline = time.monotonic() + 5
    while True:
        row = store.db.execute("SELECT * FROM scheduler_dispatches WHERE task_id=? AND token=?", (task_id, token)).fetchone()
        if not row or not live(json.loads(row["owner"])):
            raise ValueError("Dispatch owner/token is invalid or no longer alive")
        owner = json.loads(row["owner"])
        current = worker_configuration()
        if (type(owner.get('session_pool_codex_pipeline')) is not bool
                or owner['session_pool_codex_pipeline'] is not current['codex_pipeline']
                or owner.get('session_pool_generation') != current['pool_generation']):
            raise ValueError("Dispatch effective session pool generation/budget is invalid or changed")
        if row["phase"] == "STARTED":
            break
        if row["phase"] != "RESERVED" or time.monotonic() >= deadline:
            raise ValueError("Dispatch handoff was not authorized")
        time.sleep(.02)
    with ExitStack() as admitted:
        admitted.enter_context(pool_generation(root, current, worker=True))
        require_pool_headroom(current)
        join_task_resources(root, task_id, current, token)
        ancestors = {}
        pid = os.getpid()
        while pid > 1 and pid not in ancestors:
            actual = proc_identity(pid)
            if not actual: break
            ancestors[pid] = actual["start"]
            pid = actual["ppid"]
        recorded = json.loads(row["processes"] or "{}")
        if not any(ancestors.get(int(pid)) == start for pid, start in recorded.items()):
            raise ValueError("Worker is not descended from the reserved process")
        with store.db:
            cur = store.db.execute("""UPDATE scheduler_dispatches SET phase='AUTHORIZED',worker=?
                WHERE task_id=? AND token=? AND phase='STARTED' AND worker IS NULL""",
                (json.dumps(owner_identity()), task_id, token))
            if cur.rowcount != 1:
                raise ValueError("Dispatch token was already consumed")
        result = admitted.pop_all()
        result.resource_token = token
        result.resource_owner = None
        result.resource_settings = current
        return result



def worker_scope_clear(descriptor, owner):
    if scope_released(descriptor): return True
    # A durable prospective path closes the pre-receipt launch gap only after
    # the owning daemon is dead; the guarded worker cannot start later.
    expected = descriptor.get("expected_cgroup")
    if expected and not live(owner):
        try:
            relative = Path('/proc/self/cgroup').read_text().strip().split('::')[1]
            own_parent = (Path('/sys/fs/cgroup') / relative.lstrip('/')).parent
            candidate = Path(expected).resolve()
            return (candidate.parent == own_parent and own_parent.name == 'agentorchestrator.slice'
                    and candidate.name == descriptor["unit"] and scope_stat(candidate) is None)
        except (OSError,ValueError,KeyError,IndexError,AdmissionWait):return False
    return False

def recover_dispatches(runner):
    """Reconcile old reservations; never dispatch or infer successful execution."""
    recover_abandoned(runner.root, runner.store)
    db = runner.store.db
    pending = []
    for row in db.execute("SELECT * FROM scheduler_dispatches WHERE phase IN ('RESERVED','STARTED','AUTHORIZED')").fetchall():
        if live(json.loads(row["owner"])):
            continue
        processes = json.loads(row["processes"] or "{}")
        busy = any((now := proc_identity(int(pid))) and now["start"] == start and now["state"] not in ["Z", "X"]
                   for pid, start in processes.items())
        descriptor = json.loads(row["resources"])
        scope_clear = worker_scope_clear(descriptor, json.loads(row["owner"]))
        if busy or not scope_clear:
            task = runner.store.get(row["task_id"])
            if task["state"] == "QUEUED":
                runner.store.transition(task["id"], "QUEUED", "UNKNOWN", "Lost dispatch; worker release is unconfirmed")
            pending.append(row["task_id"])
            continue
        task = runner.store.get(row["task_id"])
        if task["state"] == "QUEUED":
            runner.store.transition(task["id"], "QUEUED", "UNKNOWN", "Lost durable dispatch reservation; never replayed")
            if scope_clear:
                runner.store.update(task["id"], cleanup=json.dumps({"survivors": [], "scheduler": "No execution claim; lost handoff closed"}))
        elif task["state"] in ["RUNNING", "VERIFYING"]:
            pending.append(task["id"])
            continue
        elif task["state"] == "COMPLETED":
            try:
                receipt = json.loads(Path(descriptor["receipt"]).read_text())
                confirmed = receipt["state"] == "FINISHED" and receipt["exit_code"] == 0 and not receipt.get("violation")
            except (OSError, ValueError, KeyError): confirmed = False
            if not confirmed:
                runner.store.transition(task["id"], "COMPLETED", "UNKNOWN", "Worker supervision lost after task checkpoint")
        with db:
            db.execute("UPDATE scheduler_dispatches SET phase='ORPHANED',finished=? WHERE task_id=?", (time.time(), task["id"]))
    return pending


def blocked_unknown(runner):
    from recovery import resource_facts
    blocked, workspaces = [], set()
    for row in runner.store.db.execute("SELECT * FROM tasks WHERE state='UNKNOWN'"):
        task = dict(row)
        execution = runner.store.execution(task["active_execution_id"]) if task["active_execution_id"] else None
        dispatch = runner.store.db.execute("SELECT resources,owner FROM scheduler_dispatches WHERE task_id=?", (task["id"],)).fetchone()
        worker_clear = not dispatch or worker_scope_clear(json.loads(dispatch[0]), json.loads(dispatch[1]))
        if not resource_facts(execution, task)["released"] or not worker_clear or not task["workspace_id"]:
            blocked.append(task["id"])
        if task["workspace_id"]: workspaces.add(task["workspace_id"])
    return blocked, workspaces
