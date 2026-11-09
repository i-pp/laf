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

# Supply an already-inspected immutable base through the reviewed build wrapper.
# No package installation or model/vendor bytes are included in this source image.
ARG LAF_CORE_BASE
FROM ${LAF_CORE_BASE}
WORKDIR /app
COPY inference/aceis_service/__init__.py inference/aceis_service/__init__.py
COPY inference/aceis_service/core.py inference/aceis_service/core.py
COPY inference/aceis_service/server.py inference/aceis_service/server.py
COPY inference/aceis_service/npu_bridge.py inference/aceis_service/npu_bridge.py
COPY inference/aceis_service/lifecycle.py inference/aceis_service/lifecycle.py
COPY inference/aceis_service/lifecycle_client.py inference/aceis_service/lifecycle_client.py
COPY inference/aceis_service/lifecycle_uds.py inference/aceis_service/lifecycle_uds.py
COPY inference/aceis_service/core_backends.py inference/aceis_service/core_backends.py
COPY inference/aceis_service/core_flash_worker.py inference/aceis_service/core_flash_worker.py
COPY inference/aceis_service/core_server.py inference/aceis_service/core_server.py
ENV PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
USER 1000:1000
ENTRYPOINT ["python3", "-B", "-m", "inference.aceis_service.core_server"]
