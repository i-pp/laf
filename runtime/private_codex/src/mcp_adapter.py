"""Bounded MCP facade; stdio default, optional loopback HTTP, no resident scheduler."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

from codex_pipeline import (TOOLS as PIPELINE_TOOLS, PipelineError,
                            tool_definitions as pipeline_tools, validate_arguments as pipeline_arguments)
from control_socket import request
from local_principal import LocalPrincipal, OAuthPrincipal, current_local_principal
from notification_outbox import NotificationError
from runtime_scope import resolve_project_root, runtime_path
from storage import STATES
from verification import normalize_acceptance
from workspaces import spec
from chat_mailbox import Mailbox, MailboxError, TOOLS as MAILBOX_TOOLS, validate_arguments as mailbox_arguments
from experiments import (TOOLS as EXPERIMENT_TOOLS, EvidenceError, ExperimentStore,
                         tool_definitions as experiment_tools, validate_arguments as experiment_arguments)
from legacy_tool_adapter import Invalid as ExistingToolInvalid

PROJECT = Path(__file__).resolve().parents[1]
PROTOCOL = '2025-11-25'
REQUEST_PROTOCOL = '2026-07-28'
VERSION_META = 'io.modelcontextprotocol/protocolVersion'
CAPABILITIES_META = 'io.modelcontextprotocol/clientCapabilities'
CLIENT_META = 'io.modelcontextprotocol/clientInfo'
SERVER_META = 'io.modelcontextprotocol/serverInfo'
SERVER_INFO = {'name': 'agent-orchestrator-local', 'version': '0.2.0'}
MAX_INPUT = 65536
MAX_OUTPUT = 65536
AUDIT_LIMIT = 4 * 1024**2
TIMEOUT = 2
KEY_PATTERN = r'^[A-Za-z0-9_.:-]{1,128}$'
UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'


class Invalid(ValueError):
    pass


def decode(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise ValueError('Duplicate JSON member')
            result[k] = v
        return result
    value = json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    # Reject overflowed numeric literals and unpaired escaped surrogates too.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return value


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise Invalid('Unexpected or missing fields')


def uuid_value(value):
    return value if isinstance(value, str) and re.fullmatch(UUID_PATTERN, value) else None


def task_view(task):
    if not isinstance(task, dict) or not uuid_value(task.get('id')) or task.get('state') not in STATES:
        raise ValueError('Invalid daemon task')
    result = {'task_id': task['id'], 'state': task['state']}
    for key in ['active_execution_id', 'workspace_id']:
        result[key] = uuid_value(task.get(key))
    result['thread_id'] = uuid_value(task.get('session_id'))
    result['turn_id'] = uuid_value(task.get('turn_id'))
    # No prompt/result/error, config, command, owner, environment or log path is exported.
    enums = {
        'codex_status': {'NOT_STARTED', 'DISPATCHING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN', 'CANCELLED', 'TIMED_OUT', 'LEGACY_UNVERIFIED'},
        'verification_status': {'NOT_VERIFIED', 'RUNNING', 'PASSED', 'FAILED', 'UNKNOWN', 'CANCELLED', 'TIMED_OUT', 'LEGACY_UNVERIFIED'},
        'human_status': {'NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED', 'WAITING'},
        'scheduler_phase': {'RESERVED', 'STARTED', 'AUTHORIZED', 'FINISHED', 'ORPHANED'},
    }
    for key, values in enums.items():
        result[key] = task.get(key) if task.get(key) in values else None
    for key in ['completion_verified', 'worker_pending']:
        result[key] = task.get(key) is True
    return result


def tool_definitions(writes=False, local_llm=False, local_models=False,
                     chat_mailbox=False, codex_pipeline=False, experiments=False):
    identifier = {'type': 'string', 'pattern': UUID_PATTERN, 'maxLength': 36}
    def tool(name, description, properties, required=(), read=True):
        return {'name': name, 'description': description,
                'inputSchema': {'type': 'object', 'properties': properties,
                                'required': list(required), 'additionalProperties': False},
                'outputSchema': {'type': 'object', 'properties': {'call_id': identifier, 'data': {'type': 'object'}},
                                 'required': ['call_id', 'data'], 'additionalProperties': False},
                'annotations': {'readOnlyHint': read, 'destructiveHint': not read,
                                'idempotentHint': read, 'openWorldHint': False}}
    result = [
        tool('orchestrator_status', 'Read scheduler metadata. Does not execute a task.', {}),
        tool('orchestrator_list_tasks', 'Read a bounded page of task metadata.', {
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
            'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}}),
        tool('orchestrator_get_task', 'Read state and verification metadata. Task text and logs are not exported.',
             {'task_id': identifier}, ['task_id'])]
    if local_llm:
        result.append(tool('aceis_local_llm_generate',
            'Generate an UNVERIFIED local coding suggestion. No code execution or model loading.', {
                'task': {'type': 'string', 'enum': ['draft', 'review']},
                'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 16384},
                'max_tokens': {'type': 'integer', 'minimum': 1, 'maximum': 1024}},
            ['task', 'prompt'], False))
        result[-1]['annotations'].update(destructiveHint=False, idempotentHint=False)
    if local_models:
        profile = {'type': 'string', 'enum': ['qwen-coder-30b', 'gpt-oss-120b', 'qwen-coder-next']}
        result.append(tool('aceis_local_model_status', 'Read fixed model availability. Never load or generate.',
                           {'profile': profile}, ['profile']))
        result.append(tool('aceis_local_model_generate', 'Generate UNVERIFIED advice from an explicitly selected loaded model. No autoload or fallback.',
            {'profile': profile, 'task': {'type': 'string', 'enum': ['draft', 'review']},
             'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 16384},
             'max_tokens': {'type': 'integer', 'minimum': 1, 'maximum': 1024}}, ['profile', 'task', 'prompt'], False))
        result[-1]['annotations'].update(destructiveHint=False, idempotentHint=False)
        result.append(tool('aceis_local_model_prepare', 'Explicitly unload/load one approved profile within the existing router. Temporarily interrupts other profiles; no Docker or commands.',
                           {'profile': profile}, ['profile'], False))
        result[-1]['annotations'].update(destructiveHint=False, idempotentHint=False)
    if chat_mailbox:
        common = {'task_id': identifier, 'request_id': identifier,
                  'request_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}
        required = ['task_id', 'request_id', 'request_sha256']
        body = {'type': 'string', 'minLength': 1, 'maxLength': 4096}
        definitions = [
            (MAILBOX_TOOLS[0], 'Persist one fixed, non-secret question. No Chat/task activation. Same content is idempotent.',
             {**common, 'request': body}, required + ['request'], False),
            (MAILBOX_TOOLS[1], 'Read one allowlisted question and hash. IDs/hashes do not prove Chat identity.', common, required, True),
            (MAILBOX_TOOLS[2], 'Save one non-secret answer bound to the immutable question hash. Reject conflicting content.',
             {**common, 'answer': body, 'answer_sha256': common['request_sha256']}, required + ['answer'], False),
            (MAILBOX_TOOLS[3], 'Read/reconcile persisted answer after missing acknowledgement; never blindly retry UNKNOWN.',
             {**common, 'expected_answer_sha256': common['request_sha256']}, required, True)]
        for name, description, properties, needed, read in definitions:
            result.append(tool(name, description, properties, needed, read))
            result[-1]['annotations'].update(destructiveHint=False, idempotentHint=True)
    if writes:
        result += [
            tool('orchestrator_submit_task', 'Queue one task under the operator-fixed workspace and acceptance policy. Reuse request_key after uncertain delivery.', {
                'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 4096},
                'request_key': {'type': 'string', 'pattern': KEY_PATTERN, 'maxLength': 128}}, ['prompt', 'request_key'], False),
            tool('orchestrator_cancel_task', 'Request cancellation of one task. Does not roll back side effects.',
                 {'task_id': identifier}, ['task_id'], False)]
    if codex_pipeline:
        for definition in pipeline_tools():
            definition['outputSchema'] = {'type': 'object', 'properties': {
                'call_id': identifier, 'data': {'type': 'object'}},
                'required': ['call_id', 'data'], 'additionalProperties': False}
            result.append(definition)
    if experiments:
        for definition in experiment_tools():
            definition['outputSchema'] = {'type': 'object', 'properties': {
                'call_id': identifier, 'data': {'type': 'object'}},
                'required': ['call_id', 'data'], 'additionalProperties': False}
            result.append(definition)
    return result


class Adapter:
    def __init__(self, root, allow_writes=False, *, allow_codex_pipeline=False, pipeline=None,
                 events_service=None, principal_provider=None, notification_runtime=None,
                 shared_tool_adapter=None, allow_shared_tools_http=False,
                 shared_tools_principal=None, runtime_project_root=None):
        self.runtime_project_root = resolve_project_root(runtime_project_root)
        self.root = runtime_path(root, project_root=self.runtime_project_root)
        if pipeline is not None and not allow_codex_pipeline:
            raise ValueError('Pipeline activation is explicit')
        if allow_codex_pipeline and pipeline is None:
            raise ValueError('Pipeline executor must be explicitly connected')
        if notification_runtime is not None:
            if events_service is not None or principal_provider is not None:
                raise ValueError('Use either notification_runtime or direct Events dependencies')
            options = getattr(notification_runtime, 'adapter_options', None)
            if not callable(options):
                raise ValueError('Invalid notification runtime')
            injected = options()
            if not isinstance(injected, dict) or set(injected) - {'events_service', 'principal_provider'}:
                raise ValueError('Invalid notification runtime options')
            events_service = injected.get('events_service')
            principal_provider = injected.get('principal_provider')
            runtime_root = getattr(notification_runtime, 'runtime_root', self.root)
            if Path(runtime_root).absolute() != self.root:
                raise ValueError('Notification runtime must use the MCP runtime')
        if events_service is None and principal_provider is not None:
            raise ValueError('MCP Events principal provider requires an Events service')
        if principal_provider is not None and not callable(principal_provider):
            raise ValueError('MCP Events principal provider must be callable')
        self.events_service = events_service
        self.notification_runtime = notification_runtime
        self.events_enabled = (events_service is not None
                               and getattr(events_service, 'enabled', True) is True)
        # This provider is server-side and derives only the local process UID.
        # It is not identity for callers forwarded through a tunnel.
        self.principal_provider = principal_provider
        self.pipeline = pipeline
        if shared_tool_adapter is not None:
            shared_tools = getattr(shared_tool_adapter, 'tools', None)
            dispatch = getattr(shared_tool_adapter, 'call_tool', None)
            if (not isinstance(shared_tools, list) or not callable(dispatch)
                    or any(not isinstance(tool, dict) or not isinstance(tool.get('name'), str)
                           for tool in shared_tools)):
                raise ValueError('Invalid existing MCP tool adapter')
            shared_names = [tool['name'] for tool in shared_tools]
            if len(set(shared_names)) != len(shared_names) or any(name.startswith('codex_') for name in shared_names):
                raise ValueError('Existing tool surface overlaps the owner-scoped Codex surface')
            codex_tools = [tool for tool in tool_definitions(
                               writes=allow_writes, codex_pipeline=allow_codex_pipeline)
                           if tool.get('name', '').startswith('codex_')]
            self.tools = [*shared_tools, *codex_tools]
        else:
            self.tools = tool_definitions(writes=allow_writes,
                                          codex_pipeline=allow_codex_pipeline)
        self.shared_tool_adapter = shared_tool_adapter
        self.shared_tool_names = ({tool['name'] for tool in shared_tool_adapter.tools}
                                  if shared_tool_adapter is not None else set())
        # Legacy shared state is exposed over HTTP only after an explicit
        # single-principal OAuth or local-UID mode selected by main(). A local
        # UID is safe only when the HTTP endpoint is not shared through a tunnel.
        self.allow_shared_tools_http = allow_shared_tools_http is True
        if (self.allow_shared_tools_http
                and (shared_tool_adapter is None
                     or not isinstance(shared_tools_principal,
                                       (OAuthPrincipal, LocalPrincipal)))):
            raise ValueError('Shared HTTP tools require an explicit single principal')
        self.shared_tools_principal = shared_tools_principal
        self.policy = None
        if allow_writes:
            path = self.root / 'mcp-task-policy.json'
            s = path.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_size > 16384:
                raise ValueError('Task policy must be a private regular file, at most 16 KiB')
            policy = decode(path.read_bytes())
            fields(policy, ['workspace', 'acceptance', 'timeout'], ['workspace', 'acceptance', 'timeout'])
            fields(policy['workspace'], ['fixture', 'access', 'role'])
            workspace = spec(**policy['workspace'])
            if workspace['role'] != 'implementer': raise ValueError('Initial adapter supports implementer policies only')
            normalize_acceptance(policy['acceptance'])  # Same approved command registry; daemon revalidates.
            if type(policy['timeout']) not in [int, float] or not math.isfinite(policy['timeout']) or not 0 < policy['timeout'] <= 3600:
                raise ValueError('Invalid fixed task timeout')
            self.policy = policy
        self.session = str(uuid.uuid4())
        self.audit_path = self.root / 'logs' / ('mcp-' + self.session + '.jsonl')
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.audit = os.fdopen(fd, 'w')
        self.initialized = False
        self.ready = False

    def record(self, event, **facts):
        line = json.dumps({'time': time.time(), 'session_id': self.session, 'event': event, **facts}) + '\n'
        if self.audit.tell() + len(line.encode()) > AUDIT_LIMIT: raise OSError('Audit capacity reached')
        self.audit.write(line); self.audit.flush(); os.fsync(self.audit.fileno())

    def daemon(self, method, params):
        runtime_path(self.root, project_root=self.runtime_project_root)
        return request(self.root, method, params, timeout=TIMEOUT)

    def _require_owned_pipeline_resource(self, principal, resource_type, resource_id):
        runtime = self.notification_runtime
        if (not isinstance(principal, OAuthPrincipal) or not self.events_enabled
                or runtime is None or getattr(runtime, 'outbox', None) is None):
            raise Invalid('Authenticated task ACL unavailable')
        if resource_type == 'task':
            owned = runtime.outbox.task_owned_by(resource_id, principal)
        else:
            owned = runtime.outbox.thread_owned_by(resource_id, principal)
        if not owned:
            # Do not reveal whether another principal's ID exists.
            raise Invalid('Resource not available')

    def _owner_scoped_thread_listing(self, principal, arguments):
        runtime = self.notification_runtime
        if (not isinstance(principal, OAuthPrincipal) or not self.events_enabled
                or runtime is None or getattr(runtime, 'outbox', None) is None):
            raise Invalid('Authenticated task ACL unavailable')
        records = []
        cursor = None
        for _page_number in range(4):
            page_args = {'limit': 20}
            if cursor is not None:
                page_args['cursor'] = cursor
            page = self.pipeline.dispatch('codex_threads_list', page_args)
            if not isinstance(page, dict):
                raise Invalid('Invalid pipeline listing')
            for thread in page.get('threads', []):
                if (isinstance(thread, dict) and runtime.outbox.thread_owned_by(
                        thread.get('id'), principal)):
                    records.append({'kind': 'thread', 'data': thread})
            for task in page.get('tasks', []):
                if (isinstance(task, dict) and runtime.outbox.task_owned_by(
                        task.get('id'), principal)):
                    records.append({'kind': 'task', 'data': task})
            cursor = page.get('next_cursor')
            if cursor is None:
                break
        else:
            raise Invalid('Owner listing exceeds bounded page count')
        snapshot = hashlib.sha256(json.dumps(records, sort_keys=True,
            ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        offset = 0
        supplied = arguments.get('cursor')
        if supplied is not None:
            match = re.fullmatch(r'([a-f0-9]{64}):([0-9]{1,6})', supplied)
            if not match or match.group(1) != snapshot:
                raise Invalid('Owner listing changed')
            offset = int(match.group(2))
            if offset > len(records):
                raise Invalid('Invalid owner listing cursor')
        limit = arguments.get('limit', 20)
        page_records = records[offset:offset + limit]
        next_offset = offset + len(page_records)
        return {'threads': [item['data'] for item in page_records if item['kind'] == 'thread'],
                'tasks': [item['data'] for item in page_records if item['kind'] == 'task'],
                'total_records': len(records),
                'next_cursor': f'{snapshot}:{next_offset}' if next_offset < len(records) else None}

    def _owner_scoped_events(self, principal, arguments):
        runtime = self.notification_runtime
        if (not isinstance(principal, OAuthPrincipal) or not self.events_enabled
                or runtime is None or getattr(runtime, 'outbox', None) is None):
            raise Invalid('Authenticated task ACL unavailable')
        try:
            return runtime.outbox.source_events_for_owner(
                principal, after=arguments.get('after', 0), limit=arguments.get('limit', 20))
        except NotificationError:
            raise Invalid('Event history unavailable') from None

    def call_tool(self, name, arguments, rpc_id, request_principal=None):
        if name not in {t['name'] for t in self.tools}: raise Invalid('Tool is not enabled')
        if name in self.shared_tool_names:
            if request_principal is not None:
                if (not self.allow_shared_tools_http
                        or not isinstance(request_principal,
                                          (OAuthPrincipal, LocalPrincipal))
                        or not isinstance(self.shared_tools_principal,
                                          type(request_principal))
                        or request_principal.identity != self.shared_tools_principal.identity):
                    raise Invalid('Shared tool surface is not authorized for HTTP')
            try:
                return self.shared_tool_adapter.call_tool(name, arguments, rpc_id)
            except ExistingToolInvalid:
                raise Invalid('Invalid existing tool arguments') from None
        if name == 'orchestrator_status': fields(arguments, [])
        elif name == 'orchestrator_list_tasks':
            fields(arguments, ['limit', 'offset'])
            for k, default, low, high in [('limit', 20, 1, 50), ('offset', 0, 0, 1000000)]:
                v = arguments.get(k, default)
                if type(v) is not int or not low <= v <= high: raise Invalid('Invalid list bound')
        elif name in ['orchestrator_get_task', 'orchestrator_cancel_task']:
            fields(arguments, ['task_id'], ['task_id'])
            if not uuid_value(arguments['task_id']): raise Invalid('Invalid task ID')
        elif name in PIPELINE_TOOLS:
            try: pipeline_arguments(name, arguments)
            except PipelineError as exc: raise Invalid(exc.code) from None
        else:
            fields(arguments, ['prompt', 'request_key'], ['prompt', 'request_key'])
            prompt, key = arguments['prompt'], arguments['request_key']
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4096 or len(prompt.encode()) > 16384:
                raise Invalid('Invalid prompt length')
            if not isinstance(key, str) or not re.fullmatch(KEY_PATTERN, key): raise Invalid('Invalid request_key')
        call_id = str(uuid.uuid4())
        facts = {'call_id': call_id, 'tool': name,
                 'rpc_id_hash': hashlib.sha256(json.dumps(rpc_id).encode()).hexdigest()}
        if 'task_id' in arguments: facts['task_id'] = arguments['task_id']
        if 'request_key' in arguments: facts['request_key_hash'] = hashlib.sha256(arguments['request_key'].encode()).hexdigest()
        # Reserve space for an end record before any side effect. Never run if audit cannot be persisted.
        if self.audit.tell() > AUDIT_LIMIT - 4096: raise OSError('Audit capacity reached')
        self.record('begin', **facts)
        try:
            if name in PIPELINE_TOOLS:
                if isinstance(request_principal, OAuthPrincipal):
                    if name == 'codex_pipeline_tick':
                        raise Invalid('Shared pipeline tick is unavailable to per-user callers')
                    if name in ('codex_thread_start', 'codex_threads_list', 'codex_events',
                                'codex_capabilities'):
                        if (not self.events_enabled or self.notification_runtime is None
                                or getattr(self.notification_runtime, 'outbox', None) is None):
                            raise Invalid('Authenticated task ACL unavailable')
                    elif name in ('codex_chat_store', 'codex_task_submit', 'codex_chat_steer',
                                  'codex_thread_archive'):
                        self._require_owned_pipeline_resource(
                            request_principal, 'thread', arguments['thread_id'])
                    elif name in ('codex_chat_queue', 'codex_task_get', 'codex_task_cancel'):
                        self._require_owned_pipeline_resource(
                            request_principal, 'task', arguments['task_id'])
                    if name == 'codex_threads_list':
                        data = self._owner_scoped_thread_listing(request_principal, arguments)
                        self.record('end', outcome='returned', **facts)
                        content = {'call_id': call_id, 'data': data}
                        return {'content': [{'type': 'text', 'text': json.dumps(content)}],
                                'structuredContent': content, 'isError': False}
                    if name == 'codex_events':
                        data = self._owner_scoped_events(request_principal, arguments)
                        self.record('end', outcome='returned', **facts)
                        content = {'call_id': call_id, 'data': data}
                        return {'content': [{'type': 'text', 'text': json.dumps(content)}],
                                'structuredContent': content, 'isError': False}
                principal = None
                if self.events_enabled:
                    try:
                        principal = request_principal
                        if principal is None and self.principal_provider is not None:
                            principal = self.principal_provider()
                        if not isinstance(principal, (LocalPrincipal, OAuthPrincipal)):
                            principal = None
                    except Exception:
                        principal = None
                try:
                    data = self.pipeline.dispatch(name, arguments)
                    failed = (data.get('status') in ('UNKNOWN', 'FAILED', 'BLOCKED')
                              or data.get('state') in ('UNKNOWN', 'BLOCKED'))
                except PipelineError as exc:
                    data, failed = exc.result(), True
                if (isinstance(request_principal, OAuthPrincipal) and not failed
                        and self.events_enabled and isinstance(data, dict)):
                    if name == 'codex_thread_start':
                        thread_id = data.get('thread_id')
                        if thread_id:
                            try:
                                self.events_service.bind_thread_owner(thread_id, request_principal)
                                data['access_binding'] = 'BOUND'
                            except Exception:
                                data['access_binding'] = 'UNAVAILABLE'
                    elif name in ('codex_task_submit', 'codex_chat_store'):
                        task_id = data.get('task_id') or data.get('id')
                        if task_id:
                            try:
                                self.events_service.bind_task_owner(task_id, request_principal)
                                data['access_binding'] = 'BOUND'
                            except Exception:
                                # The operation may already have committed; never replay it here.
                                data['access_binding'] = 'UNAVAILABLE'
                if (name in ('codex_task_submit', 'codex_chat_store')
                        and self.events_enabled and not failed
                        and isinstance(data, dict)):
                    task_id = data.get('task_id') or data.get('id')
                    if task_id:
                        if isinstance(request_principal, OAuthPrincipal):
                            data['notification_binding'] = ('BOUND' if data.get('access_binding') == 'BOUND'
                                                             else 'UNAVAILABLE')
                        elif principal is None:
                            data['notification_binding'] = 'UNAVAILABLE'
                        else:
                            try:
                                self.events_service.bind_task_owner(task_id, principal)
                                data['notification_binding'] = 'BOUND'
                            except Exception:
                                data['notification_binding'] = 'UNAVAILABLE'
                self.record('end', outcome='error_or_uncertain' if failed else 'returned', **facts)
                content = {'call_id': call_id, 'data': data}
                return {'content': [{'type': 'text', 'text': json.dumps(content)}],
                        'structuredContent': content, 'isError': failed}
            if name == 'orchestrator_status':
                raw = self.daemon('status', {})
                data = {'paused': raw.get('paused') is True, 'stopping': raw.get('stopping') is True,
                        'maximum_sessions': 2, 'blocked_count': len(raw.get('blocked', [])),
                        'pending_recovery_count': len(raw.get('pending_recovery', [])),
                        'active_task_ids': [v for v in raw.get('active_tasks', []) if uuid_value(v)],
                        'task_counts': {k: v for k, v in raw.get('task_counts', {}).items()
                                        if k in STATES and type(v) is int and 0 <= v <= 1000000000}}
            elif name == 'orchestrator_list_tasks':
                raw = self.daemon('list', {'limit': arguments.get('limit', 20), 'offset': arguments.get('offset', 0)})
                if not isinstance(raw, list) or len(raw) > arguments.get('limit', 20): raise ValueError('Invalid list')
                data = {'tasks': [task_view(t) for t in raw]}
            elif name == 'orchestrator_submit_task':
                raw = self.daemon('submit', {**arguments, **self.policy})
                if not uuid_value(raw.get('task_id')): raise ValueError('Invalid submit result')
                data = {'task_id': raw['task_id'], 'accepted': True}
            else:
                raw = self.daemon('cancel' if name == 'orchestrator_cancel_task' else 'status', arguments)
                data = task_view(raw)
                if data['task_id'] != arguments['task_id']: raise ValueError('Unexpected task ID')
            if 'task_id' in data: facts['task_id'] = data['task_id']
            if data.get('active_execution_id'): facts['execution_id'] = data['active_execution_id']
            self.record('end', outcome='returned', **facts)
            content = {'call_id': call_id, 'data': data}
            return {'content': [{'type': 'text', 'text': json.dumps(content)}], 'structuredContent': content, 'isError': False}
        except Invalid:
            self.record('end', outcome='denied', **facts)
            body = {'call_id': call_id, 'code': 'NOT_AUTHORIZED',
                    'retry_same_request_key': False}
            content = {'call_id': call_id, 'data': body}
            return {'content': [{'type': 'text', 'text': json.dumps(content)}],
                    'structuredContent': content, 'isError': True}
        except (OSError, EOFError, ValueError, RuntimeError, TypeError, KeyError):
            self.record('end', outcome='error_or_uncertain', **facts)
            # No daemon exception text, task content or local configuration crosses this boundary.
            body = {'call_id': call_id, 'code': 'DAEMON_ERROR_OR_UNCERTAIN',
                    'retry_same_request_key': name == 'orchestrator_submit_task'}
            return {'content': [{'type': 'text', 'text': json.dumps(body)}], 'isError': True}

    def handle(self, message, *, principal=None):
        # Discovery failures need evidence too. Never record params, client metadata,
        # raw IDs or unknown method strings; tool calls retain their existing audit.
        method = message.get('method') if isinstance(message, dict) else None
        observed = isinstance(message, dict) and 'id' in message and method in (
            'initialize', 'server/discover', 'tools/list',
            'events/list', 'events/subscribe', 'events/unsubscribe')
        try:
            if observed: self.record('protocol_request', method=method)
            reply = self._handle(message, principal)
            if observed:
                code = reply.get('error', {}).get('code') if reply else None
                self.record('protocol_response', method=method, error_code=code)
            return reply
        except OSError:
            identifier = message.get('id') if isinstance(message, dict) else None
            if not (type(identifier) is int or isinstance(identifier, str) and len(identifier) <= 128): identifier = None
            return self.error(identifier, -32603, 'Local adapter audit unavailable')

    def _handle(self, message, request_principal=None):
        identifier = message.get('id') if isinstance(message, dict) else None
        if (not isinstance(message, dict) or message.get('jsonrpc') != '2.0'
                or not isinstance(message.get('method'), str)
                or set(message) - {'jsonrpc', 'id', 'method', 'params'}):
            return self.error(None, -32600, 'Invalid JSON-RPC request')
        if 'id' in message and not (type(identifier) is int or isinstance(identifier, str) and len(identifier) <= 128):
            return self.error(None, -32600, 'Invalid JSON-RPC request ID')
        try:
            fields(message, ['jsonrpc', 'id', 'method', 'params'], ['jsonrpc', 'method'])
            if message['jsonrpc'] != '2.0' or not isinstance(message['method'], str): raise Invalid('Invalid envelope')
            if 'id' in message and not ((type(identifier) is int) or (isinstance(identifier, str) and len(identifier) <= 128)):
                raise Invalid('Invalid request ID')
            params = message.get('params', {})
            if not isinstance(params, dict): raise Invalid('params must be an object')
            method = message['method']
            if 'id' not in message:
                if method == 'notifications/initialized' and self.initialized:
                    fields(params, ['_meta']); self.ready = True
                return None
            meta = params.get('_meta', {})
            if not isinstance(meta, dict): raise Invalid('Invalid metadata')
            modern = method != 'initialize' and (method == 'server/discover' or VERSION_META in meta or CAPABILITIES_META in meta)
            if modern:
                version = meta.get(VERSION_META)
                if (not isinstance(version, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', version)
                        or not isinstance(meta.get(CAPABILITIES_META), dict)):
                    raise Invalid('Missing per-request protocol metadata')
                if version != REQUEST_PROTOCOL:
                    reply = self.error(identifier, -32022, 'Unsupported protocol version')
                    reply['error']['data'] = {'supported': [REQUEST_PROTOCOL], 'requested': version}
                    return reply
                if CLIENT_META in meta:
                    info = meta[CLIENT_META]
                    if not isinstance(info, dict) or any(not isinstance(info.get(k), str) or len(info[k]) > 128 for k in ['name', 'version']):
                        raise Invalid('Invalid clientInfo')
            if method == 'initialize':
                if self.initialized: raise Invalid('Already initialized')
                fields(params, ['protocolVersion', 'capabilities', 'clientInfo', '_meta'], ['protocolVersion', 'capabilities', 'clientInfo'])
                if not isinstance(params['protocolVersion'], str) or not isinstance(params['capabilities'], dict): raise Invalid('Invalid negotiation')
                info = params['clientInfo']
                if not isinstance(info, dict) or any(not isinstance(info.get(k), str) or len(info[k]) > 128 for k in ['name', 'version']):
                    raise Invalid('Invalid clientInfo')
                self.initialized = True
                capabilities = {'tools': {'listChanged': False}}
                if self.events_enabled: capabilities['events'] = {}
                result = {'protocolVersion': PROTOCOL, 'capabilities': capabilities,
                          'serverInfo': dict(SERVER_INFO)}
            elif method == 'server/discover':
                fields(params, ['_meta'])
                capabilities = {'tools': {'listChanged': False}}
                if self.events_enabled: capabilities['events'] = {}
                result = {'supportedVersions': [REQUEST_PROTOCOL, PROTOCOL],
                          'capabilities': capabilities, 'ttlMs': 0, 'cacheScope': 'private'}
            elif method == 'ping': result = {}
            elif not modern and not self.ready: raise Invalid('Initialization must complete first')
            elif method == 'tools/list':
                fields(params, ['_meta', 'cursor'])
                # The bounded tool set fits one page; no continuation cursor exists.
                if params.get('cursor') is not None: raise Invalid('Unknown cursor')
                result = {'tools': self.tools}
                if modern: result.update(ttlMs=0, cacheScope='private')
            elif method == 'tools/call':
                fields(params, ['name', 'arguments', '_meta'], ['name'])
                result = self.call_tool(params['name'], params.get('arguments', {}), identifier,
                                        request_principal=request_principal)
            elif method in ('events/list', 'events/subscribe', 'events/unsubscribe'):
                if not self.events_enabled:
                    return self.error(identifier, -32601, 'Method not found')
                try:
                    principal = request_principal
                    if principal is None and self.principal_provider is not None:
                        principal = self.principal_provider()
                    if not isinstance(principal, (LocalPrincipal, OAuthPrincipal)):
                        raise NotificationError('AUTHENTICATED_PRINCIPAL_REQUIRED')
                    event_params = {key: value for key, value in params.items() if key != '_meta'}
                    result = self.events_service.handle(method, event_params, principal)
                except NotificationError as exc:
                    if exc.code.startswith(('CALLBACK_', 'VERIFICATION_')):
                        if ('TIMEOUT' in exc.code or 'DNS_FAILED' in exc.code
                                or 'VERIFIER_UNAVAILABLE' in exc.code or 'BOUNDED_RESOLVER_REQUIRED' in exc.code):
                            reason = 'timeout'
                        elif 'UNSAFE' in exc.code or 'INVALID_CALLBACK_URL' in exc.code:
                            reason = 'unsafe_destination'
                        else:
                            reason = 'challenge_failed'
                        return {'jsonrpc': '2.0', 'id': identifier, 'error': {
                            'code': -32015, 'message': 'CallbackEndpointError',
                            'data': {'reason': reason}}}
                    if (exc.code.startswith(('INVALID_', 'CURSOR_'))
                            or exc.code in {'UNSUPPORTED_EVENT', 'EVENT_CATALOG_CURSOR_UNSUPPORTED'}):
                        return {'jsonrpc': '2.0', 'id': identifier, 'error': {
                            'code': -32602, 'message': 'Invalid params',
                            'data': {'code': exc.code}}}
                    return {'jsonrpc': '2.0', 'id': identifier, 'error': {
                        'code': -32000, 'message': 'MCP Events request failed',
                        'data': {'code': exc.code}}}
                except Exception:
                    return {'jsonrpc': '2.0', 'id': identifier, 'error': {
                        'code': -32000, 'message': 'MCP Events request failed',
                        'data': {'code': 'EVENT_SERVICE_UNAVAILABLE'}}}
            else: return self.error(identifier, -32601, 'Method not found')
            if modern:
                # Modern metadata belongs to this request only, never to the legacy
                # connection state. Client capabilities cannot enable local writes.
                result = {**result, 'resultType': 'complete', '_meta': {SERVER_META: dict(SERVER_INFO)}}
            return {'jsonrpc': '2.0', 'id': identifier, 'result': result}
        except (Invalid, TypeError):
            if isinstance(message, dict) and 'id' not in message: return None
            valid_id = identifier if type(identifier) is int or isinstance(identifier, str) and len(identifier) <= 128 else None
            return self.error(valid_id, -32602, 'Invalid request parameters')
        except (OSError, ValueError, RuntimeError):
            return self.error(identifier, -32603, 'Local adapter unavailable; do not blindly repeat writes')

    @staticmethod
    def error(identifier, code, text):
        return {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': code, 'message': text}}


def serve(adapter):
    selector = selectors.DefaultSelector()
    os.set_blocking(sys.stdin.fileno(), False)
    os.set_blocking(sys.stdout.fileno(), False)
    selector.register(sys.stdin, selectors.EVENT_READ)
    buffer = bytearray()
    partial_since = None
    def emit(message):
        raw = json.dumps(message, ensure_ascii=False, allow_nan=False).encode() + b'\n'
        if len(raw) > MAX_OUTPUT: raw = json.dumps(adapter.error(message.get('id'), -32603, 'Result exceeds output bound')).encode() + b'\n'
        deadline = time.monotonic() + TIMEOUT
        while raw:
            try: count = os.write(sys.stdout.fileno(), raw); raw = raw[count:]
            except BlockingIOError:
                if time.monotonic() >= deadline: raise TimeoutError('Output deadline exceeded')
                time.sleep(.01)
    try:
        while True:
            if partial_since is not None and time.monotonic() - partial_since >= 3:
                emit(adapter.error(None, -32700, 'Incomplete input deadline exceeded')); return 1
            if not selector.select(.1): continue
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                if buffer: emit(adapter.error(None, -32700, 'Incomplete JSON frame'))
                return 0 if not buffer else 1
            if not buffer: partial_since = time.monotonic()
            buffer.extend(chunk)
            while b'\n' in buffer:
                line, _, rest = buffer.partition(b'\n'); buffer = bytearray(rest)
                partial_since = time.monotonic() if buffer else None
                if len(line) > MAX_INPUT:
                    emit(adapter.error(None, -32700, 'Input exceeds bound')); return 1
                try: message = decode(line)
                except (ValueError, UnicodeError, RecursionError):
                    emit(adapter.error(None, -32700, 'Invalid JSON')); continue
                if not isinstance(message, dict):
                    emit(adapter.error(None, -32600, 'Invalid JSON-RPC request')); continue
                reply = adapter.handle(message)
                if reply is not None: emit(reply)
                partial_since = time.monotonic() if buffer else None
            if len(buffer) > MAX_INPUT:
                emit(adapter.error(None, -32700, 'Input exceeds bound')); return 1
    except (OSError, TimeoutError): return 1
    finally:
        selector.close(); adapter.audit.close()
        shared = getattr(adapter, 'shared_tool_adapter', None)
        if shared is not None and getattr(shared, 'audit', None) is not None:
            if callable(getattr(shared, 'close', None)): shared.close()
            else: shared.audit.close()


def _require_single_principal_for_shared_tools(verifier):
    principals = getattr(verifier, 'principals', None)
    if (not isinstance(principals, tuple) or len(principals) != 1
            or not isinstance(principals[0], OAuthPrincipal)):
        raise ValueError('Shared HTTP tools require exactly one allowlisted OAuth principal')
    return principals[0]


def main():
    parser = argparse.ArgumentParser(description='Local MCP adapter; stdio by default')
    parser.add_argument('--runtime', type=Path, required=True, help='Operator-fixed existing private runtime')
    parser.add_argument('--runtime-project-root', type=Path,
                        help='Explicit trusted project root when this adapter worktree shares an existing runtime')
    parser.add_argument('--transport', choices=('stdio', 'http'), default='stdio',
                        help='Use stdio or the explicit loopback-only Streamable HTTP server')
    parser.add_argument('--http-port', type=int, default=8765,
                        help='Loopback HTTP port; used only with --transport http')
    parser.add_argument('--local-single-user-http', action='store_true',
                        help='Explicit unauthenticated HTTP mode for one trusted local user only; never expose through a shared Tunnel')
    parser.add_argument('--allow-writes', action='store_true', help='Local tests only; requires runtime/mcp-task-policy.json')
    parser.add_argument('--allow-existing-tools', action='store_true',
                        help='Expose the existing non-Codex tool surface; HTTP requires one explicit principal mode')
    parser.add_argument('--allow-local-llm', action='store_true',
                        help='Enable the separately configured bounded local LLM helper')
    parser.add_argument('--allow-local-models', action='store_true',
                        help='Enable fixed-profile local model tools')
    parser.add_argument('--allow-chat-mailbox', action='store_true',
                        help='Enable the fixed-slot non-secret mailbox tools')
    parser.add_argument('--allow-experiments', action='store_true',
                        help='Enable pinned read-only experiment projections')
    parser.add_argument('--allow-codex-pipeline', action='store_true',
                        help='Opt-in app-server executor; requires the existing daemon pipeline flag too')
    # Modified 2026-11-12: private, opt-in LAF Router composition. No auth,
    # listener, existing-tool or daemon configuration is widened by default.
    parser.add_argument('--allow-laf-router', action='store_true',
                        help='Compose the five owner-scoped LAF Router job tools')
    parser.add_argument('--laf-router-socket', type=Path)
    parser.add_argument('--laf-router-state', type=Path)
    parser.add_argument('--laf-router-model', action='append', default=None)
    args = parser.parse_args()
    os.umask(0o077)
    pipeline = None
    legacy_adapter = None
    shared_tools_principal = None
    cleanup_attempted = False
    adapter = None
    try:
        if args.allow_laf_router:
            if (args.transport != 'http' or not args.allow_existing_tools
                    or args.laf_router_socket is None or args.laf_router_state is None
                    or not args.laf_router_model):
                raise ValueError('Router composition requires explicit HTTP/shared/config opt-in')
        elif (args.laf_router_socket is not None or args.laf_router_state is not None
              or args.laf_router_model is not None):
            raise ValueError('Router options require explicit Router activation')
        if args.local_single_user_http and args.transport != 'http':
            raise ValueError('--local-single-user-http requires --transport http')
        if args.transport == 'http' and not args.allow_codex_pipeline:
            raise ValueError('HTTP mode requires the Codex task pipeline')
        if (args.runtime_project_root is not None
                and not (args.transport == 'http' and args.local_single_user_http)):
            raise ValueError('--runtime-project-root is restricted to explicit local single-user HTTP mode')
        runtime_project_root = resolve_project_root(args.runtime_project_root)
        runtime = runtime_path(args.runtime, project_root=runtime_project_root)
        if args.transport == 'http' and args.allow_writes:
            raise ValueError('HTTP mode does not expose legacy orchestrator write tools')
        if args.allow_existing_tools and args.transport != 'http':
            raise ValueError('The shared existing tool surface is available only through HTTP')
        oauth_verifier = None
        oauth_metadata = None
        oauth_metadata_uri = None
        if args.transport == 'http' and not args.local_single_user_http:
            from oauth_auth import OAuthJWTVerifier
            from urllib.parse import urlsplit, urlunsplit
            oauth_verifier = OAuthJWTVerifier.from_environment()
        elif args.transport == 'http' and args.local_single_user_http:
            shared_tools_principal = current_local_principal()
        shared_flags = (args.allow_local_llm or args.allow_local_models
                        or args.allow_chat_mailbox or args.allow_experiments)
        if shared_flags and not args.allow_existing_tools:
            raise ValueError('Existing tool flags require --allow-existing-tools')
        if args.allow_existing_tools:
            if args.transport == 'http' and not args.local_single_user_http:
                shared_tools_principal = _require_single_principal_for_shared_tools(oauth_verifier)
            from legacy_tool_adapter import Adapter as ExistingToolAdapter
            legacy_adapter = ExistingToolAdapter(
                runtime, allow_writes=False,
                allow_local_llm=args.allow_local_llm,
                allow_local_models=args.allow_local_models,
                allow_chat_mailbox=args.allow_chat_mailbox,
                allow_mailbox_grants=False, allow_codex_pipeline=False,
                pipeline=None, allow_experiments=args.allow_experiments,
                runtime_project_root=runtime_project_root)
        if args.transport == 'http' and not args.local_single_user_http:
            resource_url = os.environ.get('ACEIS_OAUTH_RESOURCE_URL', '')
            authorization_server = os.environ.get(
                'ACEIS_OAUTH_AUTHORIZATION_SERVER', oauth_verifier.issuer)
            parsed_resource = urlsplit(resource_url)
            if (parsed_resource.scheme != 'https' or not parsed_resource.hostname
                    or parsed_resource.username is not None or parsed_resource.password is not None
                    or parsed_resource.query or parsed_resource.fragment
                    or parsed_resource.path != '/mcp'):
                raise ValueError('OAuth resource URL must be the public HTTPS /mcp endpoint')
            oauth_metadata_uri = urlunsplit((parsed_resource.scheme, parsed_resource.netloc,
                                             '/.well-known/oauth-protected-resource', '', ''))
            oauth_metadata = oauth_verifier.protected_resource_metadata(
                resource_url=resource_url, authorization_server=authorization_server)
        from notification_runtime import build_systemd_notification_runtime
        notification_runtime = build_systemd_notification_runtime(
            runtime,
            events_transport=(args.transport == 'http'
                              and args.allow_codex_pipeline),
            authenticated_principals=(oauth_verifier.principals
                                      if oauth_verifier is not None else
                                      ((shared_tools_principal or current_local_principal()),)
                                      if args.local_single_user_http else None),
            local_single_user=args.local_single_user_http)
        if (args.transport == 'http' and args.allow_codex_pipeline
                and not notification_runtime.enabled and not args.local_single_user_http):
            raise ValueError('OAuth pipeline ACL or notification providers are unavailable')
        if args.allow_codex_pipeline:
            from codex_host import DaemonPipelineProxy
            pipeline = DaemonPipelineProxy(runtime)
        adapter_class = Adapter
        if args.allow_laf_router:
            from laf_shared_tools import (CompositeSharedTools, IntegratedAdapter,
                                          build_router_application)
            router_app = build_router_application(socket_path=args.laf_router_socket,
                state_directory=args.laf_router_state, models=args.laf_router_model)
            try:
                legacy_adapter = CompositeSharedTools(legacy_adapter, router_app,
                                                       server_info=SERVER_INFO)
            except Exception:
                router_app.receipts.close()
                raise
            adapter_class = IntegratedAdapter
        adapter = adapter_class(runtime, args.allow_writes,
                          allow_codex_pipeline=args.allow_codex_pipeline, pipeline=pipeline,
                          notification_runtime=notification_runtime,
                          shared_tool_adapter=legacy_adapter,
                          allow_shared_tools_http=(args.transport == 'http'
                                                   and args.allow_existing_tools),
                          shared_tools_principal=shared_tools_principal,
                          runtime_project_root=runtime_project_root)
        if args.transport == 'http':
            if legacy_adapter is None:
                # The legacy daemon task APIs have no per-HTTP-principal ACL.
                # Expose only Codex resources protected by the durable owner ACL.
                adapter.tools = [tool for tool in adapter.tools
                                 if tool.get('name', '').startswith('codex_')]
            from mcp_http_transport import serve_http
            options = dict(port=args.http_port, oauth_verifier=oauth_verifier,
                           oauth_metadata=oauth_metadata, oauth_metadata_uri=oauth_metadata_uri,
                           local_single_user=args.local_single_user_http)
            if args.allow_laf_router:
                from laf_shared_tools import run_integrated_http
                cleanup_attempted = True
                return run_integrated_http(adapter, serve_http, **options)
            try:
                return serve_http(adapter, **options)
            finally:
                adapter.audit.close()
                if legacy_adapter is not None: legacy_adapter.audit.close()
        return serve(adapter)
    except (OSError, ValueError, TypeError, sqlite3.Error, ImportError):
        if legacy_adapter is not None and not cleanup_attempted:
            if args.allow_laf_router and hasattr(legacy_adapter, 'app'):
                from laf_shared_tools import close_shared_adapter
                close_shared_adapter(legacy_adapter)
            elif not legacy_adapter.audit.closed: legacy_adapter.audit.close()
        print('Adapter unavailable: verify operator policy; reconcile known attempts before retry.', file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
