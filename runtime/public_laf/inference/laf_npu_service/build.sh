#!/usr/bin/env bash
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

set -euo pipefail
[[ $# == 2 ]] || { echo 'Usage: build.sh SDK_ROOT OUTPUT (new file)' >&2; exit 2; }
SDK=$(realpath -e "$1"); OUT=$(realpath -m "$2")
[[ ! -e "$OUT" && ! -L "$OUT" ]] || { echo 'Refusing existing output' >&2; exit 2; }
[[ -d $(dirname "$OUT") ]] || { echo 'Output parent must exist' >&2; exit 2; }
TEMP=$(mktemp "$(dirname "$OUT")/.laf-worker-XXXXXX")
trap 'rm -f "$TEMP"' EXIT
c++ -std=c++17 -O2 -Wall -Wextra -Werror -I"$SDK/cpp-test/include" \
 "$(dirname "$0")/worker.cpp" -L"$SDK/cpp-test/sdk/onnxruntime/lib" -lonnxruntime \
 '-Wl,-rpath,$ORIGIN' -o "$TEMP"
chmod 700 "$TEMP"
ln "$TEMP" "$OUT"
