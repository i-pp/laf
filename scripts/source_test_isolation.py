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

# Added 2026-11-15: physically isolated source tests and fail-closed fixture access.

"""Fail-closed source tests in disposable Linux namespaces; no host state fallback.

The guarantee covers this runner's tests and descendants, not arbitrary Python
commands run by the same OS account. No production module imports this helper.
"""
import argparse
import errno
import gc
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import runpy
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = 'LAF_SOURCE_TEST_ISOLATION'
SIZE = 512 * 1024**2
NAMESPACES = ('mnt', 'pid', 'net', 'ipc', 'user')
COUNTS = {'blocked': 0, 'expected': 0}
INSTALLED = False
UNRAISABLE = []


class IsolationError(RuntimeError): pass
class FixtureAccessError(RuntimeError): pass


def namespace_ids():
    return {name: os.readlink('/proc/self/ns/' + name) for name in NAMESPACES}


def mounts(text):
    result = {}
    def decode(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    for line in text.splitlines():
        fields = line.split(); separator = fields.index('-')
        result[decode(fields[4])] = {'options': set(fields[5].split(',')),
            'type': fields[separator+1], 'super': set(fields[separator+3].split(','))}
    return result


def covering(table, path):
    matches = [p for p in table if path == p or path.startswith(p.rstrip('/') + '/')]
    if not matches: raise IsolationError('No covering mount')
    return max(matches, key=len), table[max(matches, key=len)]


def require_isolated():
    """Check kernel evidence before any test fixture or project import."""
    try:
        receipt = json.loads(os.environ.get(RECEIPT, '{}'))
        if set(receipt) != {'uid', 'home', 'namespaces', 'guard_identity'}:
            raise IsolationError('Use scripts/source_test_isolation.py; no unwrapped resource tests')
        uid = os.getuid(); home = pwd.getpwuid(uid).pw_dir
        if uid != receipt['uid'] or home != receipt['home'] or not home.startswith('/home/'):
            raise IsolationError('Different OS identity or unsupported home')
        actual = namespace_ids()
        if (set(receipt['namespaces']) != set(NAMESPACES)
                or any(not re.fullmatch(k+r':\[\d+\]',receipt['namespaces'][k]) or actual[k] == receipt['namespaces'][k] for k in NAMESPACES)):
            raise IsolationError('Private namespaces are required')
        table = mounts(Path('/proc/self/mountinfo').read_text())
        for target in ('/', home, '/tmp', '/run'):
            selected, info = covering(table, target)
            if selected != target or info['type'] != 'tmpfs' or 'rw' not in info['options']:
                raise IsolationError('Private writable tmpfs required')
            size = next((s for s in info['super'] if s.startswith('size=')), None)
            match=re.fullmatch(r'size=(\d+)([kKmMgG]?)',size or '')
            factors={'':1,'k':1024,'m':1024**2,'g':1024**3}
            if not match or not 0<int(match[1])*factors[match[2].lower()]<=SIZE:
                raise IsolationError('Explicit bounded tmpfs required')
        guard = home + '/.local/state/laf/resource-admission'
        for target in ('/source', guard):
            selected, info = covering(table, target)
            if selected != target or 'ro' not in info['options']:
                raise IsolationError('Read-only source/default placeholder required')
        if table.get('/proc', {}).get('type') != 'proc' or table.get('/dev', {}).get('type') != 'tmpfs':
            raise IsolationError('Fresh proc and minimal dev required')
        info = os.stat(guard, follow_symlinks=False)
        if [info.st_dev, info.st_ino] != receipt['guard_identity'] or os.listdir(guard):
            raise IsolationError('Different or populated default placeholder')
        if os.environ.get('HOME') != home + '/laf-test-home': raise IsolationError('Private fixture home required')
        if (not stat.S_ISCHR(os.fstat(0).st_mode) or os.fstat(0).st_rdev!=os.stat('/dev/null').st_rdev
                or any(not stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (1,2))):
            raise IsolationError('Private null input and output pipes required')
        return {'uid': uid, 'home': home, 'guard': guard, 'namespaces': actual}
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        raise IsolationError('Unverified source-test isolation') from error


def close_inherited_descriptors():
    for name in os.listdir('/proc/self/fd'):
        if name.isdecimal() and int(name) > 2:
            try: os.close(int(name))
            except OSError as error:
                if error.errno != errno.EBADF: raise


def _path(value, *, sqlite=False, follow_leaf=True):
    if isinstance(value, int):
        if sqlite: raise FixtureAccessError('SQLite descriptor alias refused')
        return None
    value = os.fsdecode(value)
    if sqlite and value in (':memory:', ''): return None
    if sqlite and value.startswith('file:'):
        parsed = urlsplit(value)
        if parsed.netloc or parsed.fragment: raise FixtureAccessError('SQLite URI authority refused')
        value = unquote(parsed.path)
        if '\x00' in value: raise FixtureAccessError('SQLite URI NUL refused')
    value=os.path.normpath(os.path.abspath(value))
    if value.startswith('//'):value='/'+value.lstrip('/')
    if value.startswith('/proc/') or value.startswith('/dev/fd/'):
        if sqlite: raise FixtureAccessError('SQLite descriptor alias refused')
    path=Path(value)
    return path.resolve() if follow_leaf else path.parent.resolve()/path.name


def install_audit_guard():
    global INSTALLED
    proof = require_isolated()
    if INSTALLED: return
    forbidden = Path(proof['guard'])
    allowed = (Path(proof['home']) / 'laf-test-home', Path('/tmp'))
    def check(value, *, sqlite=False, directory=None, follow_leaf=True):
        try:
            if directory is not None and not os.path.isabs(os.fsdecode(value)):
                value=os.path.join(os.readlink('/proc/self/fd/'+str(directory)),os.fsdecode(value))
            candidate = _path(value, sqlite=sqlite,follow_leaf=follow_leaf)
            if candidate is not None and (candidate == forbidden or candidate.is_relative_to(forbidden)):
                raise FixtureAccessError('Default ledger is forbidden in tests')
            if sqlite and candidate is not None and not any(candidate.is_relative_to(p) for p in allowed):
                raise FixtureAccessError('SQLite requires a private disposable fixture')
        except FixtureAccessError:
            COUNTS['blocked'] += 1
            raise
    def guard(event, args):
        sqlite = event == 'sqlite3.connect'
        indexes = (0,) if event in ('open', 'os.mkdir', 'os.remove', 'os.rmdir', 'sqlite3.connect') else (0, 1) if event in ('os.rename', 'os.link') else (1,) if event == 'os.symlink' else ()
        for index in indexes:
            value = args[index]
            if value is None: continue
            check(value,sqlite=sqlite,follow_leaf=event in ('open','sqlite3.connect','os.link'))
    # CPython's open audit event omits dir_fd. Guard Python APIs at their actual
    # descriptor-relative target as well, before the native operation.
    def wrap(original,fields,follow_leaf):
        def call(*args,**kwargs):
            for index,key,directory_key in fields:
                directory=kwargs.get(directory_key)
                value=args[index] if len(args)>index else kwargs.get(key)
                if directory is not None and value is not None:check(value,directory=directory,follow_leaf=follow_leaf)
            return original(*args,**kwargs)
        return call
    one=((0,'path','dir_fd'),)
    two=((0,'src','src_dir_fd'),(1,'dst','dst_dir_fd'))
    for name,fields in [('open',one),('mkdir',one),('unlink',one),('remove',one),('rmdir',one),
            ('rename',two),('replace',two),('link',two),('symlink',((1,'dst','dir_fd'),))]:
        setattr(os,name,wrap(getattr(os,name),fields,name in ('open','link')))
    sys.addaudithook(guard); INSTALLED = True


def expect_refusal(operation):
    before = COUNTS['blocked']
    try: operation()
    except FixtureAccessError:
        count = COUNTS['blocked'] - before
        if count != 1: raise AssertionError('Exactly one guard refusal required')
        COUNTS['expected'] += count
    else: raise AssertionError('Forbidden access unexpectedly succeeded')


def build_command(binary, scratch, guard_fd, suite, args, *, module=None, harness_fd=None, harness_sha=None, expected_tests=None):
    home = pwd.getpwuid(os.getuid()).pw_dir
    if not home.startswith('/home/') or Path(home).is_symlink(): raise IsolationError('Unsupported OS home')
    guard_info = os.fstat(guard_fd)
    receipt = {'uid': os.getuid(), 'home': home, 'namespaces': namespace_ids(),
               'guard_identity': [guard_info.st_dev, guard_info.st_ino]}
    command = [binary, '--die-with-parent', '--new-session', '--unshare-all', '--cap-drop', 'ALL',
        '--size', str(SIZE), '--perms', '0755', '--tmpfs', '/']
    for system in ('/usr','/etc','/sys','/bin','/sbin','/lib','/lib64'):
        if Path(system).exists(): command += ['--ro-bind',system,system]
    command += ['--proc','/proc','--dev','/dev','--clearenv']
    for destination in (home, '/tmp', '/run'):
        command += ['--size', str(SIZE), '--perms', '0700', '--tmpfs', destination]
    command += ['--perms', '0700', '--dir', home+'/laf-test-home',
        '--ro-bind', str(ROOT), '/source', '--ro-bind-fd', str(guard_fd), home+'/.local/state/laf/resource-admission',
        '--setenv', 'HOME', home+'/laf-test-home', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
        '--setenv', RECEIPT, json.dumps(receipt), '--chdir', '/source']
    if harness_fd is not None: command += ['--ro-bind-fd', str(harness_fd), '/harness.py']
    command += ['/usr/bin/python3', '-I', '-B', '-W', 'error::ResourceWarning', '/source/scripts/source_test_isolation.py', '--child', '--suite', suite]
    if module is not None: command += ['--module', module]
    if harness_sha is not None: command += ['--harness-sha256', harness_sha]
    if expected_tests is not None:command += ['--expected-tests',str(expected_tests)]
    return command + ['--', *args]


def bounded_capture(command, fds, timeout):
    """Drain both pipes within a finite memory/time budget; kill namespace on failure."""
    output={'stdout':bytearray(),'stderr':bytearray()};deadline=time.monotonic()+timeout
    with subprocess.Popen(command,pass_fds=fds,close_fds=True,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process, selectors.DefaultSelector() as ready:
        ready.register(process.stdout,selectors.EVENT_READ,'stdout');ready.register(process.stderr,selectors.EVENT_READ,'stderr')
        try:
            while ready.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
                for item,_ in ready.select(min(remaining,.5)):
                    block=os.read(item.fd,65536)
                    if not block:ready.unregister(item.fileobj);continue
                    if sum(map(len,output.values()))+len(block)>8*1024**2:raise IsolationError('Source-test output exceeds bound')
                    output[item.data].extend(block)
            code=process.wait(timeout=max(.001,deadline-time.monotonic()))
        except BaseException as error:
            process.kill();process.wait(timeout=5)
            if isinstance(error,subprocess.TimeoutExpired):
                error.output=bytes(output['stdout']);error.stderr=bytes(output['stderr'])
            raise
    return subprocess.CompletedProcess(command,code,bytes(output['stdout']),bytes(output['stderr']))


def launch(suite, args=(), *, module=None, harness_file=None, harness_sha=None, timeout=600, expected_tests=None):
    binary = shutil.which('bwrap')
    if binary is None: raise IsolationError('Bubblewrap unavailable; no unisolated fallback')
    if module is not None and not re.fullmatch('test_[a-z0-9_]+', module): raise IsolationError('Invalid test module')
    if (harness_file is None) != (harness_sha is None): raise IsolationError('Exact harness identity required')
    if (harness_file is not None) != (suite=='harness'): raise IsolationError('Explicit harness suite required')
    if type(timeout) is not int or not 1<=timeout<=600: raise IsolationError('Finite test timeout required')
    if expected_tests is not None and (suite!='focused' or type(expected_tests) is not int or not 0<expected_tests<=10000):raise IsolationError('Finite focused inventory required')
    with tempfile.TemporaryDirectory(prefix='laf-source-isolation-',dir='/tmp') as temporary:
        scratch = Path(temporary); guard = scratch/'empty-default'; guard.mkdir(mode=0o700)
        guard_fd = os.open(guard, os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC); harness_fd = None
        try:
            if harness_file is not None:
                requested=Path(harness_file)
                allowed=(Path('/tmp'),ROOT/'scripts')
                if (not requested.is_absolute() or requested.suffix!='.py'
                        or not any(requested.is_relative_to(p) for p in allowed)):
                    raise IsolationError('Review harness must be a private Python test file')
                resolved=requested.resolve()
                if not any(resolved.is_relative_to(p) for p in allowed): raise IsolationError('Review harness path escaped')
                before=requested.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_uid!=os.getuid() or before.st_mode&0o022:
                    raise IsolationError('Unsafe review harness identity')
                harness_fd = os.open(harness_file, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
                info = os.fstat(harness_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 1024**2 or (info.st_dev,info.st_ino)!=(before.st_dev,before.st_ino) or info.st_nlink!=1:
                    raise IsolationError('Unsafe review harness')
                with os.fdopen(os.dup(harness_fd),'rb') as stream: digest=hashlib.sha256(stream.read()).hexdigest()
                if digest != harness_sha: raise IsolationError('Changed review harness')
            command = build_command(binary,scratch,guard_fd,suite,args,module=module,harness_fd=harness_fd,harness_sha=harness_sha,expected_tests=expected_tests)
            fds = (guard_fd,) if harness_fd is None else (guard_fd,harness_fd)
            try:
                result = bounded_capture(command,fds,timeout)
            except subprocess.TimeoutExpired as error:
                sys.stdout.buffer.write(error.stdout or b'');sys.stderr.buffer.write(error.stderr or b'')
                raise
            if len(result.stdout)+len(result.stderr)>8*1024**2: raise IsolationError('Source-test output exceeds bound')
            sys.stdout.buffer.write(result.stdout);sys.stderr.buffer.write(result.stderr)
            return result.returncode
        finally:
            os.close(guard_fd)
            if harness_fd is not None: os.close(harness_fd)


def entrypoint(suite, main):
    if RECEIPT in os.environ:
        if INSTALLED:
            require_isolated();return main()
        bootstrap()
        try:code=main()
        except SystemExit as result:code=result.code
        gc.collect();return finish(code)
    return launch(suite, sys.argv[1:])


def finish(code):
    if COUNTS['blocked'] != COUNTS['expected'] or UNRAISABLE:
        print('SOURCE_TEST_BOUNDARY_FAILURE '+json.dumps({'access':COUNTS,'unraisable':UNRAISABLE}),file=sys.stderr)
        code=2
    print('SOURCE_TEST_GUARD '+json.dumps(COUNTS),file=sys.stderr)
    return code


def bootstrap():
    close_inherited_descriptors()
    proof = require_isolated()
    # This probe is ONLY on the already proven RO empty private mount.
    try:
        fd = os.open(proof['guard']+'/namespace-write-probe',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except OSError as error:
        if error.errno != errno.EROFS: raise IsolationError('RO placeholder probe inconclusive') from error
    else:
        os.close(fd);raise IsolationError('Private default placeholder was writable')
    install_audit_guard()
    warnings.simplefilter('error',ResourceWarning)
    def unraisable(error,_write=os.write,_exit=os._exit):
        # Capture primitives so interpreter teardown cannot turn a late global
        # connection warning into a successful exit after finish() returned.
        try:_write(2,b'SOURCE_TEST_UNRAISABLE_FATAL\n')
        finally:_exit(2)
    sys.unraisablehook=unraisable
    print('SOURCE_TEST_ISOLATION '+json.dumps({'verified':True,'default_host_home_hidden':True,'default_placeholder_readonly':True,'network_namespace_private':True}),file=sys.stderr)
    return proof


def child(args, remaining):
    bootstrap()
    sys.path[:0] = [str(ROOT/'scripts'),str(ROOT)]
    if args.suite == 'focused':
        if not args.module or not re.fullmatch('test_[a-z0-9_]+',args.module): raise IsolationError('Explicit test module required')
        suite = unittest.defaultTestLoader.loadTestsFromName(args.module)
        selected=suite.countTestCases()
        if selected == 0 or args.expected_tests is not None and selected!=args.expected_tests: raise IsolationError('Unexpected focused inventory')
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        good=result.wasSuccessful() and result.testsRun==selected and not any(getattr(result,k) for k in ('failures','errors','skipped','expectedFailures','unexpectedSuccesses'))
        code=0 if good else 1
    elif args.suite == 'harness':
        if not args.harness_sha256 or hashlib.sha256(Path('/harness.py').read_bytes()).hexdigest()!=args.harness_sha256:
            raise IsolationError('Review harness identity mismatch')
        sys.argv=['/harness.py',*remaining]
        try:runpy.run_path('/harness.py',run_name='__main__');code=0
        except SystemExit as result:code=result.code
    else:
        paths={'integrated':'scripts/run_integrated_source_checks.py','root':'scripts/check-laf.py',
            'bundle':'runtime/public_laf/scripts/check-laf.py','packaging':'packaging/run_packaging_checks.py'}
        script=ROOT/paths[args.suite];sys.argv=[str(script),*remaining]
        try:runpy.run_path(str(script),run_name='__main__');code=0
        except SystemExit as result:code=result.code
    gc.collect()
    return finish(code)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=('integrated','root','bundle','packaging','focused','harness'),required=True)
    parser.add_argument('--module');parser.add_argument('--harness-file',type=Path);parser.add_argument('--harness-sha256')
    parser.add_argument('--timeout-seconds',type=int,default=600)
    parser.add_argument('--expected-tests',type=int)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args,remaining=parser.parse_known_args();remaining=remaining[1:] if remaining[:1]==['--'] else remaining
    try:
        if args.child:return child(args,remaining)
        return launch(args.suite,remaining,module=args.module,harness_file=args.harness_file,harness_sha=args.harness_sha256,timeout=args.timeout_seconds,expected_tests=args.expected_tests)
    except (IsolationError,OSError,ValueError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'status':'refused','code':'source_test_isolation_required','error_type':type(error).__name__}),file=sys.stderr);return 2


if __name__ == '__main__':
    sys.modules['source_test_isolation']=sys.modules[__name__]
    raise SystemExit(main())
