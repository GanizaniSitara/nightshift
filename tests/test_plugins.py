"""Tests for the plugin loader: a plugin in an external dir self-registers."""

import sys
import textwrap
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift import plugins  # noqa: E402
from nightshift.verifiers import registry  # noqa: E402


PLUGIN_SRC = textwrap.dedent(
    """
    from nightshift.verifiers import registry
    from nightshift.verifiers.base import Verdict, Verifier, VerificationResult

    class _PluginVerifier(Verifier):
        deliverable_type = "plugin-test-type"
        def verify(self, increment, *, config):
            return VerificationResult(deliverable_type=self.deliverable_type, verdict=Verdict.PASS)

    def register():
        registry.register(_PluginVerifier())
    """
)


class PluginLoaderTests(unittest.TestCase):
    def test_external_plugin_self_registers(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "nightshift_plugin_under_test.py"
            mod.write_text(PLUGIN_SRC, encoding="utf-8")

            self.assertIsNone(registry.get("plugin-test-type"))
            loaded = plugins.load_plugins(explicit=[tmp])

            self.assertIn("nightshift_plugin_under_test", loaded)
            self.assertIsNotNone(registry.get("plugin-test-type"))

    def test_missing_path_is_ignored(self):
        self.assertEqual(plugins.load_path("C:/no/such/dir/xyz"), [])


if __name__ == "__main__":
    unittest.main()
