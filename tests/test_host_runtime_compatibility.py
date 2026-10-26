"""Host capability diagnostics must not manufacture browser authority."""
import unittest

from _browser_adapter_support import HostContext, host_session
from omh.plugin_bundle.omh import browser_bridge


class BrowserHostCompatibilityTests(unittest.TestCase):
    def test_missing_principal_api_is_a_feature_level_unsupported_diagnostic(self):
        with host_session() as host:
            del host._BROWSER_CONTROL_PRINCIPAL
            capability = getattr(browser_bridge, 'host_browser_capability', lambda: {})()
            self.assertEqual(capability.get('reason'), 'host_browser_identity_unsupported')
            self.assertIn('_BROWSER_CONTROL_PRINCIPAL', capability.get('missing_capabilities', []))
            self.assertFalse(capability.get('supported'))
            self.assertIsNone(browser_bridge._host_identity())

    def test_supported_host_requires_bound_request_and_both_cache_bypasses(self):
        with host_session() as host:
            capability = getattr(browser_bridge, 'host_browser_capability', lambda: {})()
            self.assertTrue(capability.get('supported'), capability)
            self.assertEqual(browser_bridge._host_identity(), ('owner', 'principal', 'local-api', 'request'))
            host._BROWSER_CONTROL_PRINCIPAL.set('')
            self.assertIsNone(browser_bridge._host_identity())

    def test_optional_registration_exposes_diagnostic_without_admitting_a_task(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp, host_session() as host:
            del host._BROWSER_CONTROL_PRINCIPAL
            ctx = HostContext(Path(tmp)/'omh')
            browser_task = browser_bridge.register(ctx, {'omh_home': str(ctx.home)})
            self.assertTrue(callable(getattr(ctx, 'browser_capability', None)))
            self.assertEqual(ctx.browser_capability()['reason'], 'host_browser_identity_unsupported')
            with self.assertRaisesRegex(ValueError, 'host_scope_unavailable.*host_browser_identity_unsupported'):
                with browser_task('task'):
                    self.fail('unsupported host must never admit')
            self.assertFalse(ctx.checks['omh_browser']())
            self.assertFalse(ctx.home.exists())


if __name__ == '__main__':
    unittest.main()
