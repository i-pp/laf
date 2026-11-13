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

# Scope: this newly authored file only; imported private source is not relicensed.
"""Private operator stdio entrypoint; no installer, daemon or provider lifecycle.

HTTP keeps its separately reviewed entrypoint/auth policy. Existing Codex/local
helper flags remain opt-in; Router5 has its own explicit owner-only configuration.
"""
import argparse,importlib.metadata,os,sys
from pathlib import Path
from types import SimpleNamespace
SUPPORTED_SDK='1.30.0'
class UsageError(ValueError):pass
class Parser(argparse.ArgumentParser):
 def error(self,message):raise UsageError('Invalid startup options')

def _components():
 from legacy_tool_adapter import runtime_path
 from runtime_scope import resolve_project_root
 from laf_stdio_adapter import StdioRouterAdapter
 from laf_shared_tools import build_router_application
 from laf_stdio_lifecycle import run_stdio
 def pipeline(runtime):
  from codex_host import DaemonPipelineProxy
  return DaemonPipelineProxy(runtime)
 return SimpleNamespace(resolve_project_root=resolve_project_root,runtime_path=runtime_path,pipeline=pipeline,router=build_router_application,adapter=StdioRouterAdapter,serve=run_stdio)

def main(argv=None):
 parser=Parser(description='Private LAF composed stdio frontend; does not install or switch services')
 parser.add_argument('--runtime',type=Path,required=True,help='Explicit existing private runtime')
 parser.add_argument('--runtime-project-root',type=Path,help='Explicit owner-controlled original runtime anchor; no guard bypass')
 for flag,help in (
  ('writes','Existing fixed-policy legacy write tools; default off'),
  ('local-llm','Existing bounded local helper; not silently repaired or translated'),
  ('local-models','Existing fixed-profile helper tools; not Router lifecycle authority'),
  ('chat-mailbox','Existing non-secret fixed-slot mailbox'),
  ('mailbox-grants','Existing finite mailbox grant; requires mailbox'),
  ('codex-pipeline','Existing daemon Codex pipeline; never start a daemon/account'),
  ('experiments','Existing pinned read-only experiment projection'),
  ('laf-router','Add five owner-scoped Router job tools; submit may start/switch providers')):
  parser.add_argument('--allow-'+flag,action='store_true',help=help)
 parser.add_argument('--laf-router-socket',type=Path)
 parser.add_argument('--laf-router-state',type=Path)
 parser.add_argument('--laf-router-model',action='append',default=None,help='Explicit allowed model ID; repeat for multiple IDs; no default or discovery-based enablement')
 try:args=parser.parse_args(argv)
 except UsageError:
  print('Invalid LAF stdio startup options; use --help. No runtime was opened.',file=sys.stderr);return 1
 if (args.allow_mailbox_grants and not args.allow_chat_mailbox) or (args.allow_laf_router and (args.laf_router_socket is None or args.laf_router_state is None or not args.laf_router_model)) or (not args.allow_laf_router and any(x is not None for x in (args.laf_router_socket,args.laf_router_state,args.laf_router_model))):
  print('Incomplete LAF stdio opt-in configuration; use --help. No runtime was opened.',file=sys.stderr);return 1
 try:version=importlib.metadata.version('mcp')
 except importlib.metadata.PackageNotFoundError:version=None
 if version!=SUPPORTED_SDK:
  print('MCP SDK1.30.0 runtime required. Use the installed SDK interpreter; no automatic installation is performed.',file=sys.stderr);return 1
 os.umask(0o077)
 try:
  parts=_components();anchor=parts.resolve_project_root(args.runtime_project_root);runtime=parts.runtime_path(args.runtime,project_root=anchor)
  pipeline=parts.pipeline(runtime) if args.allow_codex_pipeline else None
  app=parts.router(socket_path=args.laf_router_socket,state_directory=args.laf_router_state,models=args.laf_router_model) if args.allow_laf_router else None
  # Ownership transfers to the reviewed constructor, including its failure
  # cleanup; lifecycle owns later draining. Never perform a second12s drain or
  # close receipt handles behind an admitted worker after lifecycle returns.
  adapter=parts.adapter(runtime,router_app=app,allow_writes=args.allow_writes,allow_local_llm=args.allow_local_llm,allow_local_models=args.allow_local_models,allow_chat_mailbox=args.allow_chat_mailbox,allow_mailbox_grants=args.allow_mailbox_grants,allow_codex_pipeline=args.allow_codex_pipeline,pipeline=pipeline,allow_experiments=args.allow_experiments,runtime_project_root=anchor)
  return parts.serve(adapter)
 except BaseException:
  print('LAF stdio startup/lifecycle unavailable; reconcile known attempts before retry. No provider lifecycle repair was attempted.',file=sys.stderr);return 1

if __name__=='__main__':
 ROOT=Path(__file__).resolve().parents[1]
 sys.path[:0]=[str(ROOT/'integration'),str(ROOT/'private_codex/src'),str(ROOT/'public_laf/mcp/src')]
 raise SystemExit(main())
