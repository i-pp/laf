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

"""Explicit private-socket test IDs, not class-wide or detected-error skips."""
PRIVATE_SOCKET_TESTS = frozenset({
    'inference.laf_npu_service.tests.test_service.HttpTests.test_busy_and_unavailable',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_classify',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_existing_socket_not_removed',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_invalid_body',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_not_ready',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_origin_rejected',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_public_parent_refused',
    'inference.laf_npu_service.tests.test_service.HttpTests.test_socket_private',
    'inference.laf_service.test_acceptance_profiles.CandidateEntryTest.test_real_entry_http_catalog_readiness_job_switch_and_shutdown',
    'inference.laf_service.test_core_integration.CoreEntryLiveTest.test_opt_in_core_entry_uses_controller_and_preserves_http_api',
    'inference.laf_service.test_host_supervisor.SupervisorTest.test_real_socket_lanes',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_cancel_lane_survives_saturated_start_workers',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_client_wrong_expected_uid_rejected_before_mutation',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_io_timeout_is_unknown_not_automatic_retry',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_preexisting_endpoint_is_never_replaced',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_real_peer_authenticated_controller_and_host_telemetry',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_server_rejects_wrong_peer_uid_before_dispatch',
    'inference.laf_service.test_lifecycle_uds_live.RealUnixTest.test_socket_modes_and_cleanup_identity',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_busy_and_error_no_retry',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_compact_boundary_rounds_to_float32_without_body_growth',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_deadline_closes_connection_does_not_retry',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_malformed_output_fails_closed',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_oversized_upstream_fails_closed',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_private_mode_and_absent_endpoint',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_real_private_http_one_attempt',
    'inference.laf_service.test_npu_bridge.BridgeTest.test_truncated_or_duplicate_framing_fails_closed',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_bad_classification_is_400',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_capabilities_preserve_gpu_models_and_separate_npu',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_classification_success_separate_from_gpu_lane',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_empty_origin_is_rejected',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_npu_outage_keeps_gpu_ready_and_serving',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_only_classification_gets_larger_body',
    'inference.laf_service.test_npu_bridge.ClassificationHTTPTest.test_proxy_slot_rejects_concurrent_request',
    'inference.laf_service.test_service.HTTPTest.test_async_lifecycle',
    'inference.laf_service.test_service.HTTPTest.test_bad_payload_and_unknown_routes',
    'inference.laf_service.test_service.HTTPTest.test_health_and_models',
    'inference.laf_service.test_service.HTTPTest.test_origin_and_foreign_host_rejected',
    'inference.laf_service.test_service.HTTPTest.test_sync_lifecycle',
    'inference.local_platform.test_cli.TestCLI.test_private_socket_boundary',
})
