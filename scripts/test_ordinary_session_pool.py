#!/usr/bin/env python3
# KISS Open License Version 1.0 (KOL-v1)
#
# SPDX-FileCopyrightText: 2026 I-plusplus Inc.
# SPDX-License-Identifier: LicenseRef-KOL-v1
#
# Copyright remains with its respective holders; original copyright and attribution notices must be preserved.
# Contributors may add copyright notices for their own original modifications.
# Distributed modifications must be identified as required by Section 4(c) of KOL-v1.
#
# Use, modification, distribution, and incorporation into commercial or proprietary software are permitted
# without license fees or source disclosure, subject to all terms of the complete KOL-v1 license.
#
# Contributors grant the patent permissions stated in KOL-v1. Outputs are not covered merely because a
# KOL-licensed tool produced them; embedded licensed material remains subject to applicable license terms.
#
# THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OR LIABILITY TO THE EXTENT PERMITTED BY APPLICABLE LAW.
#
# This header is a summary, not a separate license or an amendment to KOL-v1.
# All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.

# Added 2026-11-13: configurable ordinary-pool admission without real workers.

"""Pure/fake/temp-file regressions; no socket, model, cgroup or child launch."""

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/private_codex/src'))
import concurrency
import daemon
import resources
import runner
import scheduler

BASE = json.loads(scheduler.CONFIG.read_text())


def task(number=1, workspace=None):
    return {'id': str(uuid.UUID(int=number)),
            'workspace_id': str(uuid.UUID(int=workspace or number + 1000))}


class PoolFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'scheduler.json'
        self.meminfo = self.root / 'meminfo'
        self.meminfo.write_text('MemTotal: 67108864 kB\nMemAvailable: 67108864 kB\n')
        self.patched(patch.object(scheduler, 'CONFIG', self.config))
        self.patched(patch.object(scheduler, 'MEMINFO', self.meminfo, create=True))
        self.patched(patch.dict(os.environ, {}, clear=False))
        os.environ.pop('LAF_SESSION_POOL_GENERATION', None)
        os.environ.pop('LAF_SESSION_POOL_CODEX_PIPELINE', None)
        self.patched(patch('codex_host.pipeline_held', return_value=False))
        self.write_config(8)

    def patched(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def write_config(self, maximum=8):
        value = copy.deepcopy(BASE)
        if maximum is None:
            value.pop('maximum_sessions', None)
        else:
            value['maximum_sessions'] = maximum
        self.config.write_text(json.dumps(value))
        return value

    def settings(self, maximum=8):
        self.write_config(maximum)
        return scheduler.configuration()


class PoolConfiguration(PoolFixture):
    def test_candidate_explicitly_selects_eight(self):
        self.assertEqual(BASE.get('maximum_sessions'), 8)

    def test_legacy_missing_setting_retains_two(self):
        self.write_config(None)
        self.assertEqual(scheduler.configuration()['maximum_sessions'], 2)

    def test_bounds_and_invalid_values(self):
        for maximum in (1, 2, 8):
            self.assertEqual(self.settings(maximum)['maximum_sessions'], maximum)
        for value in (True, False, None, 0, -1, 9, 2.0, '8', [], {}):
            with self.subTest(value=value):
                raw = copy.deepcopy(BASE)
                raw['maximum_sessions'] = value
                self.config.write_text(json.dumps(raw))
                with self.assertRaises(ValueError):
                    scheduler.configuration()

    def test_ambiguous_unreadable_and_symlink_policy_fail_closed(self):
        raw = self.config.read_text()
        self.config.write_text(raw[:-1] + ', "maximum_sessions": 1}')
        with self.assertRaises(ValueError):
            scheduler.configuration()
        self.config.unlink()
        with self.assertRaises(OSError):
            scheduler.configuration()
        other = self.root / 'other.json'
        other.write_text(raw)
        self.config.symlink_to(other)
        with self.assertRaises(ValueError):
            scheduler.configuration()

    def test_generation_is_semantic_and_binds_effective_pipeline_budget(self):
        plain = self.settings(8)
        raw = json.loads(self.config.read_text())
        self.config.write_text(json.dumps(raw, indent=2, sort_keys=True))
        reformatted = scheduler.configuration()
        self.assertEqual(plain['pool_generation'], reformatted['pool_generation'])
        pipeline = scheduler.configuration(codex_pipeline=True)
        self.assertNotEqual(plain['pool_generation'], pipeline['pool_generation'])
        self.assertEqual(scheduler.require_pool_generation(plain)['maximum_sessions'], 8)
        self.write_config(1)
        with self.assertRaises(scheduler.PoolPolicyError):
            scheduler.require_pool_generation(plain)

    def test_oversized_timing_is_invalid_instead_of_overflowing(self):
        for name in ('poll_seconds', 'task_timeout', 'shutdown_seconds'):
            for value in (10**400, -(10**400), float('inf'), float('-inf'), float('nan')):
                with self.subTest(name=name, value=value):
                    raw = self.write_config(8)
                    raw[name] = value
                    self.config.write_text(json.dumps(raw))
                    with self.assertRaises(ValueError):
                        scheduler.configuration()

    def test_memory_envelope_uses_unchanged_runner_and_worker_caps(self):
        settings = self.settings(8)
        runner = resources.policy()
        expected = settings['daemon']['memory_bytes'] + 8 * (
            runner['memory_bytes'] + settings['worker']['memory_bytes'])
        self.assertEqual(scheduler.session_memory_envelope(settings), expected)
        self.assertEqual(expected, 19864223744)
        pipeline = scheduler.configuration(codex_pipeline=True)
        self.assertEqual(scheduler.session_memory_envelope(pipeline), 25769803776)
        self.assertEqual(runner['memory_bytes'], 2147483648)
        self.assertEqual(settings['worker']['memory_bytes'], 268435456)

    def test_memory_admission_is_sampled_and_fail_closed(self):
        settings = self.settings(8)
        self.assertGreaterEqual(scheduler.require_pool_headroom(settings),
                                scheduler.session_memory_envelope(settings))
        for text in ('MemAvailable: 1 kB\n', 'MemTotal: 67108864 kB\n',
                     'MemAvailable: -1 kB\n', 'MemAvailable: 99999999 bytes\n',
                     'MemAvailable: 67108864 kB\nMemAvailable: 67108864 kB\n'):
            with self.subTest(text=text):
                self.meminfo.write_text(text)
                with self.assertRaises(resources.ResourceExceeded):
                    scheduler.require_pool_headroom(settings)
        self.meminfo.unlink()
        with self.assertRaises(resources.ResourceExceeded):
            scheduler.require_pool_headroom(settings)


class PoolLeases(PoolFixture):
    def test_one_two_and_eight_capacity_then_explicit_release(self):
        for maximum in (1, 2, 8):
            with self.subTest(maximum=maximum):
                self.write_config(maximum)
                with ExitStack() as stack:
                    for number in range(1, maximum + 1):
                        stack.enter_context(concurrency.guard(self.root, task(number)))
                    with self.assertRaises(RuntimeError):
                        with concurrency.guard(self.root, task(99)):
                            self.fail('An extra session was admitted')
                with concurrency.guard(self.root, task(99)):
                    pass

    def test_lowering_does_not_rebind_or_release_existing_workers(self):
        self.write_config(8)
        with ExitStack() as stack:
            for number in range(1, 9):
                stack.enter_context(concurrency.guard(self.root, task(number)))
            self.write_config(2)
            with self.assertRaises(RuntimeError):
                with concurrency.guard(self.root, task(99)):
                    self.fail('A new generation adopted active workers')
            for number in range(8):
                with self.assertRaises(RuntimeError):
                    concurrency.lease(self.root / 'leases' / f'slot-{number}.lock')
        with concurrency.guard(self.root, task(99)):
            pass

    def test_held_upper_slots_count_toward_lower_limit(self):
        settings = self.settings(2)
        with concurrency.pool_generation(self.root, settings), ExitStack() as stack:
            upper = stack.enter_context(concurrency.lease(self.root / 'leases' / 'slot-7.lock'))
            stack.enter_context(concurrency.guard(self.root, task(1)))
            with self.assertRaises(RuntimeError):
                with concurrency.guard(self.root, task(2)):
                    self.fail('Upper held slot was ignored')
            self.assertFalse(upper.closed)

    def test_active_legacy_runner_prevents_generation_rebind(self):
        settings = self.settings(8)
        with concurrency.lease(self.root / 'runner.lock', shared=True):
            with self.assertRaises(RuntimeError):
                concurrency.pool_generation(self.root, settings)
        with concurrency.pool_generation(self.root, settings):
            pass

    def test_idle_legacy_daemon_cannot_be_silently_adopted(self):
        settings = self.settings(8)
        with concurrency.lease(self.root / 'daemon.lock'):
            with self.assertRaises(RuntimeError):
                concurrency.pool_generation(self.root, settings)

    def test_standalone_pin_blocks_a_future_legacy_daemon(self):
        settings = self.settings(8)
        with concurrency.pool_generation(self.root, settings):
            with self.assertRaises(RuntimeError):
                concurrency.lease(self.root / 'daemon.lock')

    def test_worker_joins_only_a_marked_current_daemon(self):
        settings = self.settings(8)
        with concurrency.lease(self.root / 'daemon.lock'):
            with concurrency.pool_generation(self.root, settings, daemon_owned=True):
                with concurrency.guard(self.root, task(1)):
                    pass
                self.write_config(2)
                with self.assertRaises(RuntimeError):
                    with concurrency.guard(self.root, task(2)):
                        self.fail('Worker joined a changed generation')

    def test_generation_record_is_not_rewritten_while_pinned(self):
        settings = self.settings(8)
        with concurrency.pool_generation(self.root, settings):
            path = self.root / 'leases' / 'pool-generation.lock'
            before = path.read_bytes()
            self.write_config(1)
            with self.assertRaises(RuntimeError):
                concurrency.pool_generation(self.root, scheduler.configuration())
            self.assertEqual(path.read_bytes(), before)

    def test_malformed_pinned_record_is_not_repaired_or_deleted(self):
        settings = self.settings(8)
        with concurrency.pool_generation(self.root, settings):
            pass
        path = self.root / 'leases' / 'pool-generation.lock'
        path.write_text('{')
        with self.assertRaises(RuntimeError):
            concurrency.pool_generation(self.root, settings)
        self.assertEqual(path.read_text(), '{')

    def test_task_workspace_and_pipeline_exclusions_are_preserved(self):
        with concurrency.guard(self.root, task(1)):
            for duplicate in (task(1, 9999), task(2, 1001)):
                with self.assertRaises(RuntimeError):
                    with concurrency.guard(self.root, duplicate):
                        self.fail('Task/workspace exclusion was lost')
            with concurrency.guard(self.root, task(3)):
                pass
        with patch('codex_host.pipeline_held', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'Pipeline'):
                with concurrency.guard(self.root, task(4)):
                    self.fail('An unresolved pipeline was ignored')

    def test_error_releases_own_locks_but_not_foreign_lease(self):
        settings = self.settings(8)
        with concurrency.pool_generation(self.root, settings), concurrency.lease(
                self.root / 'leases' / 'slot-7.lock') as foreign:
            with self.assertRaisesRegex(ValueError, 'fixture'):
                with concurrency.guard(self.root, task(1)):
                    raise ValueError('fixture')
            with concurrency.guard(self.root, task(1)):
                pass
            self.assertFalse(foreign.closed)
            with self.assertRaises(RuntimeError):
                concurrency.lease(self.root / 'leases' / 'slot-7.lock')

    def test_changed_or_invalid_dispatch_generation_cannot_fall_back(self):
        settings = self.settings(8)
        for generation in ('not-a-generation', '0' * 64):
            with patch.dict(os.environ, {'LAF_SESSION_POOL_GENERATION': generation}):
                with self.assertRaises(RuntimeError):
                    with concurrency.guard(self.root, task(1)):
                        self.fail('The worker ignored its dispatch generation')
        with concurrency.lease(self.root / 'daemon.lock'), concurrency.pool_generation(
                self.root, settings, daemon_owned=True), patch.dict(os.environ, {
                'LAF_SESSION_POOL_GENERATION': settings['pool_generation'],
                'LAF_SESSION_POOL_CODEX_PIPELINE': '0'}):
            with concurrency.guard(self.root, task(1)):
                pass

    def test_invalid_policy_does_not_release_an_existing_slot(self):
        with concurrency.guard(self.root, task(1)):
            self.config.write_text('{')
            with self.assertRaises((ValueError, RuntimeError)):
                with concurrency.guard(self.root, task(2)):
                    self.fail('Malformed policy was accepted')
            with self.assertRaises(RuntimeError):
                concurrency.lease(self.root / 'leases' / 'slot-0.lock')

    def test_generation_downgrade_never_uses_blocking_flock(self):
        settings = self.settings(8)
        original = concurrency.fcntl.flock
        operations = []

        def nonblocking(fd, operation):
            operations.append(operation)
            self.assertTrue(operation & concurrency.fcntl.LOCK_NB,
                            'Generation conversion must not create an unbounded wait')
            return original(fd, operation)

        with patch.object(concurrency.fcntl, 'flock', side_effect=nonblocking):
            with concurrency.pool_generation(self.root, settings):
                pass
        self.assertTrue(operations)

        def contested_conversion(fd, operation):
            if operation == concurrency.fcntl.LOCK_SH | concurrency.fcntl.LOCK_NB:
                raise BlockingIOError('owned fake conversion contender')
            return original(fd, operation)

        with patch.object(concurrency.fcntl, 'flock', side_effect=contested_conversion):
            with self.assertRaises(RuntimeError):
                concurrency.pool_generation(self.root, settings)
        with concurrency.pool_generation(self.root, settings):
            pass

    def test_admission_wait_is_bounded_without_reclaiming_a_foreign_lease(self):
        fake = object.__new__(runner.Runner)
        fake.root = self.root
        fake.store = SimpleNamespace(get=lambda _: task(1), recover=lambda _: None,
                                     claim=lambda *args: None)
        fake.workroot = lambda _: self.root
        fake.development = False
        executed, errors = [], []
        done = threading.Event()
        fake._execute = lambda *args: executed.append(args)

        def contender():
            try:
                fake.run(task(1)['id'], timeout=.01)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        holder = concurrency.lease(self.root / 'leases' / 'admission.lock')
        thread = threading.Thread(target=contender)
        with patch.object(runner, 'workspace_snapshot', return_value={}), patch.object(
                runner, 'proc_identity', return_value={'pid': 1, 'start': 42}):
            try:
                thread.start()
                self.assertTrue(done.wait(.15), 'Admission exceeded its independent bounded wait')
                self.assertFalse(holder.closed)
                with self.assertRaises(RuntimeError):
                    concurrency.lease(self.root / 'leases' / 'admission.lock')
            finally:
                holder.close()
                thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(executed, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_terminal_or_unknown_database_labels_do_not_free_a_lease(self):
        self.write_config(1)
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE tasks(id TEXT, workspace_id TEXT, state TEXT)')
        owner = task(1)
        db.execute("INSERT INTO tasks VALUES(?,?,'RUNNING')", (owner['id'], owner['workspace_id']))
        recovered = []
        store = SimpleNamespace(db=db, recover=lambda key: recovered.append(key) or [key])
        with concurrency.guard(self.root, owner):
            for state in ('RUNNING', 'VERIFYING', 'UNKNOWN', 'FAILED', 'COMPLETED'):
                db.execute('UPDATE tasks SET state=?', (state,))
                self.assertEqual(concurrency.recover_abandoned(self.root, store), [])
                with self.assertRaises(RuntimeError):
                    with concurrency.guard(self.root, task(2)):
                        self.fail('A database label released the held slot')
            self.assertEqual(recovered, [])
        with concurrency.guard(self.root, task(2)):
            pass

    def test_recovery_guard_does_not_require_new_policy_or_memory(self):
        self.config.write_text('{')
        self.meminfo.write_text('MemAvailable: 1 kB\n')
        with concurrency.guard(self.root, task(1), slot=False):
            with self.assertRaises(RuntimeError):
                concurrency.lease(self.root / 'leases' / (task(1)['id'] + '.lock'))
        self.assertFalse((self.root / 'leases' / 'pool-generation.lock').exists())

    def test_atomic_admission_with_upper_slot_retained(self):
        settings = self.settings(2)
        begin = threading.Barrier(12)
        release = threading.Event()
        attempted = threading.Event()
        mutex = threading.Lock()
        counts = {'attempted': 0, 'entered': 0}
        errors = []

        def contender(number):
            try:
                begin.wait(timeout=5)
                with concurrency.guard(self.root, task(number + 1)):
                    with mutex:
                        counts['entered'] += 1
                        counts['attempted'] += 1
                        if counts['attempted'] == 12:
                            attempted.set()
                    release.wait(timeout=5)
            except RuntimeError:
                with mutex:
                    counts['attempted'] += 1
                    if counts['attempted'] == 12:
                        attempted.set()
            except BaseException as exc:
                errors.append(exc)
                attempted.set()

        with concurrency.pool_generation(self.root, settings), concurrency.lease(
                self.root / 'leases' / 'slot-7.lock'):
            threads = [threading.Thread(target=contender, args=(n,)) for n in range(12)]
            for thread in threads:
                thread.start()
            try:
                self.assertTrue(attempted.wait(timeout=5))
                self.assertEqual(errors, [])
                self.assertEqual(counts, {'attempted': 12, 'entered': 1})
            finally:
                release.set()
                for thread in threads:
                    thread.join(timeout=5)
            self.assertFalse(any(thread.is_alive() for thread in threads))


class PoolExecutionAdmission(PoolFixture):
    def test_standalone_execution_checks_memory_and_retains_generation(self):
        settings = self.settings(8)
        with scheduler.execution_admission(self.root, None, task(1)['id']):
            self.write_config(1)
            with self.assertRaises(RuntimeError):
                concurrency.pool_generation(self.root, scheduler.configuration())
        self.write_config(8)
        self.meminfo.write_text('MemAvailable: 1 kB\n')
        with self.assertRaises(resources.ResourceExceeded):
            scheduler.execution_admission(self.root, None, task(1)['id'])
        with concurrency.lease(self.root / 'daemon.lock'):
            pass

    def dispatch_fixture(self, *, generation=True, codex_pipeline=False):
        self.write_config(8)
        settings = scheduler.configuration(codex_pipeline=codex_pipeline)
        self.dispatch_root = self.root / uuid.uuid4().hex
        self.dispatch_root.mkdir()
        owned = ExitStack()
        self.addCleanup(owned.close)
        owned.enter_context(concurrency.lease(self.dispatch_root / 'daemon.lock'))
        owned.enter_context(concurrency.pool_generation(self.dispatch_root, settings, daemon_owned=True))
        self.patched(patch.dict(os.environ, {
            'LAF_SESSION_POOL_GENERATION': settings['pool_generation'],
            'LAF_SESSION_POOL_CODEX_PIPELINE': '1' if codex_pipeline else '0'}))
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.execute("""CREATE TABLE scheduler_dispatches(task_id TEXT, token TEXT, owner TEXT,
                   phase TEXT, processes TEXT, worker TEXT)""")
        owner = {'session_pool_generation': settings['pool_generation'],
                 'session_pool_codex_pipeline': codex_pipeline} if generation else {}
        db.execute("INSERT INTO scheduler_dispatches VALUES(?,?,?,'STARTED',?,NULL)",
                   (task(1)['id'], 'fake-token', json.dumps(owner), json.dumps({str(os.getpid()): 42})))
        self.patched(patch.object(scheduler, 'live', return_value=True))
        self.patched(patch.object(scheduler, 'proc_identity', return_value={'start': 42, 'ppid': 1}))
        self.patched(patch.object(scheduler, 'owner_identity', return_value={'fixture': True}))
        return SimpleNamespace(db=db), settings

    def test_same_generation_dispatch_is_one_use_only(self):
        store, _ = self.dispatch_fixture()
        with scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token'):
            pass
        self.assertEqual(store.db.execute('SELECT phase FROM scheduler_dispatches').fetchone()[0],
                         'AUTHORIZED')
        with self.assertRaises(ValueError):
            scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token')

    def test_old_daemon_or_changed_config_cannot_authorize_worker(self):
        store, _ = self.dispatch_fixture(generation=False)
        with self.assertRaises(ValueError):
            scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token')
        self.assertEqual(store.db.execute('SELECT phase FROM scheduler_dispatches').fetchone()[0], 'STARTED')
        store, _ = self.dispatch_fixture()
        self.write_config(2)
        with self.assertRaises((ValueError, scheduler.PoolPolicyError)):
            scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token')
        self.assertEqual(store.db.execute('SELECT phase FROM scheduler_dispatches').fetchone()[0], 'STARTED')

    def test_pipeline_worker_rechecks_the_effective_daemon_memory_budget(self):
        store, settings = self.dispatch_fixture(codex_pipeline=True)
        self.meminfo.write_text('MemAvailable: ' + str(21 * 1024**2) + ' kB\n')
        with self.assertRaises(resources.ResourceExceeded):
            scheduler.require_pool_headroom(settings)
        with self.assertRaises(resources.ResourceExceeded):
            with scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token'):
                with concurrency.guard(self.dispatch_root, task(1)):
                    self.fail('The worker used the weaker ordinary memory budget')
        self.assertEqual(store.db.execute('SELECT phase FROM scheduler_dispatches').fetchone()[0], 'STARTED')

    def test_missing_malformed_or_stale_mode_never_authorizes_a_weaker_budget(self):
        variants = [('owner', None), ('owner', 1), ('owner', 'true'), ('owner', False),
                    ('environment', None), ('environment', 'true'), ('environment', '0'),
                    ('generation', None)]
        for field, value in variants:
            with self.subTest(field=field, value=value):
                store, settings = self.dispatch_fixture(codex_pipeline=True)
                if field == 'owner':
                    owner = json.loads(store.db.execute('SELECT owner FROM scheduler_dispatches').fetchone()[0])
                    if value is None:
                        owner.pop('session_pool_codex_pipeline', None)
                    else:
                        owner['session_pool_codex_pipeline'] = value
                    store.db.execute('UPDATE scheduler_dispatches SET owner=?', (json.dumps(owner),))
                else:
                    key = ('LAF_SESSION_POOL_CODEX_PIPELINE' if field == 'environment'
                           else 'LAF_SESSION_POOL_GENERATION')
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                with self.assertRaises((ValueError, RuntimeError)):
                    scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token')
                self.assertEqual(store.db.execute('SELECT phase FROM scheduler_dispatches').fetchone()[0], 'STARTED')

    def test_consistent_stale_ordinary_handoff_cannot_join_pipeline_generation(self):
        store, pipeline = self.dispatch_fixture(codex_pipeline=True)
        ordinary = scheduler.configuration()
        stale = {'session_pool_generation': ordinary['pool_generation'],
                 'session_pool_codex_pipeline': False}
        store.db.execute('UPDATE scheduler_dispatches SET owner=?', (json.dumps(stale),))
        self.meminfo.write_text('MemAvailable: ' + str(21 * 1024**2) + ' kB\n')
        with patch.dict(os.environ, {'LAF_SESSION_POOL_GENERATION': ordinary['pool_generation'],
                                    'LAF_SESSION_POOL_CODEX_PIPELINE': '0'}):
            with self.assertRaises(RuntimeError):
                scheduler.execution_admission(self.dispatch_root, store, task(1)['id'], 'fake-token')
        self.assertNotEqual(ordinary['pool_generation'], pipeline['pool_generation'])
        self.assertEqual(store.db.execute('SELECT phase FROM scheduler_dispatches').fetchone()[0], 'STARTED')


class PoolDispatcher(PoolFixture):
    def fake_daemon(self, maximum):
        settings = self.settings(maximum)
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE scheduler_state(id INTEGER, paused INTEGER);
            INSERT INTO scheduler_state VALUES(1,0);
            CREATE TABLE tasks(id TEXT PRIMARY KEY, state TEXT, workspace_id TEXT,
                               resume_of TEXT, resume_approval TEXT);
            CREATE TABLE scheduler_options(task_id TEXT PRIMARY KEY, timeout REAL);
            CREATE TABLE scheduler_dispatches(task_id TEXT PRIMARY KEY, token TEXT,
                owner TEXT, phase TEXT, processes TEXT, worker TEXT, resources TEXT,
                cleanup TEXT, created REAL, finished REAL);
        """)
        for number in range(1, maximum + 4):
            candidate = task(number)
            db.execute("INSERT INTO tasks VALUES(?,'QUEUED',?,NULL,NULL)",
                       (candidate['id'], candidate['workspace_id']))
        db.commit()
        obj = object.__new__(daemon.Daemon)
        obj.settings = settings
        obj.root = self.root
        obj.store = SimpleNamespace(db=db, get=lambda key: dict(db.execute(
            'SELECT * FROM tasks WHERE id=?', (key,)).fetchone()))
        obj.runner = SimpleNamespace(development=False, root=self.root, store=obj.store,
                                     resource_limits=resources.policy())
        obj.owner = {}
        obj.boot_id = 'fake-boot'
        obj.socket_path = None
        obj.children = {}
        obj.stopping = False
        obj.blocked = []
        obj.pending_recovery = []
        obj.reconciled_unknowns = set()
        obj.allow_codex_pipeline = False
        obj.pipeline = SimpleNamespace(held=False, pump=lambda: None)
        obj.reap = lambda: None
        obj.started = []

        def start(candidate):
            obj.started.append(candidate['id'])
            obj.children[candidate['id']] = SimpleNamespace(closed=True)
            db.execute('INSERT INTO scheduler_dispatches(task_id) VALUES(?)', (candidate['id'],))
        obj.start_worker = start
        self.patched(patch.object(scheduler, 'recover_dispatches', return_value=[]))
        self.patched(patch.object(scheduler, 'blocked_unknown', return_value=([], set())))
        self.patched(patch.object(daemon, 'storage_check', return_value=None))
        return obj

    def test_status_fifo_page_and_dispatch_agree_on_one_two_eight(self):
        for maximum in (1, 2, 8):
            with self.subTest(maximum=maximum):
                obj = self.fake_daemon(maximum)
                statements = []
                obj.store.db.set_trace_callback(statements.append)
                self.assertEqual(obj.status()['maximum_sessions'], maximum)
                obj.tick()
                self.assertEqual(len(obj.started), maximum)
                self.assertTrue(any('ORDER BY t.rowid LIMIT ' + str(maximum) in query
                                    for query in statements))
                obj.tick()
                self.assertEqual(len(obj.started), maximum)

    def test_changed_generation_blocks_dispatch_without_stopping_children(self):
        obj = self.fake_daemon(8)
        retained = task(999)['id']
        obj.children[retained] = SimpleNamespace(closed=True)
        obj.store.db.execute("INSERT INTO tasks VALUES(?,'RUNNING',?,NULL,NULL)",
                             (retained, task(999)['workspace_id']))
        self.write_config(1)
        obj.tick()
        self.assertEqual(obj.started, [])
        self.assertIn(retained, obj.children)
        self.assertFalse(obj.stopping)
        self.assertEqual(obj.status()['maximum_sessions'], 8)
        self.assertFalse(obj.status()['session_pool_policy_current'])
        self.assertIn('session_pool_policy_changed', obj.status()['blocked'])

    def test_unreadable_policy_and_memory_pressure_block_new_dispatch(self):
        obj = self.fake_daemon(8)
        self.config.unlink()
        obj.tick()
        self.assertEqual(obj.started, [])
        self.assertIn('session_pool_policy_changed', obj.status()['blocked'])
        self.write_config(8)
        self.meminfo.write_text('MemAvailable: 1 kB\n')
        obj.tick()
        self.assertEqual(obj.started, [])
        self.assertIn('session_pool_memory_headroom', obj.status()['blocked'])

    def test_oversized_live_timing_blocks_without_stopping_existing_children(self):
        obj = self.fake_daemon(8)
        retained = task(999)['id']
        obj.children[retained] = SimpleNamespace(closed=True)
        obj.store.db.execute("INSERT INTO tasks VALUES(?,'RUNNING',?,NULL,NULL)",
                             (retained, task(999)['workspace_id']))
        raw = self.write_config(8)
        raw['poll_seconds'] = 10**400
        self.config.write_text(json.dumps(raw))
        with self.assertRaises(scheduler.PoolPolicyError):
            scheduler.require_pool_generation(obj.settings)
        obj.tick()
        self.assertEqual(obj.started, [])
        self.assertIn(retained, obj.children)
        self.assertFalse(obj.stopping)
        self.assertFalse(obj.status()['session_pool_policy_current'])
        self.assertIn('session_pool_policy_changed', obj.status()['blocked'])

    def test_start_worker_handoff_carries_the_pinned_generation(self):
        obj = self.fake_daemon(8)
        obj.runner.binary = '/fixture/codex'
        obj.worker_factory = lambda *args: ['fixture-worker']
        obj.owner = {'session_pool_generation': obj.settings['pool_generation'],
                     'session_pool_codex_pipeline': obj.allow_codex_pipeline}
        obj.event = lambda *args, **kwargs: None
        control = SimpleNamespace(unit='fixture.scope', descriptor={'unit': 'fixture.scope'})
        process = SimpleNamespace(seen={})
        original = Path.read_text

        def read(path, *args, **kwargs):
            if str(path) == '/proc/self/cgroup':
                return '0::/agentorchestrator.slice/fixture-daemon.scope\n'
            return original(path, *args, **kwargs)

        with patch.object(daemon, 'Control', return_value=control), patch.object(
                daemon, 'Process', return_value=process) as launch, patch.object(Path, 'read_text', read):
            daemon.Daemon.start_worker(obj, obj.store.get(task(1)['id']))
        self.assertEqual(launch.call_args.kwargs['env']['LAF_SESSION_POOL_GENERATION'],
                         obj.settings['pool_generation'])
        self.assertEqual(launch.call_args.kwargs['env']['LAF_SESSION_POOL_CODEX_PIPELINE'], '0')
        row = obj.store.db.execute('SELECT owner,phase FROM scheduler_dispatches').fetchone()
        self.assertEqual(json.loads(row['owner'])['session_pool_generation'], obj.settings['pool_generation'])
        self.assertIs(json.loads(row['owner'])['session_pool_codex_pipeline'], False)
        self.assertEqual(row['phase'], 'STARTED')

    def test_start_worker_checks_generation_before_creating_resource_scope(self):
        obj = self.fake_daemon(8)
        self.write_config(2)
        with patch.object(daemon, 'Control') as control:
            with self.assertRaises(scheduler.PoolPolicyError):
                daemon.Daemon.start_worker(obj, task(99))
            control.assert_not_called()


class PoolConstructor(PoolFixture):
    def test_post_pin_failure_unwinds_owned_resources_with_traceback_retained(self):
        for stage in ('identity', 'selector', 'log', 'budget'):
            with self.subTest(stage=stage):
                root = self.root / stage
                (root / 'logs').mkdir(parents=True)
                store = SimpleNamespace(close=Mock())
                fake_runner = SimpleNamespace(root=root, store=store)
                selector = SimpleNamespace(close=Mock())
                expected = PermissionError('owned fixture ' + stage + ' failure')
                original_open = Path.open

                def open_log(path, *args, **kwargs):
                    if stage == 'log' and path.parent == root / 'logs':
                        raise expected
                    return original_open(path, *args, **kwargs)

                retained, failed = None, None
                foreign = concurrency.lease(root / 'foreign.lock')
                with ExitStack() as patches:
                    patches.enter_context(patch.object(daemon, 'Runner', return_value=fake_runner))
                    patches.enter_context(patch.object(scheduler, 'initialize', return_value=None))
                    patches.enter_context(patch.object(scheduler, 'owner_identity',
                        side_effect=expected if stage == 'identity' else None, return_value={'fixture': True}))
                    patches.enter_context(patch.object(daemon.selectors, 'DefaultSelector',
                        side_effect=expected if stage == 'selector' else None, return_value=selector))
                    patches.enter_context(patch.object(Path, 'open', open_log))
                    if stage == 'budget':
                        patches.enter_context(patch.object(daemon, 'BudgetLog', side_effect=expected))
                    try:
                        try:
                            daemon.Daemon(root)
                        except PermissionError as exc:
                            retained = exc
                        self.assertIs(retained, expected)
                        trace = retained.__traceback__
                        while trace:
                            candidate = trace.tb_frame.f_locals.get('self')
                            if isinstance(candidate, daemon.Daemon):
                                failed = candidate
                                break
                            trace = trace.tb_next
                        self.assertIsNotNone(failed)
                        store.close.assert_called_once_with()
                        if stage not in ('identity', 'selector'):
                            selector.close.assert_called_once_with()
                        stream = getattr(failed, 'events_stream', None)
                        if stream is not None:
                            self.assertTrue(stream.closed)
                        for name in ('daemon.lock', 'leases/pool-daemon.lock', 'leases/pool-generation.lock'):
                            with concurrency.lease(root / name):
                                pass
                        self.assertFalse(foreign.closed)
                        with self.assertRaises(RuntimeError):
                            concurrency.lease(root / 'foreign.lock')
                    finally:
                        # RED runs must also release only their own fixture resources.
                        if failed is None and retained is not None:
                            trace = retained.__traceback__
                            while trace:
                                candidate = trace.tb_frame.f_locals.get('self')
                                if isinstance(candidate, daemon.Daemon):
                                    failed = candidate
                                    break
                                trace = trace.tb_next
                        if failed is not None:
                            for name in ('events_stream', 'pool_lease', 'lease'):
                                owned = getattr(failed, name, None)
                                if owned is not None:
                                    owned.close()
                        if not selector.close.called:
                            selector.close()
                        if not store.close.called:
                            store.close()
                        foreign.close()

    def test_daemon_runner_guards_join_its_effective_pipeline_generation(self):
        (self.root / 'logs').mkdir()
        store = SimpleNamespace(close=Mock())
        fake_runner = object.__new__(runner.Runner)
        fake_runner.root, fake_runner.store = self.root, store
        selector = SimpleNamespace(close=Mock())
        obj = None
        with patch.object(daemon, 'Runner', return_value=fake_runner), patch.object(
                scheduler, 'initialize', return_value=None), patch.object(
                scheduler, 'owner_identity', return_value={'fixture': True}), patch.object(
                daemon.selectors, 'DefaultSelector', return_value=selector):
            try:
                obj = daemon.Daemon(self.root, allow_codex_pipeline=True)
                self.assertIs(obj.runner.scheduler_settings, obj.settings)
                with obj.runner.task_lock(task(1)):
                    pass
            finally:
                if obj is not None:
                    if hasattr(obj, '_owned'):
                        obj._owned.close()
                    else:
                        for name in ('events_stream', 'pool_lease', 'lease'):
                            getattr(obj, name).close()
                        selector.close()
                        store.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
