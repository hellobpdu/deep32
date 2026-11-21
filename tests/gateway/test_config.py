"""Environment parsing and the uvicorn entry point."""

import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway_target  # noqa: E402,F401  the gateway copy under test, on sys.path (gateway_target.py)

import importlib
import ipaddress
import os
import sys
import unittest
from unittest import mock

from starlette.applications import Starlette

from support import CORE_TOKEN, OLD_TOKEN, TOKEN
from rag_edge import Config, parse_networks, parse_tokens

ENV = {"RAG_CORE_URL": "http://core.internal.test:8765/", "RAG_CORE_API_TOKEN": CORE_TOKEN,
       "TAILSCALE_DNS_NAME": "deep32-gateway.example.ts.net"}


class ConfigTest(unittest.TestCase):
    def test_from_env(self):
        config = Config.from_env({**ENV, "RAG_MCP_TOKENS": f"{TOKEN},{OLD_TOKEN}",
                                  "RAG_MCP_ALLOWED_IPS": "198.51.100.61, 192.0.2.0/24"})
        self.assertEqual(config.core_url, "http://core.internal.test:8765")
        self.assertEqual(config.core_token, CORE_TOKEN)
        self.assertEqual(config.mcp_tokens, (TOKEN, OLD_TOKEN))
        self.assertEqual(config.allowed_networks, (ipaddress.ip_network("198.51.100.61/32"),
                                                   ipaddress.ip_network("192.0.2.0/24")))

    def test_optional_keys_default_to_deny_all_and_no_ip_filter(self):
        config = Config.from_env(ENV)
        self.assertEqual((config.mcp_tokens, config.allowed_networks), ((), ()))

    def test_required_keys(self):
        for key in ENV:
            with self.subTest(missing=key):
                with self.assertRaises(RuntimeError) as caught:
                    Config.from_env({k: v for k, v in ENV.items() if k != key})
                self.assertIn(key, str(caught.exception))

    def test_core_token_comes_from_rag_core_api_token_only(self):
        env = {k: v for k, v in ENV.items() if k != "RAG_CORE_API_TOKEN"} | {"RAG_CORE_TOKEN": CORE_TOKEN}
        with self.assertRaises(RuntimeError) as caught:
            Config.from_env(env)
        self.assertIn("RAG_CORE_API_TOKEN", str(caught.exception))

    def test_parse_tokens(self):
        self.assertEqual(parse_tokens(f" {TOKEN} ,, {OLD_TOKEN},"), (TOKEN, OLD_TOKEN))
        self.assertEqual(parse_tokens(""), ())
        with self.assertLogs("rag_edge", "WARNING") as logs:
            self.assertEqual(parse_tokens(f"tiny-secret,{TOKEN}"), (TOKEN,))
        self.assertIn("ignoring 1 token(s) shorter than 32 chars", logs.output[0])
        self.assertNotIn("tiny-secret", "".join(logs.output))

    def test_parse_networks(self):
        self.assertEqual(parse_networks(""), ())
        self.assertEqual(parse_networks("fd7a:115c:a1e0::/48"), (ipaddress.ip_network("fd7a:115c:a1e0::/48"),))
        for bad in ("198.51.100", "198.51.100.61/33", "::g", "198.51.100.61/24", "fd7a:115c:a1e0::1/48"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_networks(bad)


class EntryPointTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("app", None)

    def test_app_module_builds_the_asgi_app(self):
        with mock.patch.dict(os.environ, {**ENV, "RAG_MCP_TOKENS": TOKEN}):
            app = importlib.import_module("app").app
        self.assertIsInstance(app, Starlette)

    def test_app_module_refuses_to_start_without_core_settings(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            importlib.import_module("app")


if __name__ == "__main__":
    unittest.main()
