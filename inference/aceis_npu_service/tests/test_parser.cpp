/*
 * KISS Open License Version 1.0 (KOL-v1)
 *
 * SPDX-FileCopyrightText: 2026 I-plusplus Inc.
 * SPDX-License-Identifier: LicenseRef-KOL-v1
 *
 * Copyright remains with its respective holders; original copyright and attribution notices must be preserved.
 * Contributors may add copyright notices for their own original modifications.
 * Distributed modifications must be identified as required by Section 4(c) of KOL-v1.
 *
 * Use, modification, distribution, and incorporation into commercial or proprietary software are permitted
 * without license fees or source disclosure, subject to all terms of the complete KOL-v1 license.
 *
 * Contributors grant the patent permissions stated in KOL-v1. Outputs are not covered merely because a
 * KOL-licensed tool produced them; embedded licensed material remains subject to applicable license terms.
 *
 * THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OR LIABILITY TO THE EXTENT PERMITTED BY APPLICABLE LAW.
 *
 * This header is a summary, not a separate license or an amendment to KOL-v1.
 * All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.
 */

// Test the production worker parser without creating an ORT session/device.
#define main aceis_worker_main
#include "../worker.cpp"
#undef main
#include <limits>
int main() {
  std::vector<float> values(3072);
  std::string line="0 1.40129846e-45 3.40282347e+38";
  for (int i=3;i<3072;++i) line+=" 0";
  parse_input(line,values);
  if (values[0]!=0 || values[1]!=std::numeric_limits<float>::denorm_min() ||
      values[2]!=std::numeric_limits<float>::max()) return 1;
  try { parse_input("nan",values); return 2; } catch (const std::runtime_error&) {}
  try { parse_input("1e99",values); return 3; } catch (const std::runtime_error&) {}
  return 0;
}
