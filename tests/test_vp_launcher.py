import importlib
import socket
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch


launcher = importlib.import_module("start-vp")


class VPLauncherTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.addresses = self.stack.enter_context(patch.object(launcher, "show_addresses"))
        self.factory = self.stack.enter_context(patch.object(launcher, "create_app"))
        self.factory.return_value = SimpleNamespace(config={})
        self.serve = self.stack.enter_context(patch.object(launcher, "serve_app"))

    def test_loopback_listener_forces_lan_server_onto_another_port(self):
        with socket.create_server(("127.0.0.1", 0)) as occupied:
            port = occupied.getsockname()[1]
            with patch.object(launcher, "PORTS", [port, 0]), patch.object(
                launcher, "same_service", return_value=False
            ) as probe:
                launcher.launch(1802, "icpc", None)
            self.assertEqual(probe.call_args.args[0], port)
            chosen = self.addresses.call_args.args[0]
            self.assertGreater(chosen, 0)
            self.assertNotEqual(chosen, port)
            self.serve.assert_called_once()

    def test_existing_match_is_reused_even_when_first_port_is_free(self):
        with socket.create_server(("127.0.0.1", 0)) as existing:
            port = existing.getsockname()[1]
            with patch.object(launcher, "PORTS", [0, port]), patch.object(
                launcher, "same_service", return_value=True
            ):
                launcher.launch(1802, "icpc", None)
        self.addresses.assert_called_once_with(port)
        self.factory.assert_not_called()
        self.serve.assert_not_called()

    def test_ccpc_reveal_thresholds_affect_service_identity(self):
        identities = []
        with patch.object(launcher, "PORTS", [0]):
            for count in (20, 50):
                url = f"https://vp.xcpc.link/?contest=ccpc2026ladies&count={count}"
                launcher.launch(1802, "ccpc26", url)
                self.assertEqual(self.factory.call_args.kwargs["rankland_url"], url)
                identities.append(self.factory.return_value.config["VP_LAUNCH_ID"])
        self.assertNotEqual(*identities)
