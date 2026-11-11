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


class RenderedConfigs(TempKeys):
    def setUp(self):
        super().setUp()
        shutil.copytree(s.ADAPTERS / "experience", self.folder / "experience",
                        ignore=shutil.ignore_patterns("*.yaml"))
        patch = mock.patch.object(s, "ADAPTERS", self.folder)
        patch.start()
        self.addCleanup(patch.stop)
        self.compose = self.folder / "docker-compose.experience.yml"
        patch = mock.patch.object(s, "EXTRA_COMPOSE", self.compose)
        patch.start()
        self.addCleanup(patch.stop)
        (self.folder / "experience.json").write_text(json.dumps(IDENTITY))

    def rendered(self):
        return sorted(path.name for path in (self.folder / "experience").glob("*.yaml"))


class RenderAdapterConfigs(RenderedConfigs):
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


class ExtraExperienceCompose(RenderedConfigs):
    def keep(self, *numbers):
        for number in numbers:
            extra = {**IDENTITY, "participantId": f"acme-{number}.example.org"}
            (self.folder / f"experience-{number}.json").write_text(json.dumps(extra))

    def render(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s.render_adapter_configs("experience")

    def test_each_extra_adapter_gets_a_service_that_mounts_its_own_config(self):
        self.keep(1)
        self.render()
        text = self.compose.read_text()
        self.assertIn("experience-adapter-1:", text)
        self.assertIn("config/adapters/experience/experience-adapter-1.yaml:/app/config/adapter.yaml", text)

    def test_the_host_port_follows_the_number_and_keeps_clear_of_the_first_two(self):
        self.keep(1, 3)
        self.render()
        text = self.compose.read_text()
        self.assertIn("127.0.0.1:9301:9203", text)
        self.assertIn("127.0.0.1:9303:9203", text)

    def test_no_extra_adapters_means_no_compose_file(self):
        self.render()
        self.assertFalse(self.compose.exists())


class PublishedUrl(unittest.TestCase):
    def test_a_node_entry_gets_an_https_url_named_after_its_id(self):
        self.assertEqual(s.published_url("experience-api.local"), "https://experience-api.local")


class IdentityFile(unittest.TestCase):
    def test_each_identity_keeps_its_own_keys_file(self):
        self.assertEqual(s.identity_file("dss").name, "dss.json")
        self.assertNotEqual(s.identity_file("dss"), s.KEYS)


class ExtraExperienceNumbers(TempKeys):
    def keep(self, *names):
        for name in names:
            (self.folder / name).write_text(json.dumps(IDENTITY))

    def test_none_registered_means_the_next_one_is_1(self):
        self.keep("experience.json", "dss.json")
        self.assertEqual(s.extra_experience_numbers(), [])
        self.assertEqual(s.next_experience_number(), 1)

    def test_the_next_number_follows_the_highest_one_kept(self):
        self.keep("experience.json", "experience-1.json", "experience-3.json")
        self.assertEqual(s.extra_experience_numbers(), [1, 3])
        self.assertEqual(s.next_experience_number(), 4)

    def test_keys_whose_registration_never_finished_are_not_counted(self):
        self.keep("experience-1.json")
        (self.folder / "experience-2.json").write_text(json.dumps({"participantId": "x.local.b"}))
        self.assertEqual(s.extra_experience_numbers(), [1])
        self.assertEqual(s.next_experience_number(), 2)

    def test_files_that_are_not_numbered_experience_keys_are_ignored(self):
        self.keep("experience-a.json", "experience-1.txt", "dss-1.json")
        self.assertEqual(s.extra_experience_numbers(), [])


class AddExperienceAdapter(TempKeys):
    def setUp(self):
        super().setUp()
        self.ensure_participant = self.patched("ensure_participant")
        self.patched("wait_for_registry")
        self.patched("key_osid", side_effect=lambda identity: {**identity, "keyId": "1-abc"})

    def patched(self, name, **options):
        patch = mock.patch.object(s, name, **options)
        self.addCleanup(patch.stop)
        return patch.start()

    def add(self, participant_id):
        s.add_experience_adapter(participant_id)

    def registered_ids(self):
        return [call.args[0] for call in self.ensure_participant.call_args_list]

    def keys_of(self, number):
        return json.loads((self.folder / f"experience-{number}.json").read_text())

    def test_the_id_is_registered_exactly_as_given(self):
        self.add("acme.org")
        self.add("acme.org.a")
        self.assertEqual(self.registered_ids(), ["acme.org", "acme.org.a"])

    def test_each_different_id_gets_the_next_file_number_and_keys_of_its_own(self):
        self.add("acme.org")
        self.add("other.org")
        self.assertEqual(self.keys_of(1)["participantId"], "acme.org")
        self.assertEqual(self.keys_of(2)["participantId"], "other.org")
        self.assertNotEqual(self.keys_of(1)["signingPublic"], self.keys_of(2)["signingPublic"])

    def test_the_same_id_again_reuses_its_adapter_and_its_keys(self):
        self.add("acme.org")
        before = self.keys_of(1)
        self.add("acme.org")
        self.assertEqual(self.keys_of(1), before)
        self.assertEqual(list(self.folder.glob("experience-*.json")), [self.folder / "experience-1.json"])

    def test_the_first_adapters_own_id_is_refused_and_nothing_is_made(self):
        (self.folder / "experience.json").write_text(json.dumps(IDENTITY))
        with self.assertRaises(SystemExit):
            self.add(IDENTITY["participantId"])
        self.assertEqual(list(self.folder.glob("experience-*.json")), [])
        self.ensure_participant.assert_not_called()

    def test_an_id_the_registry_would_refuse_is_stopped_before_any_key_is_made(self):
        for bad in ("Acme.org", "acme", "acme..org", "-acme.org", ""):
            with self.assertRaises(SystemExit, msg=bad):
                self.add(bad)
        self.assertEqual(list(self.folder.glob("experience-*.json")), [])
        self.ensure_participant.assert_not_called()

    def test_adding_leaves_the_first_adapters_keys_alone(self):
        self.add("acme.org")
        self.assertFalse((self.folder / "experience.json").exists())


class RenderExtraExperienceAdapters(RenderedConfigs):
    def setUp(self):
        super().setUp()
        extra = {**IDENTITY, "participantId": "experience-api.local.a"}
        (self.folder / "experience-1.json").write_text(json.dumps(extra))

    def test_each_extra_adapter_gets_a_config_signed_as_itself(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s.render_adapter_configs("experience")
        text = (self.folder / "experience" / "experience-adapter-1.yaml").read_text()
        self.assertIn("subscriberId: experience-api.local.a", text)
        self.assertNotIn("experience-api.local\n", text)

    def test_the_routing_file_is_shared_so_only_the_adapter_file_is_added(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s.render_adapter_configs("experience")
        self.assertEqual(self.rendered(),
                         ["experience-adapter-1.yaml", "experience-adapter.yaml",
                          "routing-experience-adapter.yaml"])

    def test_the_dss_adapter_is_not_given_extras(self):
        (self.folder / "dss.json").write_text(json.dumps(IDENTITY))
        with mock.patch.dict(os.environ, {"DSS_URL": "http://dss.example:8077"}, clear=True):
            s.render_adapter_configs("dss")
        self.assertNotIn("experience-adapter-1.yaml", self.rendered())


if __name__ == "__main__":
    unittest.main()
