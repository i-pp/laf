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
"""Bounded private NDJSON transport; no provider lifecycle or service actions.

Only the caller thread reads/writes protocol FDs. A legacy worker preserves
serialization; Router normal(3)/cancel(1) lanes cannot occupy its queue. EOF or
stop ends admission and drains already admitted work for at most12 seconds.
Timeout never closes descriptors in use, replays work or cancels provider jobs.
This module is not yet a live Tunnel launcher or deployment acceptance.
"""
import collections,json,math,os,queue,selectors,threading,time
from legacy_tool_adapter import decode,MAX_INPUT,MAX_OUTPUT

class StdioTransport:
 def __init__(self,adapter,input_fd,output_fd,*,stop_event=None,drain_timeout=12,write_timeout=2,partial_timeout=3):
  for value,bound in ((drain_timeout,12),(write_timeout,2),(partial_timeout,3)):
   if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=bound:raise ValueError('Bounded positive transport timeout required')
  if type(input_fd) is not int or type(output_fd) is not int or input_fd==output_fd:raise ValueError('Distinct operator-owned protocol FDs required')
  self.adapter=adapter;self.input_fd=input_fd;self.output_fd=output_fd;self.stop=stop_event or threading.Event()
  self.drain_timeout=drain_timeout;self.write_timeout=write_timeout;self.partial_timeout=partial_timeout
  self._legacy=queue.Queue(8);self._control=queue.Queue(8);self._completed=queue.Queue(32);self._normal=threading.BoundedSemaphore(3);self._cancel=threading.BoundedSemaphore(1)
  self.started=threading.Event();self._worker_stop=threading.Event();self._lock=threading.Lock();self._threads=[];self._active_ids=set();self._outstanding=0
  self._state_condition=threading.Condition();self._state_submitted=0;self._state_completed=0
  self.report={'admitted':0,'completed':0,'output_failed':False,'handles_retained':False,'closed':False,'dispatch_start_failures':0}
 @staticmethod
 def _key(m):
  if isinstance(m,dict) and 'id' in m and (type(m['id']) is int or isinstance(m['id'],str) and len(m['id'])<=128):return (type(m['id']),m['id'])
  return None
 def _error(self,m,code,text):
  key=self._key(m);return self.adapter.error(key[1] if key else None,code,text)
 def _call(self,m,key,slot=None,dependency=0,state=0):
  reply=None
  try:
   with self._state_condition:
    while dependency>self._state_completed and not self._worker_stop.is_set():self._state_condition.wait(.02)
   if self._worker_stop.is_set():reply=self._error(m,-32603,'Frontend stopped before dispatch; reconcile known attempts before retry')
   else:
    try:reply=self.adapter.handle(m)
    except BaseException:reply=self._error(m,-32603,'Local adapter unavailable; reconcile known attempts before retry')
   self._completed.put_nowait((reply,key))
  finally:
   if state:
    with self._state_condition:self._state_completed=state;self._state_condition.notify_all()
   if slot is not None:slot.release()
   with self._lock:self._outstanding-=1;self.report['completed']+=1
 def _legacy_worker(self):
  while not self._worker_stop.is_set():
   try:m,key,dependency=self._legacy.get(timeout=.02)
   except queue.Empty:continue
   if self._worker_stop.is_set():return
   self._call(m,key,dependency=dependency)
 def _control_worker(self):
  while not self._worker_stop.is_set():
   try:m,key,state=self._control.get(timeout=.02)
   except queue.Empty:continue
   if self._worker_stop.is_set():return
   self._call(m,key,state=state)
 def _admit(self,m,key,pending):
  params=m.get('params') if isinstance(m,dict) else None
  name=params.get('name') if isinstance(params,dict) else None
  is_tool=isinstance(m,dict) and m.get('method')=='tools/call'
  if not is_tool:
   # Control I/O/audit can stall: keep it off the reader. A serial bounded
   # control lane plus a state-only barrier preserves2025 initialization order.
   if self._control.full():
    if isinstance(m,dict) and 'id' in m:pending.append((self._encode(self._error(m,-32000,'Frontend busy; request not dispatched')),key,None))
    return
   state=0
   if isinstance(m,dict) and m.get('method') in ('initialize','notifications/initialized'):
    with self._state_condition:self._state_submitted+=1;state=self._state_submitted
   with self._lock:self._outstanding+=1;self.report['admitted']+=1
   self._control.put_nowait((m,key,state));return
  meta=params.get('_meta') if isinstance(params,dict) else None
  modern=isinstance(meta,dict) and ('io.modelcontextprotocol/protocolVersion' in meta or 'io.modelcontextprotocol/clientCapabilities' in meta)
  with self._state_condition:dependency=0 if modern else self._state_submitted
  router=name in self.adapter.router_names if isinstance(name,str) else False
  slot=(self._cancel if name=='laf_router_cancel_job' else self._normal) if router else None
  available=slot.acquire(blocking=False) if slot is not None else not self._legacy.full()
  if not available:
   if isinstance(m,dict) and 'id' in m:
    reply=self._error(m,-32000,'Frontend busy; request not dispatched')
    if router:
     data={'outcome':'UNKNOWN','dispatch_started':False,'automatic_retry':False}
     args=params.get('arguments')
     handle=args.get('attempt_id',args.get('handle')) if isinstance(args,dict) else None
     if isinstance(handle,str) and len(handle)==32 and all(c in '0123456789abcdef' for c in handle):data['handle']=handle
     reply['error']['data']=data
    pending.append((self._encode(reply),key,None))
   return
  with self._lock:self._outstanding+=1;self.report['admitted']+=1
  if slot is None:self._legacy.put_nowait((m,key,dependency))
  else:
   self._threads=[t for t in self._threads if t.is_alive()]
   worker=threading.Thread(target=self._call,args=(m,key,slot,dependency),daemon=True)
   try:worker.start()
   except RuntimeError:
    slot.release()
    with self._lock:self._outstanding-=1;self.report['dispatch_start_failures']+=1
    if isinstance(m,dict) and 'id' in m:pending.append((self._encode(self._error(m,-32603,'Frontend worker unavailable; request not dispatched')),key,None))
   else:self._threads.append(worker)
 def _encode(self,reply):
  try:
   raw=json.dumps(reply,ensure_ascii=False,allow_nan=False).encode('utf-8')+b'\n'
   if len(raw)<=MAX_OUTPUT:return raw
  except (TypeError,ValueError,UnicodeError,RecursionError):pass
  return (json.dumps(self._error(reply,-32603,'Result exceeds output bound'),ensure_ascii=True)+'\n').encode()
 def join_workers(self,timeout):
  deadline=time.monotonic()+timeout
  for thread in self._threads:
   thread.join(max(0,deadline-time.monotonic()))
  static=[getattr(self,name,None) for name in ('_legacy_thread','_control_thread')]
  for thread in static:
   if thread is not None:thread.join(max(0,deadline-time.monotonic()))
  return all(not t.is_alive() for t in self._threads+ [t for t in static if t is not None])
 def run(self):
  selector=None;original={};pending=collections.deque();buffer=bytearray();partial_since=None;deadline=None;status=0;output_ok=True
  def stop(status_code=0):
   nonlocal deadline,status
   status=max(status,status_code)
   if deadline is None:deadline=time.monotonic()+self.drain_timeout
  def error(code,text):pending.append((self._encode(self.adapter.error(None,code,text)),None,None))
  try:
   for fd in (self.input_fd,self.output_fd):original[fd]=os.get_blocking(fd);os.set_blocking(fd,False)
   selector=selectors.DefaultSelector();selector.register(self.input_fd,selectors.EVENT_READ)
   legacy_thread=threading.Thread(target=self._legacy_worker,daemon=True);legacy_thread.start();self._legacy_thread=legacy_thread
   control_thread=threading.Thread(target=self._control_worker,daemon=True);control_thread.start();self._control_thread=control_thread;self.started.set()
   while True:
    if self.stop.is_set():stop()
    while True:
     try:reply,key=self._completed.get_nowait()
     except queue.Empty:break
     if reply is not None and output_ok:pending.append((self._encode(reply),key,None))
     elif key is not None:self._active_ids.discard(key)
    if pending and output_ok:
     raw,key,write_deadline=pending[0]
     if write_deadline is None:write_deadline=time.monotonic()+self.write_timeout
     try:
      if time.monotonic()>=write_deadline:raise TimeoutError()
      written=os.write(self.output_fd,raw)
      if written<=0:raise BrokenPipeError()
      raw=raw[written:]
      if not raw:
       pending.popleft()
       if key is not None:self._active_ids.discard(key)
      else:pending[0]=(raw,key,write_deadline)
     except BlockingIOError:
      pending[0]=(raw,key,write_deadline)
      if time.monotonic()>=write_deadline:output_ok=False
     except OSError:output_ok=False
     if not output_ok:self.report['output_failed']=True;pending.clear();stop(1)
    with self._lock:outstanding=self._outstanding
    if deadline is not None:
     if outstanding==0 and self._completed.empty() and not pending:break
     if time.monotonic()>=deadline:status=1;break
     time.sleep(.002);continue
    # Backpressure bounds reader admission too. Completed lanes add at most22
    # bounded responses to the16-reader-response high-water mark.
    if len(pending)>=16:time.sleep(.002);continue
    frames=0
    while b'\n' in buffer and frames<8 and len(pending)<16 and deadline is None:
     if self.stop.is_set():stop();break
     line,_,rest=buffer.partition(b'\n');buffer=bytearray(rest);frames+=1;partial_since=None
     if len(line)>MAX_INPUT:error(-32700,'Input exceeds frame bound');stop(1);break
     try:message=decode(line)
     except (ValueError,UnicodeError,RecursionError):error(-32700,'Invalid JSON frame');continue
     key=self._key(message)
     if key is not None:
      if key in self._active_ids:error(-32600,'Duplicate in-flight request identifier');stop(1);break
      self._active_ids.add(key)
     self._admit(message,key,pending)
    if deadline is not None:continue
    if len(buffer)>MAX_INPUT and b'\n' not in buffer:error(-32700,'Input exceeds frame bound');stop(1);continue
    if buffer and b'\n' not in buffer:
     if partial_since is None:partial_since=time.monotonic()
     if time.monotonic()-partial_since>=self.partial_timeout:error(-32700,'Incomplete input deadline exceeded');stop(1);continue
    if b'\n' in buffer or len(pending)>=16:continue
    try:
     if not selector.select(.01):continue
     chunk=os.read(self.input_fd,4096)
    except BlockingIOError:continue
    except OSError:stop(1);continue
    if not chunk:
     if buffer:error(-32700,'Incomplete JSON frame');stop(1)
     else:stop()
    else:buffer.extend(chunk)
  except (OSError,ValueError,RuntimeError):stop(1)
  finally:
   self._worker_stop.set()
   with self._state_condition:self._state_condition.notify_all()
   with self._lock:outstanding=self._outstanding
   if outstanding==0:
    try:self.report['closed']=bool(self.adapter.close(timeout=max(0,(deadline or time.monotonic()+self.drain_timeout)-time.monotonic())))
    except BaseException:self.report['closed']=False
   self.report['handles_retained']=not self.report['closed']
   if not self.report['closed']:status=1
   if selector is not None:selector.close()
   for fd,blocking in original.items():
    try:os.set_blocking(fd,blocking)
    except OSError:status=1
   self.join_workers(.05)
  return status
