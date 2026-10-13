"""The interrupt flag must not outlive a test that sets it (#1482).

`dispatch_fanout` sets the process-global `_INTERRUPT_FLAG` on an interrupt or
a dispatcher exception and leaves it for the next dispatch entry. A later test
in the same process that spawns through `signal_safe_unit_runner` directly is
then SIGTERMed on register-then-check. CI shard plans reorder tests run to run,
so the leak only shows when a leaker lands before such a test.
"""

from __future__ import annotations

import io
import unittest

from omh.coding.fanout_dispatch import _INTERRUPT_FLAG

# Tests known to drive dispatch_fanout into its interrupt or exception branch.
_LEAKERS = (
    "test_fanout_admission.FanoutInterruptedAdmissionTests"
    ".test_interrupt_cleanup_observes_a_collected_clean_completion_once",
    "test_fanout_dispatch.FanoutUnitResultIntakeTests"
    ".test_d5_dispatcher_exception_preserves_summary_and_reraises",
    "test_fanout_dispatch.FanoutUnitResultIntakeTests"
    ".test_d5_dispatcher_fault_keeps_completed_sibling_evidence",
    "test_fanout_signal_safety.AsyncSignalTests"
    ".test_sigterm_mid_flight_kills_groups_and_blocks_new_spawns",
    "test_fanout_executor_sessions.SessionsIntegration"
    ".test_s1_observer_failure_preserves_historical_id_without_resume",
)


class InterruptFlagIsolationTests(unittest.TestCase):
    def test_interrupt_setting_tests_leave_the_flag_clear(self) -> None:
        self.addCleanup(_INTERRUPT_FLAG.clear)
        for name in _LEAKERS:
            with self.subTest(test=name):
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
                result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertFalse(
                    _INTERRUPT_FLAG.is_set(),
                    f"{name} left the process-global interrupt flag set",
                )


if __name__ == "__main__":
    unittest.main()
