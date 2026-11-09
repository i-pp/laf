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

import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from .core import Controller, GIB, ServiceError, check_resources, validate_request
from .server import Handler, Server, UnixHTTPConnection, private_dir, npu_health


def good():
    return {'mem_available': 32*GIB, 'swap_used': 0, 'vram_total': 64*GIB,
            'vram_used': GIB, 'psi_full_avg10': 0, 'pswpin': 0, 'pswpout': 0}


def body(model='flash', **kwargs):
    return {'model': model, 'messages': [{'role': 'user', 'content': 'Hello'}], **kwargs}


class Fake:
    def __init__(self):
        self.calls = []
        self.concurrent = 0
        self.maximum = 0
        self.aborts = 0
        self.resident = True
    def generate(self, job, check):
        self.concurrent += 1
        self.maximum = max(self.maximum, self.concurrent)
        try:
            for _ in range(10):
                check()
                time.sleep(.005)
            self.calls.append(job.model)
            return {'content': 'ok'}
        finally:
            self.concurrent -= 1
    def abort(self):
        self.aborts += 1
        self.resident = False
    def start_flash(self, check):
        check()
        self.resident = True
    def close(self):
        self.abort()
    def health(self):
        return {'flash_resident': self.resident}


class CoreTest(unittest.TestCase):
    def test_input_rejects_unknown_options_and_bad_types(self):
        for b in [body(options={}), body(max_tokens=True), body(max_tokens=1025), body(timeout_seconds=301),
                  body(model='arbitrary'), body(model=[]), {'model':'flash','messages':[{'role':[], 'content':'x'}]}, {'model': 'flash', 'messages': []},
                  {'model':'flash', 'messages':[{'role':'user','content':'x'*8000}]}]:
            with self.assertRaises(ValueError):
                validate_request(b)
    def test_context_budget_and_output_reserve(self):
        validate_request(body(max_tokens=1024))
        with self.assertRaises(ValueError):
            validate_request({'model':'flash','messages':[{'role':'user','content':'x'*7000}], 'max_tokens':1024})
    def test_resource_limits(self):
        for key, value in [('mem_available', 7*GIB), ('vram_used',63*GIB), ('swap_used',GIB), ('psi_full_avg10',11)]:
            sample = good(); sample[key] = value
            with self.assertRaises(ServiceError):
                check_resources(sample, good())
    def test_models_are_serialized(self):
        f = Fake(); ctl = Controller(f, good)
        try:
            jobs=[ctl.submit(body(m)) for m in ['coder-next','gpt-oss-120b','flash','coder-next']]
            for job in jobs:
                self.assertTrue(job.done.wait(3)); self.assertEqual(job.status, 'completed'); self.assertEqual(job.messages, [])
            self.assertEqual(f.maximum,1)
            self.assertEqual(f.calls,['coder-next','gpt-oss-120b','flash','coder-next'])
        finally:
            ctl.close()
    def test_queued_cancellation_never_generates(self):
        f=Fake(); ctl=Controller(f,good)
        try:
            a=ctl.submit(body()); b=ctl.submit(body('coder-next')); b.cancel.set()
            self.assertTrue(b.done.wait(2)); self.assertEqual(b.status,'cancelled'); self.assertNotIn('coder-next',f.calls)
        finally:
            ctl.close()
    def test_running_cancel_and_idle_restore(self):
        f=Fake(); ctl=Controller(f,good)
        try:
            job=ctl.submit(body()); time.sleep(.02); job.cancel.set()
            self.assertTrue(job.done.wait(2)); self.assertEqual(job.status,'cancelled')
            time.sleep(.4); self.assertTrue(f.resident)
        finally:
            ctl.close()
    def test_fault_latches_and_manual_recovery(self):
        f=Fake(); ctl=Controller(f,good)
        try:
            ctl.trip('test pressure')
            with self.assertRaises(ServiceError):ctl.submit(body())
            ctl.recover(); self.assertIsNone(ctl.fault)
            j=ctl.submit(body()); self.assertTrue(j.done.wait(2)); self.assertEqual(j.status,'completed')
        finally:ctl.close()
    def test_queue_and_retention_bounded(self):
        ctl=Controller(Fake(),good,start=False)
        try:
            for _ in range(8):ctl.submit(body())
            with self.assertRaises(ServiceError):ctl.submit(body())
        finally:ctl.close()
    def test_missing_npu_is_independent(self):
        self.assertEqual(npu_health('/does-not-exist')['status'],'unavailable')
    def test_private_directory_rejects_existing_public_dir(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d,0o755)
            with self.assertRaises(ServiceError):private_dir(d)


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory(); self.path=str(Path(self.dir.name)/'service.sock')
        self.ctl=Controller(Fake(),good)
        self.srv=Server(self.path,Handler); self.srv.controller=self.ctl; self.srv.config={}
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.srv.shutdown();self.srv.server_close();self.ctl.close();self.dir.cleanup()
    def request(self,method,path,body=None,headers=None):
        c=UnixHTTPConnection(self.path); raw=None if body is None else json.dumps(body)
        h={'Content-Type':'application/json',**(headers or {})}
        c.request(method,path,raw,h);r=c.getresponse();out=(r.status,json.loads(r.read()));c.close();return out
    def test_health_and_models(self):
        self.assertEqual(self.request('GET','/health')[0],200)
        self.assertEqual(len(self.request('GET','/v1/models')[1]['models']),3)
    def test_origin_and_foreign_host_rejected(self):
        self.assertEqual(self.request('GET','/health',headers={'Origin':'http://evil'})[0],400)
        self.assertEqual(self.request('GET','/health',headers={'Host':'evil'})[0],400)
    def test_async_lifecycle(self):
        code,j=self.request('POST','/v1/jobs',body());self.assertEqual(code,202)
        time.sleep(.2);code,out=self.request('GET','/v1/jobs/'+j['id']);self.assertEqual(out['status'],'completed')
    def test_sync_lifecycle(self):
        code,j=self.request('POST','/v1/chat/completions',body());self.assertEqual(code,200);self.assertEqual(j['status'],'completed')
    def test_bad_payload_and_unknown_routes(self):
        self.assertEqual(self.request('POST','/v1/jobs',{'model':'no'})[0],400)
        self.assertEqual(self.request('GET','/v1/jobs/missing')[0],404)
        self.assertEqual(self.request('DELETE','/v1/jobs/missing')[0],404)


class BackendTest(unittest.TestCase):
    def make(self):
        from .backends import Backends
        return Backends({'state_dir':'/tmp/aceis-test','docker_network':'private', 'weights':'/tmp/weights'}, '/tmp/config')
    def test_ps_empty_does_not_prove_physical_release(self):
        b=self.make(); b.active_model='coder-next'; b.flash_base=good()
        b.api=lambda *a,**kw: {'models':[]}
        high=good();high['vram_used']=40*GIB
        with patch('inference.aceis_service.backends.snapshot',return_value=high), \
             patch('inference.aceis_service.backends.time.monotonic',side_effect=[0,0,1,31]), \
             patch('inference.aceis_service.backends.time.sleep'):
            with self.assertRaises(ServiceError):b.unload(lambda:None)
        self.assertEqual(b.active_model,'coder-next')
    def test_ram_release_is_also_required(self):
        b=self.make();b.active_model='coder-next';b.flash_base=good();b.api=lambda *a,**kw:{'models':[]}
        low=good();low['mem_available']=16*GIB
        with patch('inference.aceis_service.backends.snapshot',return_value=low), \
             patch('inference.aceis_service.backends.time.monotonic',side_effect=[0,0,1,31]), \
             patch('inference.aceis_service.backends.time.sleep'):
            with self.assertRaises(ServiceError):b.unload(lambda:None)
    def test_two_stable_physical_readings_allow_switch(self):
        b=self.make();b.active_model='coder-next';b.flash_base=good();b.api=lambda *a,**kw:{'models':[]}
        with patch('inference.aceis_service.backends.snapshot',return_value=good()), \
             patch('inference.aceis_service.backends.time.sleep'):
            b.unload(lambda:None)
        self.assertIsNone(b.active_model)
    def test_host_network_container_rejected_before_start(self):
        from .backends import IMAGE
        b=self.make();b.cid='owned'
        info={'Config':{'Labels':{'aceis.inference.owner':b.owner}},'Image':IMAGE,
              'HostConfig':{'Privileged':False,'PortBindings':{},'NetworkMode':'host'},
              'NetworkSettings':{'Networks':{'host':{}}}}
        b.docker=lambda *a:json.dumps([info])
        with self.assertRaises(ServiceError):b.inspect_owned()
    def test_ollama_restart_waits_for_readiness(self):
        b=self.make();b.cid='owned';calls=[]
        b.inspect_owned=lambda:{'State':{'Running':False},'NetworkSettings':{'Networks':{'private':{'IPAddress':'172.22.0.2'}}}}
        b.docker=lambda *a,**kw:calls.append(a)
        b.wait_ollama=lambda check:calls.append(('ready',))
        b.start_ollama()
        self.assertEqual(calls,[('start','owned'),('ready',)])
        self.assertEqual(b.address,'172.22.0.2')
    def test_swap_io_guard(self):
        s=good();s['pswpout']=10**9
        with self.assertRaises(ServiceError):check_resources(s,good())

class FaultTest(unittest.TestCase):
    def test_stop_failure_latches_admission(self):
        f=Fake();ctl=Controller(f,good,start=False)
        def fail():raise RuntimeError('stop failure')
        f.abort=fail
        ctl.trip('watchdog')
        self.assertIn('unconfirmed',ctl.fault)
        with self.assertRaises(ServiceError):ctl.submit(body())
    def test_cancel_stop_barrier_keeps_next_job_queued(self):
        class Blocking(Fake):
            def __init__(self):
                super().__init__();self.started=threading.Event();self.aborting=threading.Event();self.release=threading.Event()
            def generate(self,job,check):
                self.calls.append(job.model)
                if job.model=='coder-next':
                    self.started.set();self.aborting.wait(2);raise ServiceError('aborted')
                return {'content':'ok'}
            def abort(self):
                self.aborting.set();self.release.wait(2);super().abort()
        f=Blocking();ctl=Controller(f,good)
        try:
            a=ctl.submit(body('coder-next'));self.assertTrue(f.started.wait(1));b=ctl.submit(body('gpt-oss-120b'));a.cancel.set()
            self.assertTrue(f.aborting.wait(2));time.sleep(.05)
            self.assertNotIn('gpt-oss-120b',f.calls)
            f.release.set();self.assertTrue(b.done.wait(3));self.assertEqual(b.status,'completed')
        finally:
            f.release.set();ctl.close()
    def test_group_kills_live_child_after_wrapper_exits(self):
        from .backends import Backends
        from unittest.mock import Mock
        process=Mock();process.pid=12345;process.poll.return_value=0
        with patch.object(Backends,'group_alive',side_effect=[True,True,True,True,False,False]), \
             patch('inference.aceis_service.backends.time.monotonic',side_effect=[0,1,6,6]), \
             patch('inference.aceis_service.backends.time.sleep'), \
             patch('inference.aceis_service.backends.os.killpg') as kill:
            Backends.stop_group(process)
        import signal
        self.assertEqual([c.args[1] for c in kill.call_args_list],[signal.SIGTERM,signal.SIGKILL])

class EngineHealthTest(unittest.TestCase):
    def test_wrapper_alive_does_not_hide_missing_engine(self):
        from .backends import Backends
        from unittest.mock import Mock
        b=Backends({'state_dir':'/tmp/test'},'/tmp/config');b.flash=Mock();b.flash.pid=12345;b.flash.poll.return_value=None;b.engine_pid=23456
        with patch('inference.aceis_service.backends.Path.read_text',side_effect=FileNotFoundError()):
            self.assertFalse(b.health()['flash_resident'])
    def test_zombie_engine_not_ready(self):
        from .backends import Backends
        from unittest.mock import Mock
        b=Backends({'state_dir':'/tmp/test'},'/tmp/config');b.flash=Mock();b.flash.pid=12345;b.flash.poll.return_value=None;b.engine_pid=23456
        with patch('inference.aceis_service.backends.Path.read_text',return_value='23456 (strata) Z 12345 12345 12345 0'):
            self.assertFalse(b.flash_alive())

if __name__=='__main__':
    unittest.main()
