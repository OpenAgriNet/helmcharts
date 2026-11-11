"""Checks for the experience and DSS adapter parts of setup.py.

    python3 -m unittest quick-start/bin/test_setup.py

No registry, no files in config/: only what turns a mode and an identity into
rendered text.
"""

import json, os, pathlib, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import setup as s

IDENTITY = {"participantId": "experience-api.local", "keyId": "1-abc",
            "signingPrivate": "sp", "signingPublic": "spub",
            "encrPrivate": "ep", "encrPublic": "epub"}


class UpstreamsForMode(unittest.TestCase):
    def test_single_mode_serves_both_layers_from_the_network_adapter(self):
        urls = s.network_upstreams("single-adapter")
        self.assertEqual(urls["__NETWORK_URL__"], "http://network-adapter:9201/network")
        self.assertEqual(urls["__PROVIDER_URL__"], "http://network-adapter:9201/provider")

    def test_multi_mode_sends_the_provider_layer_to_its_own_container(self):
        urls = s.network_upstreams("multi-adapter")
        self.assertEqual(urls["__NETWORK_URL__"], "http://network-adapter:9201/network")
        self.assertEqual(urls["__PROVIDER_URL__"], "http://provider-adapter:9200/provider")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(SystemExit):
            s.network_upstreams("dual-adapter")


class IdentityPlaceholders(unittest.TestCase):
    def test_every_key_is_named_under_the_prefix(self):
        self.assertEqual(s.identity_placeholders("EXPERIENCE", IDENTITY), {
            "__EXPERIENCE_SUBSCRIBER_ID__": "experience-api.local",
            "__EXPERIENCE_KEY_ID__": "1-abc",
            "__EXPERIENCE_SIGNING_PRIVATE__": "sp",
            "__EXPERIENCE_SIGNING_PUBLIC__": "spub",
            "__EXPERIENCE_ENCR_PRIVATE__": "ep",
            "__EXPERIENCE_ENCR_PUBLIC__": "epub"})


class RenderTemplate(unittest.TestCase):
    def test_fills_every_placeholder(self):
        self.assertEqual(s.render_template("a: __X__\nb: __Y__", {"__X__": "1", "__Y__": "2"}, "t"),
                         "a: 1\nb: 2")

    def test_a_placeholder_left_unfilled_is_named_and_refused(self):
        with self.assertRaises(SystemExit) as stopped:
            s.render_template("a: __X__\nb: __MISSING__", {"__X__": "1"}, "dss-adapter.yaml")
        self.assertIn("__MISSING__", str(stopped.exception))
        self.assertIn("dss-adapter.yaml", str(stopped.exception))


class AdapterValues(unittest.TestCase):
    def render_all(self, name, values):
        for template in s.ADAPTER_TEMPLATES[name]:
            text = (s.ADAPTERS / "experience" / f"{template}.yaml.tmpl").read_text()
            s.render_template(text, values, template)

    def test_the_experience_adapter_renders_without_any_dss_setting(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.render_all("experience", s.experience_values(IDENTITY))

    def test_the_dss_adapter_renders_without_any_experience_setting(self):
        with mock.patch.dict(os.environ, {"DSS_URL": "http://dss.example:8077"}, clear=True):
            self.render_all("dss", s.dss_values(IDENTITY))

    def test_the_dss_adapter_needs_the_url_of_the_dss(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as stopped:
                s.dss_values(IDENTITY)
        self.assertIn("DSS_URL", str(stopped.exception))


class DssAdapterTimeouts(unittest.TestCase):
    def outbound_module(self):
        text = (s.ADAPTERS / "experience" / "dss-adapter.yaml.tmpl").read_text()
        return text.split("name: dss-adapter-outbound")[1]

    def test_the_outbound_module_gives_the_network_30_seconds_to_start_answering(self):
        self.assertIn("responseHeaderTimeout: 30s", self.outbound_module())

    def test_the_outbound_module_sets_every_connection_pool_option(self):
        for option in ("maxIdleConns:", "maxIdleConnsPerHost:", "idleConnTimeout:"):
            self.assertIn(option, self.outbound_module())


class ExperienceRoutesToTheDssAdapter(unittest.TestCase):
    def routing(self, environ):
        with mock.patch.dict(os.environ, environ, clear=True):
            values = s.experience_values(IDENTITY)
        text = (s.ADAPTERS / "experience" / "routing-experience-adapter.yaml.tmpl").read_text()
        return s.render_template(text, values, "routing-experience-adapter")

    def test_it_defaults_to_the_dss_adapter_container(self):
        self.assertIn("http://dss-adapter:9204/dss", self.routing({}))

    def test_it_follows_dss_adapter_url(self):
        routing = self.routing({"DSS_ADAPTER_URL": "http://dss-host.example:9204"})
        self.assertIn("http://dss-host.example:9204/dss", routing)
        self.assertNotIn("dss-adapter:9204", routing)


class TempKeys(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = pathlib.Path(folder.name)
        patch = mock.patch.object(s, "KEYS", self.folder / "keys.json")
        patch.start()
        self.addCleanup(patch.stop)


class NeedAdapterIdentity(TempKeys):
    def test_missing_keys_name_that_adapters_own_register_command(self):
        with self.assertRaises(SystemExit) as stopped:
            s.need_adapter_identity("dss")
        self.assertIn("make register-dss", str(stopped.exception))

    def test_it_reads_only_the_keys_of_the_adapter_asked_for(self):
        (self.folder / "experience.json").write_text(json.dumps(IDENTITY))
        self.assertEqual(s.need_adapter_identity("experience"), IDENTITY)
        with self.assertRaises(SystemExit):
            s.need_adapter_identity("dss")


class RenderAdapterConfigs(TempKeys):
    def setUp(self):
        super().setUp()
        shutil.copytree(s.ADAPTERS / "experience", self.folder / "experience",
                        ignore=shutil.ignore_patterns("*.yaml"))
        patch = mock.patch.object(s, "ADAPTERS", self.folder)
        patch.start()
        self.addCleanup(patch.stop)
        (self.folder / "experience.json").write_text(json.dumps(IDENTITY))

    def rendered(self):
        return sorted(path.name for path in (self.folder / "experience").glob("*.yaml"))

    def test_it_renders_the_files_of_that_adapter_and_no_others(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s.render_adapter_configs("experience")
        self.assertEqual(self.rendered(),
                         ["experience-adapter.yaml", "routing-experience-adapter.yaml"])

    def test_it_refuses_an_adapter_that_has_no_keys_yet(self):
        with mock.patch.dict(os.environ, {"DSS_URL": "http://dss.example:8077"}, clear=True):
            with self.assertRaises(SystemExit):
                s.render_adapter_configs("dss")
        self.assertEqual(self.rendered(), [])


class PublishedUrl(unittest.TestCase):
    def test_a_node_entry_gets_an_https_url_named_after_its_id(self):
        self.assertEqual(s.published_url("experience-api.local"), "https://experience-api.local")


class IdentityFile(unittest.TestCase):
    def test_each_identity_keeps_its_own_keys_file(self):
        self.assertEqual(s.identity_file("dss").name, "dss.json")
        self.assertNotEqual(s.identity_file("dss"), s.KEYS)


if __name__ == "__main__":
    unittest.main()
