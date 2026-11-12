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
"""Operator-only, tunnel-only transaction using an existing owned service recipe.

No daemon/model operation, credential read, bus fallback, installation, enable,
permission expansion or automatic retry. Machine plans and journals stay outside
Git. The external recipe is imported, not redistributed by this utility.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time
import tempfile
import zipfile

FLAGS = ("local_llm", "local_models", "chat_mailbox", "mailbox_grants", "codex_pipeline", "experiments")
PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Transient", "Description", "InvocationID", "MainPID", "KillMode", "KillSignal", "TimeoutStopUSec", "Restart", "MemoryMax", "MemorySwapMax", "TasksMax", "CPUQuotaPerSecUSec", "CPUQuotaPeriodUSec", "UMask", "LimitCORE", "LimitFSIZE", "BindsTo", "After", "PartOf", "DropInPaths", "NeedDaemonReload", "ExecStart")

class Refused(RuntimeError): pass

class OperationError(Refused):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain

def digest(data): return hashlib.sha256(data).hexdigest()

def safe_path(path, *, directory=False, private=False):
    p=Path(path).absolute()
    if not Path(path).is_absolute(): raise Refused("Absolute operator path required")
    for a in (p, *p.parents):
        s=a.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid not in (0, os.getuid()) or s.st_mode & 0o022:
            raise Refused("Symlink, foreign or writable path refused")
    s=p.lstat()
    if directory != stat.S_ISDIR(s.st_mode): raise Refused("Unexpected path type")
    if not directory and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1): raise Refused("Regular single-link file required")
    if private and (s.st_uid != os.getuid() or s.st_mode & 0o077): raise Refused("Private owner file/directory required")
    return p

def read_json(path):
    p=safe_path(path, private=True)
    if p.stat().st_size > 131072: raise Refused("Oversized operator record")
    return json.loads(p.read_bytes())

def write_json(path, value, *, exclusive=False):
    # Retain the old complete record until a flushed replacement is committed.
    path=Path(path)
    parent=safe_path(path.parent,directory=True,private=True)
    if path.exists() or path.is_symlink():
        if exclusive: raise FileExistsError(path)
        safe_path(path,private=True)
    fd,name=tempfile.mkstemp(prefix=".cutover-record-",dir=parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,"w") as stream:
            json.dump(value,stream,sort_keys=True,indent=2);stream.write("\n");stream.flush();os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary,path,follow_symlinks=False)
            temporary.unlink()
        else: os.replace(temporary,path)
        directory=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if temporary.exists(): temporary.unlink()

def run(argv, timeout=10):
    # Inherit the operator terminal exactly. Never synthesize/replace a user bus.
    try: value=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as exc: raise OperationError("Manager command timed out; inspect before any retry",uncertain=True) from exc
    if value.returncode: raise OperationError("Manager command failed; no alternate route or retry attempted",uncertain=True)
    return value.stdout

def show(service, name):
    text=service.call(["/usr/bin/systemctl","--user","show",name,"--property="+",".join(PROPS),"--no-pager"])
    return dict(line.split("=",1) for line in text.splitlines() if "=" in line)

def verify_bundle(plan):
    archive=safe_path(plan["candidate_bundle"],private=True)
    root=safe_path(plan["candidate_root"],directory=True,private=True)
    if digest(archive.read_bytes()) != plan["candidate_sha256"]: raise Refused("Reviewed source archive changed")
    count=0
    with zipfile.ZipFile(archive) as z:
        names=set()
        for item in z.infolist():
            if not item.filename.startswith("stdio/") or item.is_dir(): continue
            rel=Path(item.filename).relative_to("stdio")
            if ".." in rel.parts or rel.is_absolute() or str(rel) in names: raise Refused("Unsafe/duplicate archive member")
            names.add(str(rel))
            path=safe_path(root/rel,private=True)
            if path.read_bytes()!=z.read(item): raise Refused("Staged source differs from reviewed archive")
            count+=1
        actual={str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
        if actual!=names or count<100: raise Refused("Unexpected or incomplete staged source")
    python=Path(plan["python"])
    if not python.is_absolute() or not python.is_file() or not os.access(python,os.X_OK): raise Refused("Installed SDK interpreter missing")
    return count

def load_service(plan):
    # This is an existing approved checkout, not a newly installed executable.
    # Preserve its original path/permission boundary; pin its tracked bytes.
    script=Path(plan["manager_script"])
    if not script.is_absolute() or any(p.is_symlink() for p in (script,*script.parents)):
        raise Refused("Unsafe existing recipe path")
    info=script.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or info.st_mode & 0o002:
        raise Refused("Existing owner recipe required")
    project=script.parent.parent
    head=subprocess.run(["/usr/bin/git","-C",str(project),"rev-parse","HEAD"],capture_output=True,text=True,check=True).stdout.strip()
    dirty=subprocess.run(["/usr/bin/git","-C",str(project),"status","--porcelain","--untracked-files=no"],capture_output=True,text=True,check=True).stdout
    if head!=plan["manager_commit"] or dirty: raise Refused("Existing recipe checkout changed")
    original=subprocess.run(["/usr/bin/git","-C",str(project),"show",head+":"+str(script.relative_to(project))],capture_output=True,check=True).stdout
    if script.read_bytes()!=original: raise Refused("Recipe bytes differ from pinned source")
    sys.path[:0]=[str(script.parent),str(project/"src")]
    spec=importlib.util.spec_from_file_location("existing_owned_recipe",script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class NativeServices(module.Services):
        def call(self, argv, timeout=10): return run(argv,timeout)
    # Reuse the external recipe's owner/runtime guard, not a new incompatible
    # ancestry rule for an already accepted checkout. No guard is disabled.
    state_path=Path(plan["runtime"])/module.STATE
    module.private_file(state_path)
    saved=json.loads(state_path.read_bytes())
    kwargs={"allow_"+key:saved.get(key+"_enabled",False) for key in FLAGS}
    svc=NativeServices(plan["runtime"],saved["codex_file_reference"],saved["credential_file_reference"],**kwargs)
    return svc,module

def new_command(service, plan):
    old=service.commands()["tunnel"]
    if sum(a.startswith("--mcp.command=channel=main,command=") for a in old)!=1: raise Refused("Exactly one existing stdio channel required")
    argv=["/usr/bin/env","-u","CONTROL_PLANE_API_KEY","-u","OPENAI_API_KEY","-u","OPENAI_ADMIN_KEY",plan["python"],"-B",str(Path(plan["candidate_root"])/"integration/laf_stdio_cli.py"),"--runtime",str(service.root),"--runtime-project-root",str(service.root.parent.parent)]
    for flag in FLAGS:
        if getattr(service,"allow_"+flag,False): argv.append("--allow-"+flag.replace("_","-"))
    argv.extend(["--allow-laf-router","--laf-router-socket",plan["router_socket"],"--laf-router-state",plan["router_state"]])
    models=plan["models"]
    if not isinstance(models,list) or not models or len(models)!=len(set(models)) or any(not isinstance(x,str) or not x or len(x)>64 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in x) for x in models): raise Refused("Explicit bounded model allowlist required")
    for model in models: argv.extend(["--laf-router-model",model])
    replacement="--mcp.command=channel=main,command="+shlex.join(argv)
    return [replacement if x.startswith("--mcp.command=channel=main,command=") else x for x in old]

def current(service,state):
    props={r:show(service,n) for r,n in service.units.items()}
    for role,p in props.items():
        if not service.owned(state,role,p) or p.get("ActiveState")!="active": raise Refused("Expected owned active generation is missing")
    return props

def validate(plan,service):
    state=service.read_state()
    if state.get("phase")!="READY" or state.get("invocations")!=plan["expected_invocations"]: raise Refused("Recorded generation differs from reviewed plan")
    if digest((service.root/"poc-services-state.json").read_bytes())!=plan["expected_state_sha256"]: raise Refused("Recorded state changed")
    props=current(service,state)
    if props["tunnel"]!=plan["expected_tunnel_properties"]: raise Refused("Tunnel configuration changed; inspect a new plan")
    service.preflight(state["credential_file_reference"])
    if not service_wait_ready(service,2): raise Refused("Existing transport is not currently ready")
    return state,props

def service_wait_ready(service,timeout):
    from service_wait import tunnel_ready
    return tunnel_ready(service.client,service.health,timeout)

def stop_tunnel(service,state):
    props=show(service,service.units["tunnel"])
    if not service.owned(state,"tunnel",props): raise Refused("Tunnel generation is not owned")
    service.call(["/usr/bin/systemctl","--user","stop",service.units["tunnel"]],timeout=25)
    props=show(service,service.units["tunnel"])
    if props.get("LoadState")!="not-found" and props.get("ActiveState") not in ("inactive","failed"): raise OperationError("Tunnel stop unconfirmed",uncertain=True)

def wait_tunnel(service,state,seconds=75):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        p=show(service,service.units["tunnel"])
        service.owned(state,"tunnel",p)
        if p.get("ActiveState") not in ("active","activating"): raise Refused("Replacement tunnel exited before readiness")
        try: ready=service_wait_ready(service,min(1,max(.01,deadline-time.monotonic())))
        except FileNotFoundError: ready=False  # New listener has not created its private locator yet.
        # Permission/ownership failures are not transient and are never retried.
        if ready: return
        time.sleep(.1)
    raise Refused("Tunnel readiness decision deadline exceeded")

def rotate_health(service,journal,name):
    path=service.health
    if path.exists() or path.is_symlink():
        # Existing runtime ancestry is validated by the external recipe. This
        # direct child must retain that recipe's owner-only file contract.
        info=path.lstat()
        if path.parent!=service.root or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:
            raise Refused("Existing health locator must be private owner regular file")
        target=journal/name
        if target.exists(): raise Refused("Health backup already exists")
        path.rename(target)

def daemon_unchanged(service,state):
    p=show(service,service.units["daemon"])
    if not service.owned(state,"daemon",p) or p.get("ActiveState")!="active": raise Refused("Daemon generation changed; no repair attempted")

def switch(plan,service,journal):
    # External recipe lock serializes the old manager and this transaction.
    with service.locked():
        state,props=validate(plan,service)
        if journal.exists(): raise Refused("Use a new journal directory; previous evidence is retained")
        journal.mkdir(mode=0o700)
        write_json(journal/"before-state.json",state,exclusive=True)
        write_json(journal/"before-properties.json",props,exclusive=True)
        write_json(journal/"plan.json",plan,exclusive=True)
        old_command=service.commands()["tunnel"]
        new=new_command(service,plan)
        record={"schema":1,"stage":"prepared","old_invocations":copy.deepcopy(state["invocations"]),"new_invocation":None,"old_command":old_command,"service_changes":0}
        write_json(journal/"transaction.json",record,exclusive=True)
        try:
            state["phase"]="STARTING";service.write_state(state)
            record["stage"]="stop_requested";write_json(journal/"transaction.json",record)
            stop_tunnel(service,state)
            record["stage"]="old_stopped";record["service_changes"]=1;write_json(journal/"transaction.json",record)
            daemon_unchanged(service,state)
            rotate_health(service,journal,"old-health-url")
            service.launch(state,"tunnel",new)
            record["new_invocation"]=state["invocations"]["tunnel"]
            record["stage"]="new_launched";record["service_changes"]=2;write_json(journal/"transaction.json",record)
            wait_tunnel(service,state)
            daemon_unchanged(service,state)
            state["phase"]="READY";state["updated_at"]=time.time();service.write_state(state)
            record["stage"]="transport_ready_client_acceptance_pending";write_json(journal/"transaction.json",record)
            return {"status":record["stage"],"tunnel_invocation":record["new_invocation"],"daemon_unchanged":True,"inference_operations":0,"journal":str(journal)}
        except BaseException:
            state["phase"]="CLEANUP_UNCONFIRMED";service.write_state(state)
            record["stage"]="reconcile_before_retry";write_json(journal/"transaction.json",record)
            raise

def rollback(plan,service,journal):
    safe_path(journal,directory=True,private=True)
    record=read_json(journal/"transaction.json")
    before=read_json(journal/"before-state.json")
    if read_json(journal/"plan.json")!=plan: raise Refused("Rollback plan differs from recorded transaction")
    with service.locked():
        state=service.read_state()
        daemon_unchanged(service,before)
        if not record.get("new_invocation") or state["invocations"].get("tunnel")!=record["new_invocation"]: raise Refused("Replacement ownership is uncertain; inspect, do not retry or adopt a generation")
        if state["invocations"].get("daemon")!=before["invocations"]["daemon"]: raise Refused("Daemon invocation differs")
        service.require_idle_database()
        try:
            state["phase"]="STARTING";service.write_state(state)
            record["stage"]="rollback_stop_requested";write_json(journal/"transaction.json",record)
            stop_tunnel(service,state)
            record["stage"]="rollback_new_stopped";write_json(journal/"transaction.json",record)
            rotate_health(service,journal,"replacement-health-url")
            daemon_unchanged(service,before)
            service.launch(state,"tunnel",record["old_command"])
            record["rollback_invocation"]=state["invocations"]["tunnel"];record["stage"]="rollback_launched";write_json(journal/"transaction.json",record)
            wait_tunnel(service,state)
            daemon_unchanged(service,before)
            state["phase"]="READY";state["updated_at"]=time.time();service.write_state(state)
            record["stage"]="rolled_back_transport_ready";write_json(journal/"transaction.json",record)
            return {"status":record["stage"],"daemon_unchanged":True,"inference_operations":0,"journal":str(journal)}
        except BaseException:
            state["phase"]="CLEANUP_UNCONFIRMED";service.write_state(state)
            record["stage"]="rollback_reconcile_before_retry";write_json(journal/"transaction.json",record)
            raise

def reconcile(plan,service,journal):
    """Finalize only the recorded replacement, without any service operation."""
    safe_path(journal,directory=True,private=True)
    record=read_json(journal/"transaction.json")
    before=read_json(journal/"before-state.json")
    if read_json(journal/"plan.json")!=plan: raise Refused("Reconciliation plan differs from transaction")
    with service.locked():
        state=service.read_state()
        if state.get("phase")!="CLEANUP_UNCONFIRMED" or record.get("stage")!="reconcile_before_retry":
            raise Refused("This is not the recorded incomplete cutover")
        if not record.get("new_invocation") or state["invocations"].get("tunnel")!=record["new_invocation"]:
            raise Refused("Unknown generation cannot be adopted")
        daemon_unchanged(service,before)
        p=show(service,service.units["tunnel"])
        if not service.owned(state,"tunnel",p) or p.get("ActiveState")!="active": raise Refused("Recorded replacement is not active")
        if not service_wait_ready(service,2): raise Refused("Recorded replacement is not currently ready")
        state["phase"]="READY";state["updated_at"]=time.time();service.write_state(state)
        record["stage"]="transport_ready_client_acceptance_pending"
        record["reconciled_without_service_operation"]=True
        write_json(journal/"transaction.json",record)
        return {"status":record["stage"],"tunnel_invocation":record["new_invocation"],"service_changes":0,"inference_operations":0,"daemon_unchanged":True,"journal":str(journal)}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("inspect","apply","rollback","reconcile"))
    parser.add_argument("--plan",type=Path,required=True)
    parser.add_argument("--journal",type=Path)
    args=parser.parse_args(argv)
    os.umask(0o077)
    try:
        plan=read_json(args.plan)
        if plan.get("schema")!=1: raise Refused("Unsupported plan schema")
        files=verify_bundle(plan)
        service,module=load_service(plan)
        if args.action=="inspect":
            with service.locked(): validate(plan,service)
            result={"status":"ready_for_tunnel_only_cutover","source_files_byte_equal":files,"inference_operations":0,"daemon_stop_operations":0,"estimated_decision_budget_seconds":95,"not_an_outage_sla":True}
        else:
            if args.journal is None or not args.journal.is_absolute(): raise Refused("Explicit absolute private journal directory required")
            safe_path(args.journal.parent,directory=True,private=True)
            operation={"apply":switch,"rollback":rollback,"reconcile":reconcile}[args.action]
            result=operation(plan,service,args.journal)
        print(json.dumps(result,sort_keys=True));return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print(json.dumps({"status":"blocked_or_reconciliation_required","reason":str(exc),"automatic_retry":False,"service_state_must_be_inspected":True}));return 1

if __name__=="__main__": raise SystemExit(main())
