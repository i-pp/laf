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

"""Small pure-function grammar, not a sandbox for arbitrary Python."""
import ast
import json
import math

BUILTINS = {'min', 'max', 'abs', 'len', 'round', 'sum', 'all', 'any', 'sorted'}
NODES = {ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
         ast.Assign, ast.If, ast.Pass, ast.Name, ast.Load, ast.Store,
         ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
         ast.IfExp, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Subscript,
         ast.Slice, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div,
         ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd, ast.Not,
         ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
         ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot}


def identifier(name):
    if not isinstance(name, str) or not name.isidentifier() or name.startswith('_'):
        raise ValueError('private or invalid identifier')


def canonical_source(source, original=None):
    if not isinstance(source, str) or not source.strip() or len(source.encode('utf-8')) > 8192:
        raise ValueError('source must be 1..8192 UTF-8 bytes')
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('invalid Python source') from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 512 or not 1 <= len(tree.body) <= 8:
        raise ValueError('source AST exceeds bound')
    if any(type(n) not in NODES for n in nodes):
        raise ValueError('only bounded pure functions, arithmetic, if, assignments and return are supported')
    if any(not isinstance(n, ast.FunctionDef) for n in tree.body):
        raise ValueError('module must contain functions only')
    signatures = {}
    for function in tree.body:
        identifier(function.name)
        args = function.args
        if (function.name in signatures or function.name in BUILTINS or function.decorator_list
                or function.returns or function.type_comment or getattr(function, 'type_params', [])
                or args.defaults or args.kw_defaults or args.vararg or args.kwarg
                or args.kwonlyargs or args.posonlyargs):
            raise ValueError('duplicate/reserved function or unsupported signature')
        parameters = tuple(a.arg for a in args.args)
        if len(parameters) > 8 or len(set(parameters)) != len(parameters):
            raise ValueError('invalid parameter count')
        for arg in args.args:
            identifier(arg.arg)
            if arg.annotation or arg.type_comment or arg.arg in BUILTINS:
                raise ValueError('annotations and builtin shadowing are unsupported')
        signatures[function.name] = parameters
        local = set(parameters)
        for node in ast.walk(function):
            if isinstance(node, ast.FunctionDef) and node is not function:
                raise ValueError('nested functions are unsupported')
            if isinstance(node, ast.Name):
                identifier(node.id)
                if isinstance(node.ctx, ast.Store):
                    if node.id in BUILTINS:
                        raise ValueError('builtin shadowing is unsupported')
                    local.add(node.id)
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or node.type_comment:
                    raise ValueError('assignment must target one local name')
            if isinstance(node, ast.Call):
                if (not isinstance(node.func, ast.Name) or node.func.id not in BUILTINS
                        or node.keywords or len(node.args) > 8):
                    raise ValueError('only allowlisted pure builtin calls are supported')
            if isinstance(node, ast.Constant):
                if type(node.value) not in {str, int, float, bool, type(None)}:
                    raise ValueError('unsupported constant')
                if isinstance(node.value, str) and len(node.value.encode()) > 1024:
                    raise ValueError('constant exceeds bound')
                if type(node.value) is int and node.value.bit_length() > 256:
                    raise ValueError('integer constant exceeds bound')
                if type(node.value) is float and not math.isfinite(node.value):
                    raise ValueError('nonfinite constant')
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in local | BUILTINS:
                raise ValueError('undefined or global name is unsupported')
    if original is not None:
        original_tree = ast.parse(canonical_source(original))
        old = {f.name: tuple(a.arg for a in f.args.args) for f in original_tree.body}
        if signatures != old:
            raise ValueError('function names and signatures must remain unchanged')
    # Never execute original bytes. Comments and encoding cookies cannot alter
    # the canonical UTF-8 program emitted from the validated AST.
    try:
        return ast.unparse(tree) + '\n'
    except RecursionError as exc:
        raise ValueError('source nesting exceeds bound') from exc


def validated_replacement(text, original):
    if not isinstance(text, str) or len(text.encode()) > 12288:
        raise ValueError('bounded text response required')
    stripped = text.strip()
    if stripped.startswith('```json\n') and stripped.endswith('\n```'):
        stripped = stripped[8:-4]
    value = json.loads(stripped)
    if (not isinstance(value, dict) or set(value) != {'replacement', 'explanation'}
            or not isinstance(value['explanation'], str)):
        raise ValueError('expected replacement/explanation JSON')
    return canonical_source(value['replacement'], original), value['explanation'][:1000]
