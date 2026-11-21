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

"""One-function fixture for the bounded repair client; not a general agent."""
import argparse
import json
from .repair import repair

BUGGY = 'def clamped_add(a, b, limit):\n    return min(a - b, limit)\n'
CASES = [{'function':'clamped_add','args':args,'expected':expected} for args,expected in
         [([2,3,10],5),([8,7,10],10),([-2,3,10],1),([0,0,10],0)]]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket',required=True)
    p.add_argument('--workdir',required=True,help='new private demonstration directory only')
    args=p.parse_args()
    result=repair(args.socket,BUGGY,CASES,args.workdir)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['tests_passed'] else 1

if __name__=='__main__': raise SystemExit(main())
