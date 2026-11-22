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

# Added 2026-11-20: enforce LAF-only naming on release and installation artifacts.

"""Check release files and operator-supplied installation names without installing."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


RETIRED = re.compile(br'ac[e]is', re.IGNORECASE)
MAX_ENTRIES = 4096
MAX_FILE = 32 * 1024**2
MAX_TOTAL = 64 * 1024**2
COMPONENT_NOTICE = '424870b9b1518dee73f42afd450dd711a3766b508e73a8da64dbdb28d267a464'
# Only these exact historical or attribution bytes may mention the retired name.
PRESERVED = {
    'NOTICE': {
        '5738f32d55e894347a946b1e3d61a5cfc49a508fa1733cd2249ab44fe1537893',
        COMPONENT_NOTICE,
        'c2c45b2812fca303d6cc994c763960a8a9d94d6b04fe97936ed7485ba9ca5af7',
    },
    'mcp/NOTICE': {COMPONENT_NOTICE},
    # Modified 2026-11-22: preserve the exact component notice in the local marketplace.
    'plugins/laf-local/mcp/NOTICE': {COMPONENT_NOTICE},
    'runtime/public_laf/NOTICE': {COMPONENT_NOTICE},
    'runtime/public_laf/mcp/NOTICE': {COMPONENT_NOTICE},
    'CHANGES.md': {'aa547c41d9a158c51f5d6e490cd6018a0fea1b252ae3698f6916e4ad1fb26518'},
    'runtime/public_laf/CHANGES.md': {'0e5eac9c141b2860512b3add9ea6bec8643c9eb361e8a1cd00bb2d9dbf80ce51'},
}


class NamingError(ValueError):
    pass


def check_name(name):
    if RETIRED.search(os.fsencode(name)):
        raise NamingError(f'Retired name in {name}')


def check_path(name):
    check_name(name)
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or {'..', '.git'} & set(path.parts) or '\\' in name or '\0' in name):
        raise NamingError(f'Invalid release path {name}')


def check_member(name, data):
    check_path(name)
    if len(data) > MAX_FILE:
        raise NamingError(f'Release member exceeds check limit: {name}')
    if name.lower().endswith(('.zip', '.whl', '.tar', '.tar.gz', '.tgz', '.tar.xz', '.tar.zst')):
        raise NamingError(f'Check an expanded copy of nested archive: {name}')
    if RETIRED.search(data):
        digests = PRESERVED.get(name, ())
        if re.fullmatch(r'laf_mcp-[^/]+\.dist-info/licenses/NOTICE', name):
            digests = (COMPONENT_NOTICE,)
        if sha256(data).hexdigest() not in digests:
            raise NamingError(f'Retired identifier in {name}')


def check_tree(root, *, names_only=False, checkout=False):
    if root.is_symlink() or not root.is_dir():
        raise NamingError('Expected an existing real directory')
    count = 0
    total = 0

    def walk_error(error):
        raise error

    for parent, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(parent)
        if checkout and parent == root:
            if '.git' in dirs:
                dirs.remove('.git')
            if '.git' in files:
                files.remove('.git')
        for label in sorted(dirs + files):
            path = parent / label
            name = path.relative_to(root).as_posix()
            check_path(name)
            count += 1
            if count > MAX_ENTRIES:
                raise NamingError('Release tree exceeds entry limit')
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if stat.S_ISLNK(info.st_mode) and names_only:
                check_name(os.readlink(path))
                continue
            if not stat.S_ISREG(info.st_mode):
                raise NamingError(f'Nonregular release member: {name}')
            if not names_only:
                total += info.st_size
                if total > MAX_TOTAL or info.st_size > MAX_FILE:
                    raise NamingError('Release tree exceeds byte limit')
                with path.open('rb') as stream:
                    check_member(name, stream.read(info.st_size + 1))
    if count == 0:
        raise NamingError('Empty release tree')
    return count


def check_zip(path):
    check_name(path.name)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TOTAL:
        raise NamingError('Expected a bounded, regular ZIP artifact')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_TOTAL:
            raise NamingError('ZIP inventory outside release check limit')
        if RETIRED.search(archive.comment):
            raise NamingError('Retired identifier in ZIP comment')
        seen = set()
        for entry in entries:
            name = entry.filename.removesuffix('/')
            check_path(name)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if (entry.orig_filename != entry.filename or name in seen
                    or kind not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG)
                    or entry.file_size > MAX_FILE):
                raise NamingError(f'Unsafe ZIP entry: {name}')
            if RETIRED.search(entry.comment):
                raise NamingError(f'Retired identifier in ZIP member comment: {name}')
            seen.add(name)
            if not entry.is_dir():
                check_member(name, archive.read(entry))
        return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, help='Release tree or ZIP. Use a fresh install prefix for names-only checks.')
    parser.add_argument('--names-only', action='store_true', help='Read directory names and symlink targets only')
    parser.add_argument('--checkout', action='store_true', help='Skip the root Git metadata directory or pointer')
    parser.add_argument('--name', action='append', default=[],
                        help='Also check a named unit, setting, tool, or registration. Repeat as needed.')
    args = parser.parse_args()
    try:
        check_name(args.target.name)
        for name in args.name:
            check_name(name)
        if args.target.is_dir():
            count = check_tree(args.target, names_only=args.names_only, checkout=args.checkout)
        elif args.names_only or args.checkout:
            raise NamingError('Directory scan options require a directory')
        else:
            count = check_zip(args.target)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(1, f'LAF release naming check failed: {error}\n')
    print(json.dumps({'status': 'passed', 'entries': count, 'explicit_names': len(args.name)}))


if __name__ == '__main__':
    main()
