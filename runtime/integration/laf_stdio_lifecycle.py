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
"""Cooperative stdio signal lifecycle for an explicitly supplied private adapter.

No CLI/service installation yet. Does not cancel provider jobs or replay work.
"""
import signal,sys,threading
from laf_stdio_transport import StdioTransport

def run_stdio(adapter,*,input_fd=None,output_fd=None,transport_class=StdioTransport):
 previous={};stop=threading.Event();requested=False
 def request_stop(_signum,_frame):
  nonlocal requested
  if not requested:requested=True;stop.set()
 try:
  if threading.current_thread() is not threading.main_thread():raise ValueError('Main frontend thread required')
  for signum in (signal.SIGTERM,signal.SIGINT):previous[signum]=signal.signal(signum,request_stop)
  transport=transport_class(adapter,sys.stdin.fileno() if input_fd is None else input_fd,sys.stdout.fileno() if output_fd is None else output_fd,stop_event=stop)
  return transport.run()
 except BaseException:
  # No raw exception or arguments on stderr. close() itself retains handles if
  # admitted work cannot drain. Keep cooperative signal handlers until it ends.
  try:adapter.close(timeout=12)
  except BaseException:pass
  print('LAF stdio frontend unavailable; reconcile known attempts before retry.',file=sys.stderr)
  return 1
 finally:
  for signum,handler in previous.items():signal.signal(signum,handler)
