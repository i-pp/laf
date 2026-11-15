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

# Added 2026-11-15: bounded installed-package MCP acceptance with durable reconciliation.
"""Exercise an installed source plugin. Never install, start a daemon or repair."""
import argparse
import asyncio
import copy
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import uuid
import zipfile
sys.dont_write_bytecode = True

LIMIT = 4 * 1024**2
PROMPT = 'Reply with exactly LAF_ACCEPTANCE_OK. Do not use tools, commands, files or network.'
EXPECTED_REPLY = 'LAF_ACCEPTANCE_OK'


class Refused(ValueError):
    pass


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refused('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refused('nonfinite_json')))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check_configuration(config, binding, pins):
    if digest(regular(config, 32768)) != pins['config_sha256']:
        raise Refused('configuration_changed')
    if 'binding_sha256' in pins and digest(regular(binding, 16384)) != pins['binding_sha256']:
        raise Refused('backend_binding_changed')


def check_catalog(found, expected, version, cursor=None):
    def comparable(tool):
        return {k: tool[k] for k in ('name', 'description', 'inputSchema', 'annotations')}
    if (version != '0.1.0' or cursor is not None or len(found) != 33
            or len({t['name'] for t in found}) != 33 or
            {t['name']: comparable(t) for t in found} !=
            {t['name']: comparable(t) for t in expected}):
        raise Refused('installed_catalog_mismatch')


def tool_data(result, *, capture_error=False):
    if result.isError is True and not capture_error:
        raise Refused('tool_error_result')
    body = result.structuredContent
    if type(body) is not dict:
        raise Refused('tool_result_unavailable')
    data = body.get('data', body)
    if type(data) is not dict:
        raise Refused('tool_result_unavailable')
    return dict(data, _mcp_is_error=True) if result.isError is True else data


def regular(path, limit=LIMIT):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('absolute_path_required')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused('symlink_refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise Refused('regular_bounded_file_required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise Refused('file_changed_or_oversized')
        return raw
    finally:
        os.close(fd)


def verify_package(archive, installed):
    """The expected archive hash must be reviewed separately; this proves equality."""
    installed = Path(installed)
    raw = regular(archive, 64 * 1024**2)
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        names = source.namelist()
        if not 1 <= len(names) <= 2000 or len(names) != len(set(names)):
            raise Refused('invalid_archive_inventory')
        total = 0
        for item in source.infolist():
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or str(path) != item.filename or item.is_dir()
                    or not stat.S_ISREG(item.external_attr >> 16)
                    or item.file_size > 2 * 1024**2):
                raise Refused('unsafe_archive_member')
            total += item.file_size
            if total > 64 * 1024**2:
                raise Refused('archive_too_large')
            if regular(installed / item.filename, 2 * 1024**2) != source.read(item):
                raise Refused('installed_package_drift')
    for needed in ('launcher.py', '.mcp.json', '.codex-plugin/plugin.json'):
        if needed not in names:
            raise Refused('incomplete_installed_plugin')
    observed = set()
    for directory, dirs, files in os.walk(installed, followlinks=False):
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise Refused('installed_directory_symlink')
        for name in files:
            observed.add((Path(directory) / name).relative_to(installed).as_posix())
            if len(observed) > 2000:
                raise Refused('installed_inventory_bound')
    if observed != set(names):
        raise Refused('unexpected_installed_files')
    meta = decode(regular(installed / '.codex-plugin/plugin.json'))
    if meta.get('name') != 'laf' or meta.get('version') != '0.1.0':
        raise Refused('unexpected_plugin_identity')
    manifest = decode(regular(installed / '.mcp.json'))
    if manifest != {'mcpServers': {'laf': {'command': 'python3',
                    'args': ['-B', './launcher.py'], 'cwd': '.'}}}:
        raise Refused('unexpected_manifest_command')
    return {'archive_sha256': digest(raw), 'source_members': len(names),
            'installed_root': str(installed), 'plugin_version': '0.1.0'}


def verify_runtime(installed, config):
    """Verify copied executable source before adding its directories to sys.path."""
    import importlib.util
    installed = Path(installed)
    spec = importlib.util.spec_from_file_location(
        '_laf_acceptance_bootstrap', installed / 'runtime/integration/laf_bootstrap.py')
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    source = (Path(config['runtime_source_root']) if config.get('schema') == 2
              else installed / 'runtime')
    if config.get('schema') not in (1, 2):
        raise Refused('unsupported_configuration')
    snapshot = bootstrap.source_snapshot()
    for name, raw in snapshot.items():
        if regular(source / name, 2 * 1024**2) != raw:
            raise Refused('copied_runtime_drift')
    if config['schema'] == 2:
        if regular(source.parent / 'launcher.py') != regular(installed / 'launcher.py'):
            raise Refused('copied_launcher_drift')
        for scope in ('integration', 'private_codex/src', 'public_laf/mcp/src'):
            for path in (source / scope).rglob('*'):
                if path.is_symlink() or (path.is_file() and path.relative_to(source).as_posix() not in snapshot):
                    raise Refused('unexpected_copied_runtime_file')
    return source


class Journal:
    """One owner-only append-only log; an incomplete write fences all dispatch."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or '..' in self.path.parts:
            raise Refused('absolute_journal_required')
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise Refused('symlink_journal_refused')
        parent = self.path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise Refused('private_journal_parent_required')
        self.fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT |
                          os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(self.fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > LIMIT):
                raise Refused('private_journal_required')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = os.read(self.fd, LIMIT + 1)
            if raw and not raw.endswith(b'\n'):
                raise Refused('incomplete_journal_preserved')
            self.rows = [decode(line) for line in raw.splitlines()]
            if any(type(row) is not dict for row in self.rows):
                raise Refused('invalid_journal')
        except BaseException:
            os.close(self.fd)
            raise

    def append(self, row):
        raw = (packed(row) + '\n').encode()
        info = os.fstat(self.fd)
        current = self.path.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise Refused('journal_identity_changed')
        if info.st_size + len(raw) > LIMIT:
            raise Refused('journal_capacity')
        remaining = raw
        while remaining:
            count = os.write(self.fd, remaining)
            if count <= 0:
                raise Refused('journal_write_failed')
            remaining = remaining[count:]
        os.fsync(self.fd)
        parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        self.rows.append(copy.deepcopy(row))

    def close(self):
        os.close(self.fd)


class Run:
    def __init__(self, journal, pin, call, *, wait=asyncio.sleep):
        self.journal, self.pin, self.call, self.wait = journal, pin, call, wait
        plans = [r for r in journal.rows if r.get('kind') == 'plan']
        if plans:
            if len(plans) != 1 or plans[0]['pin'] != pin:
                raise Refused('candidate_or_configuration_changed')
            self.plan = plans[0]
        else:
            self.plan = {'kind': 'plan', 'pin': pin, 'keys': {
                name: uuid.uuid4().hex for name in ('local', 'thread', 'task', 'cancel')}}
            journal.append(self.plan)

    async def observe(self, name, arguments):
        result = await self.call(name, arguments)
        self.journal.append({'kind': 'observation', 'tool': name, 'result': result})
        if result.get('_mcp_is_error') is True:
            raise Refused('tool_error_result')
        return result

    async def once(self, label, name, arguments, *, reconcile=None):
        intents = [r for r in self.journal.rows
                   if r.get('kind') == 'intent' and r.get('label') == label]
        results = [r for r in self.journal.rows
                   if r.get('kind') == 'result' and r.get('label') == label]
        intended = {'kind': 'intent', 'label': label, 'tool': name, 'arguments': arguments}
        if intents and (len(intents) != 1 or intents[0] != intended):
            raise Refused('conflicting_operation_input')
        if results:
            return results[-1]['result']
        if intents and reconcile is not None:
            # Router GET uses the original nonce even when the admission reply was lost.
            return await self.observe(*reconcile)
        if intents and name not in ('codex_thread_start', 'codex_task_submit',
                                    'codex_task_cancel', 'codex_thread_archive'):
            raise Refused('unresolved_operation_no_replay')
        if not intents:
            self.journal.append(intended)
        # The four Codex operations reconcile their exact immutable request_key in
        # the server ledger. No new key/thread/task is created on client resumption.
        result = await self.call(name, arguments)
        self.journal.append({'kind': 'result', 'label': label, 'result': result})
        return result

    async def local(self, model):
        status = await self.observe('laf_router_status', {})
        models = await self.observe('laf_router_models', {})
        if status.get('status') != 'ready' or model not in models.get('models', []):
            raise Refused('router_not_ready_or_model_not_allowed')
        key = self.plan['keys']['local']
        arguments = {'attempt_id': key, 'model': model, 'messages': [
            {'role': 'user', 'content': PROMPT}], 'max_tokens': 16, 'timeout_seconds': 30}
        result = await self.once('local', 'laf_router_submit_job', arguments,
                                reconcile=('laf_router_get_job', {'handle': key}))
        if result.get('handle') != key or not result.get('receipt_durable'):
            raise Refused('local_receipt_unconfirmed')
        job_id = result.get('job_id')
        for _ in range(35):
            result = await self.observe('laf_router_get_job', {'handle': key})
            if result.get('outcome') == 'UNKNOWN' or result.get('error'):
                raise Refused('local_outcome_unknown_preserve_handle')
            if (result.get('handle') != key or result.get('model') != model
                    or result.get('receipt_durable') is not True or not result.get('job_id')
                    or job_id is not None and result['job_id'] != job_id):
                raise Refused('local_receipt_identity_mismatch')
            job_id = result['job_id']
            if result.get('status') in ('completed', 'failed', 'cancelled'):
                if (result.get('outcome') != 'TERMINAL' or result.get('status') != 'completed' or
                        result.get('content', '').strip() != EXPECTED_REPLY):
                    raise Refused('local_acceptance_failed')
                return {'status': 'passed', 'handle': key, 'job_id': result['job_id'],
                        'scope': 'real MCP job completion; output unverified'}
            await self.wait(1)
        raise Refused('local_wait_bound_preserve_handle')

    async def codex_catalog(self, model, effort):
        catalog = await self.observe('codex_models_list', {})
        pages, rows = 0, list(catalog.get('models', []))
        cursor = catalog.get('next_cursor')
        while cursor:
            pages += 1
            if pages > 8:
                raise Refused('catalog_pagination_bound')
            page = await self.observe('codex_models_list', {'cursor': cursor})
            rows.extend(page.get('models', []))
            cursor = page.get('next_cursor')
        chosen = next((row for row in rows if row.get('model') == model), {})
        if (not chosen.get('selectable') or
                effort not in chosen.get('supported_reasoning_efforts', [])):
            raise Refused('model_or_effort_not_advertised')
    async def codex_quiet(self, *, owned_empty_context=False):
        capacity = (await self.observe('codex_threads_list', {'limit': 20})).get('capacity', {})
        release = capacity.get('executor_release_confirmed')
        if (any(capacity.get(k) != 0 for k in ('active_threads', 'pending_inputs', 'held_tasks'))
                or not (release is True or owned_empty_context and release is False)
                or capacity.get('executor_reconciliation_required') is not False):
            raise Refused('codex_not_idle')
        return capacity

    def known_task(self):
        """Task IDs come only from this journal's authoritative keyed response."""
        results = {r['label']: r['result'] for r in self.journal.rows if r.get('kind') == 'result'}
        return results.get('task')

    async def codex(self, workspace, model, effort):
        started = any(r.get('kind') == 'intent' and r.get('label') == 'thread' for r in self.journal.rows)
        if not started:
            await self.codex_catalog(model, effort)
            await self.codex_quiet()
        keys = self.plan['keys']
        thread = await self.once('thread', 'codex_thread_start', {
            'workspace': workspace, 'model': model, 'reasoning_effort': effort,
            'request_key': keys['thread']})
        if thread.get('_mcp_is_error') is True or thread.get('status') != 'READY' or not thread.get('thread_id'):
            raise Refused('thread_creation_unconfirmed_preserve_key')
        arguments = {
            'thread_id': thread['thread_id'], 'model': model, 'reasoning_effort': effort,
            'prompt': PROMPT, 'request_key': keys['task']}
        intents = [r for r in self.journal.rows if r.get('kind') == 'intent' and r.get('label') == 'task']
        if intents and intents[0]['arguments'] != arguments:
            raise Refused('conflicting_operation_input')
        if not intents:
            if started:
                await self.codex_catalog(model, effort)
            # The acknowledged empty thread deliberately retains its native
            # context/budget until first input. This is this journal's context,
            # after an initially quiet executor, not a foreign held task.
            await self.codex_quiet(owned_empty_context=True)
        # For an existing intent the immutable server request key is authoritative.
        # Never infer task ownership from a matching thread/prompt/model listing.
        task = await self.once('task', 'codex_task_submit', arguments)
        task_id = task.get('id') or task.get('task_id')
        if not task_id:
            raise Refused('task_submission_unconfirmed_preserve_key')
        for _ in range(125):
            task = await self.observe('codex_task_get', {'task_id': task_id})
            if (task.get('id') != task_id or task.get('thread_id') != thread['thread_id']
                    or task.get('model') != model or task.get('reasoning_effort') != effort
                    or task.get('prompt_sha256') != digest(packed(PROMPT).encode())):
                raise Refused('codex_task_identity_mismatch')
            state = task.get('state')
            if state in ('UNKNOWN', 'WAITING_APPROVAL'):
                raise Refused('codex_unresolved_preserve_task')
            if state in ('COMPLETED', 'FAILED', 'CANCELLED'):
                if state != 'COMPLETED' or task.get('answer', '').strip() != EXPECTED_REPLY:
                    raise Refused('codex_acceptance_failed')
                context = task.get('context_reasoning_effort_evidence', {})
                turn = task.get('reasoning_effort_evidence', {})
                if (context.get('method') not in ('thread/start', 'thread/resume')
                        or context.get('parameter') != 'config.model_reasoning_effort'
                        or context.get('requested') != effort or context.get('transport') != 'acknowledged'
                        or turn.get('turn_parameter') != 'effort'
                        or turn.get('turn_transport') != 'acknowledged'):
                    raise Refused('codex_parameter_forwarding_mismatch')
                capacity = await self.codex_quiet()
                return {'status': 'passed', 'task_id': task_id, 'thread_id': thread['thread_id'],
                        'model': model, 'reasoning_effort': effort,
                        'executor_release_confirmed': capacity['executor_release_confirmed'],
                        'thread_retained': True,
                        'provider_internal_effort_verified': False, 'billing_verified': False}
            await self.wait(1)
        raise Refused('codex_wait_bound_preserve_task')

    async def cancel(self):
        recovered = self.known_task()
        if recovered is not None:
            task_id = recovered.get('id') or recovered.get('task_id')
            if not task_id:
                raise Refused('no_known_task_to_cancel')
            result = await self.once('cancel', 'codex_task_cancel', {
                'task_id': task_id, 'request_key': self.plan['keys']['cancel']})
            await self.observe('codex_task_get', {'task_id': task_id})
        elif any(r.get('label') == 'task' and r.get('kind') == 'intent' for r in self.journal.rows):
            # Replaying submit merely to discover a cancellation target could
            # begin the original work if its first request never committed.
            raise Refused('task_receipt_unresolved_reconcile_original_key_before_cancel')
        elif any(r.get('label') == 'local' for r in self.journal.rows):
            handle = self.plan['keys']['local']
            result = await self.once('cancel', 'laf_router_cancel_job', {'handle': handle},
                                    reconcile=('laf_router_get_job', {'handle': handle}))
            await self.observe('laf_router_get_job', {'handle': handle})
        else:
            raise Refused('no_known_work_to_cancel')
        return {'status': 'observed', 'cancellation': result, 'physical_stop_confirmed': False}


async def exercise(args):
    if importlib.metadata.version('mcp') != '1.30.0':
        raise Refused('existing_mcp_sdk_1_30_0_required')
    package = verify_package(args.archive, args.installed_root)
    home = args.home
    config = home / '.config/laf/plugin.json'
    raw = regular(config, 32768)
    value = decode(raw)
    source = verify_runtime(args.installed_root, value)
    sys.path[:0] = [str(source / 'integration'), str(source / 'private_codex/src'),
                   str(source / 'public_laf/mcp/src')]
    from legacy_tool_adapter import tool_definitions
    from laf_shared_tools import CompositeSharedTools
    from types import SimpleNamespace
    from legacy_tool_adapter import SERVER_INFO
    expected = CompositeSharedTools(SimpleNamespace(
        tools=tool_definitions(local_llm=True, local_models=True, chat_mailbox=True,
                               codex_pipeline=True, experiments=True),
        call_tool=lambda *a: None, audit=None), SimpleNamespace(call=lambda *a: None),
        server_info=SERVER_INFO).tools
    binding = Path(value['runtime']) / 'unconfigured-backends.json'
    pins = {'config_sha256': digest(raw), **package}
    if binding.exists():
        pins['binding_sha256'] = digest(regular(binding, 16384))
    def check_pin():
        check_configuration(config, binding, pins)
    from datetime import timedelta
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    journal = Journal(args.journal)
    try:
        params = StdioServerParameters(command='/usr/bin/python3', args=['-B', './launcher.py'],
            cwd=str(args.installed_root), env={'PATH': '/usr/bin:/bin', 'HOME': str(home),
                'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120)) as session:
                initialized = await session.initialize()
                catalog = await session.list_tools()
                found = [tool.model_dump(by_alias=True, exclude_none=True) for tool in catalog.tools]
                check_catalog(found, expected, initialized.serverInfo.version, catalog.nextCursor)
                async def call(name, arguments):
                    check_pin()
                    result = await session.call_tool(name, arguments)
                    check_pin()
                    # Persist an authoritative error receipt/ID before refusing
                    # acceptance; cancellation must not lose a known target.
                    return tool_data(result, capture_error=True)
                run = Run(journal, pins, call)
                journal.append({'kind': 'catalog', 'tools': found, 'version': '0.1.0',
                                'scope': 'SDK MCP from installed manifest; Codex loader is a separate check'})
                if args.action == 'inspect':
                    result = {'status': 'observed', 'router': await run.observe('laf_router_status', {}),
                              'codex': await run.observe('codex_capabilities', {}),
                              'execution_accepted': False}
                elif args.action == 'local':
                    async with asyncio.timeout(45):
                        result = await run.local(args.model)
                elif args.action == 'codex':
                    async with asyncio.timeout(160):
                        result = await run.codex(args.workspace, args.model, args.effort)
                else:
                    result = await run.cancel()
                journal.append({'kind': 'outcome', 'action': args.action, 'result': result})
                return result
    finally:
        journal.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'local', 'codex', 'cancel'))
    for name in ('archive', 'installed-root', 'home', 'journal'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--workspace')
    parser.add_argument('--effort')
    args = parser.parse_args(argv)
    if (args.action in ('local', 'codex') and not args.model or
            args.action == 'codex' and (not args.workspace or not args.effort)):
        parser.error('explicit model, and Codex workspace/advertised effort, required')
    try:
        async def bounded():
            async with asyncio.timeout(200):
                return await exercise(args)
        print(packed(asyncio.run(bounded())))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(packed({'status': 'blocked_or_unresolved', 'journal': str(args.journal),
                      'reason': str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                      'automatic_retry': False, 'physical_stop_confirmed': False,
                      'next': 'Preserve this journal and keys; inspect the exact failure before resuming.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
