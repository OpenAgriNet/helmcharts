"""Checks for publishing and verifying the Network Descriptor and JWKS.

    python3 -m unittest quick-start/bin/test_oanfed.py

No network, no registry: the registry is a stub holding one entry.
"""

import base64, copy, json, os, pathlib, shutil, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import oanfed as o
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NETWORK = "network-a.example.org"
# The registry entry's own osid. Nothing names the key by it.
ENTRY_OSID = "1-dd2e40a3-8d4d-427e-bc77-266befb01d00"
# The key's own osid: the network's key id.
KEY_ID = "1-6b9f77c2-139a-4c26-a278-bbb73384ea8a"


def raw_public(private):
    return o.public_of(private)


class StubRegistry:
    entry = None
    own_id = NETWORK
    fail_update = False

    def participant(self, participant_id):
        return self.entry if participant_id == self.entry["participantId"] else None

    def update(self, record):
        if StubRegistry.fail_update:
            raise o.FederationError("registry PUT returned 500")
        StubRegistry.entry = record

    def schema_packs(self):
        return ["https://openagrinet.github.io/network-specs/schema/MandiPrice/v0.1"]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.traffic = Ed25519PrivateKey.generate()
        StubRegistry.entry = {
            "participantId": NETWORK, "name": "Network A", "osid": ENTRY_OSID,
            "baseUrl": f"https://{NETWORK}/network",
            "keys": [self.registry_key(self.traffic)],
        }
        StubRegistry.own_id, StubRegistry.fail_update = NETWORK, False
        self._real = o.open_registry
        o.open_registry = StubRegistry
        self._env = dict(os.environ)
        os.environ["NETWORK_OPERATOR_NAME"] = "Network A Operator"
        os.environ.pop("FEDERATION_PROJECTION_TTL", None)
        self.key_file = self.write_key(self.traffic, self.dir / "keys" / "keys.json")

    def tearDown(self):
        o.open_registry = self._real
        os.environ.clear()
        os.environ.update(self._env)

    @staticmethod
    def write_key(private, path):
        """A keys.json the way setup.py writes it: standard base64 seeds."""
        seed = private.private_bytes(o.ser.Encoding.Raw, o.ser.PrivateFormat.Raw,
                                     o.ser.NoEncryption())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"participantId": NETWORK,
                                    "signingPrivate": base64.b64encode(seed).decode()}))
        path.chmod(0o600)
        return path

    @staticmethod
    def registry_key(private, **extra):
        return {"alg": "ed25519", "status": "active", "validFrom": "2026-01-01T00:00:00Z",
                "key": base64.b64encode(raw_public(private)).decode(), "osid": KEY_ID, **extra}

    def publish(self, **overrides):
        args = SimpleNamespace(operating_profile=None, status="Active",
                               out=str(self.dir), keys=str(self.key_file))
        vars(args).update(overrides)
        o.cmd_publish(args)
        well_known = self.dir / ".well-known"
        return (json.loads((well_known / "openagrinet").read_text()),
                json.loads((well_known / "jwks.json").read_text()))


class Canonical(unittest.TestCase):
    def test_keys_sort_by_utf16_code_units(self):
        # RFC 8785 section 3.2.3: U+1F600 sorts BEFORE U+FB33 in UTF-16.
        keys = ["€", "\r", "דּ", "1", "\U0001F600", "\u0080", "ö"]
        out = o._jcs({k: 0 for k in keys})
        order = [json.loads("{" + p + "}").popitem()[0] for p in out[1:-1].split(",")]
        self.assertEqual(order, ["\r", "1", "\u0080", "ö", "€", "\U0001F600", "דּ"])

    def test_escaping_and_no_whitespace(self):
        self.assertEqual(o._jcs({"b": "a/\u001f\né", "a": [1, True, None]}),
                         '{"a":[1,true,null],"b":"a/\\u001f\\né"}')

    def test_floats_huge_ints_and_lone_surrogates_are_refused(self):
        for bad in (1.5, 2 ** 53, "\ud800"):
            with self.assertRaises(o.FederationError):
                o._jcs({"x": bad})

    def test_signature_is_excluded(self):
        d = {"a": 1}
        self.assertEqual(o.canonical(d), o.canonical({**d, "signature": {"value": "x"}}))


class Publish(Base):
    def test_documents_verify_with_the_one_registered_key(self):
        descriptor, keyset = self.publish()
        o.verify(descriptor, keyset)
        # Named by the key's osid, never the entry's.
        self.assertEqual(descriptor["signature"]["keyId"], KEY_ID)
        self.assertEqual([k["kid"] for k in keyset["keys"]], [KEY_ID])
        self.assertNotIn(ENTRY_OSID, json.dumps([descriptor, keyset]))
        self.assertEqual(keyset["keys"][0], {"kty": "OKP", "crv": "Ed25519", "kid": KEY_ID,
                                             "x": o.b64url(raw_public(self.traffic))})
        self.assertEqual(descriptor["services"]["discovery"], f"https://{NETWORK}/network/discover")
        self.assertNotIn("operationalKeys", descriptor)

    def test_schema_packs_is_one_url_serving_the_list(self):
        descriptor, _ = self.publish()
        self.assertEqual(descriptor["schemaPacks"], f"https://{NETWORK}/.well-known/schema-packs.json")
        served = json.loads((self.dir / ".well-known" / "schema-packs.json").read_text())
        self.assertEqual(served, {"schemaPacks": StubRegistry().schema_packs()})
        for field in ("version", "networkId", "name", "operator", "revision", "status",
                      "issuedAt", "operatingProfile", "schemaPacks", "services"):
            self.assertIn(field, descriptor)
        self.assertEqual(descriptor["operatingProfile"], ["OAN/v0.1", "Beckn/v2"])

    def test_a_peer_reads_the_schema_packs_we_publish(self):
        self.publish()
        fetcher = o.Fetcher()
        fetcher._get = lambda _, path: json.loads((self.dir / path.lstrip("/")).read_text())
        self.assertEqual(fetcher.fetch(NETWORK)[2], StubRegistry().schema_packs())
        # A bare list, without the schemaPacks wrapper, is not the agreed shape.
        packs_file = self.dir / ".well-known" / "schema-packs.json"
        packs_file.write_text(json.dumps(StubRegistry().schema_packs()))
        with self.assertRaises(o.FederationError):
            fetcher.fetch(NETWORK)

    def test_entry_without_an_osid_still_publishes(self):
        # A registry other than Sunbird RC assigns no osid; nothing here needs one.
        del StubRegistry.entry["osid"]
        descriptor, keyset = self.publish()
        o.verify(descriptor, keyset)

    def test_registry_key_without_an_osid_is_refused(self):
        del StubRegistry.entry["keys"][0]["osid"]
        with self.assertRaisesRegex(o.FederationError, "has no osid"):
            self.publish()
        self.assertFalse((self.dir / ".well-known" / "openagrinet").exists())

    def test_every_registered_key_is_published_during_a_rotation(self):
        # Two keys on the entry: ours signs, both are published, so a peer
        # holding either still verifies.
        other = Ed25519PrivateKey.generate()
        StubRegistry.entry["keys"].insert(0, self.registry_key(other, osid="1-next-key"))
        descriptor, keyset = self.publish()
        self.assertEqual(descriptor["signature"]["keyId"], KEY_ID)
        self.assertEqual({k["kid"] for k in keyset["keys"]}, {"1-next-key", KEY_ID})
        o.verify(descriptor, keyset)

    def test_readable_key_is_refused(self):
        os.chmod(self.key_file, 0o644)
        with self.assertRaisesRegex(o.FederationError, "chmod 600"):
            self.publish()

    def test_unregistered_key_is_refused(self):
        other = self.write_key(Ed25519PrivateKey.generate(), self.dir / "keys" / "other.json")
        with self.assertRaisesRegex(o.FederationError, "not an active key registered"):
            self.publish(keys=str(other))

    def test_missing_or_unreadable_keys_file_fails_cleanly(self):
        with self.assertRaisesRegex(o.FederationError, "run setup.py first"):
            self.publish(keys=str(self.dir / "absent.json"))
        bad = self.dir / "keys" / "bad.json"
        bad.write_text("{}")
        bad.chmod(0o600)
        with self.assertRaisesRegex(o.FederationError, "no readable signingPrivate"):
            self.publish(keys=str(bad))

    def test_revision_counts_up_and_is_kept_on_the_entry(self):
        self.assertEqual([self.publish()[0]["revision"] for _ in range(3)], [1, 2, 3])
        self.assertEqual(StubRegistry.entry["revision"], 3)

    def test_losing_well_known_does_not_restart_the_revision(self):
        self.publish()
        self.publish()
        shutil.rmtree(self.dir / ".well-known")
        self.assertEqual(self.publish()[0]["revision"], 3)

    def test_a_revision_only_on_disk_keeps_counting(self):
        # Published before the entry held the revision.
        self.publish()
        del StubRegistry.entry["revision"]
        self.assertEqual(self.publish()[0]["revision"], 2)

    def test_registry_write_failing_publishes_nothing(self):
        StubRegistry.fail_update = True
        with self.assertRaisesRegex(o.FederationError, "PUT"):
            self.publish()
        self.assertFalse((self.dir / ".well-known").exists())

    def test_corrupt_published_descriptor_is_not_read_as_revision_zero(self):
        self.publish()
        (self.dir / ".well-known" / "openagrinet").write_text("{")
        with self.assertRaises(o.FederationError):
            self.publish()

    def test_operator_is_the_configured_organisation(self):
        self.assertEqual(self.publish()[0]["operator"], {"name": "Network A Operator"})
        for unset in ("", "<organisation that operates this network>"):
            os.environ["NETWORK_OPERATOR_NAME"] = unset
            with self.subTest(unset=unset), \
                    self.assertRaisesRegex(o.FederationError, "NETWORK_OPERATOR_NAME"):
                self.publish()

    def test_projection_ttl_comes_from_the_environment(self):
        os.environ["FEDERATION_PROJECTION_TTL"] = "30"
        self.assertEqual(self.publish()[0]["projectionTtl"], 30)
        os.environ["FEDERATION_PROJECTION_TTL"] = "soon"
        with self.assertRaisesRegex(o.FederationError, "not a number"):
            self.publish()

    def test_only_usable_registry_keys_can_sign(self):
        revoked, expired, upcoming = (Ed25519PrivateKey.generate() for _ in range(3))
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        StubRegistry.entry["keys"] += [
            self.registry_key(revoked, status="revoked"),
            self.registry_key(expired, validUntil="2020-01-01T00:00:00Z"),
            self.registry_key(upcoming, validFrom=future),
            {"alg": "x25519", "status": "active", "key": base64.b64encode(b"e" * 32).decode()},
        ]
        self.assertEqual(list(o.registered_signing_keys(StubRegistry.entry)),
                         [raw_public(self.traffic), raw_public(upcoming)])
        for private in (revoked, expired):
            with self.subTest(private=private), \
                    self.assertRaisesRegex(o.FederationError, "not an active key registered"):
                self.publish(keys=str(self.write_key(private, self.dir / "keys" / "other.json")))

    def test_malformed_registry_keys_fail_cleanly(self):
        for bad in ({"validUntil": "not-a-date"}, {"key": "!!!not base64!!!"},
                    {"key": base64.b64encode(b"short").decode()}):
            StubRegistry.entry["keys"] = [self.registry_key(self.traffic, **bad)]
            with self.subTest(bad=bad), self.assertRaises(o.FederationError):
                self.publish()

    def test_registry_expiry_without_timezone_is_read_as_utc(self):
        StubRegistry.entry["keys"] = [self.registry_key(self.traffic,
                                                        validUntil="2020-01-01T00:00:00")]
        self.assertEqual(len(o.registered_signing_keys(StubRegistry.entry)), 0)

    def test_the_same_registry_key_twice_still_signs(self):
        StubRegistry.entry["keys"].append(self.registry_key(self.traffic))
        self.assertEqual(list(o.registered_signing_keys(StubRegistry.entry)), [raw_public(self.traffic)])
        _, keyset = self.publish()
        self.assertEqual(len(keyset["keys"]), 1)

    def test_registry_expiry_with_any_fraction_length_is_read(self):
        for until in ("2099-01-01T00:00:00.5Z", "2099-01-01T00:00:00.123456789Z",
                      "2099-01-01T00:00:00.1234567+05:30"):
            StubRegistry.entry["keys"] = [self.registry_key(self.traffic, validUntil=until)]
            with self.subTest(until=until):
                self.assertEqual(len(o.registered_signing_keys(StubRegistry.entry)), 1)
        StubRegistry.entry["keys"] = [self.registry_key(self.traffic,
                                                        validUntil="2020-01-01T00:00:00.123456789Z")]
        self.assertEqual(len(o.registered_signing_keys(StubRegistry.entry)), 0)

    def test_key_path_that_is_a_folder_says_so(self):
        folder = self.dir / "keys-folder"
        folder.mkdir()
        with self.assertRaisesRegex(o.FederationError, "run setup.py first"):
            self.publish(keys=str(folder))

    def test_no_usable_traffic_key_is_refused(self):
        StubRegistry.entry["keys"] = [self.registry_key(self.traffic, status="revoked")]
        with self.assertRaisesRegex(o.FederationError, "no active Ed25519 key"):
            self.publish()

    def test_invalid_descriptor_is_never_written(self):
        StubRegistry.entry["baseUrl"] = "http://insecure/network"
        with self.assertRaisesRegex(o.FederationError, "services.discovery"):
            self.publish()
        self.assertFalse((self.dir / ".well-known" / "openagrinet").exists())

    def test_unregistered_network_is_refused(self):
        StubRegistry.own_id = "other.example.org"
        with self.assertRaisesRegex(o.FederationError, "not in this registry"):
            self.publish()


class PeerAccess(unittest.TestCase):
    def test_only_verify_can_fetch_over_http(self):
        # Without --scheme a command fetches over https; only verify takes it.
        bare = SimpleNamespace(ca_file="", resolve="", timeout=1)
        self.assertEqual(o.fetcher_from(bare).scheme, "https")
        import argparse, contextlib, io
        parser = argparse.ArgumentParser()
        o.add_peer_access(parser)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["--scheme", "http"])


class Verify(Base):
    def setUp(self):
        super().setUp()
        self.descriptor, self.keyset = self.publish()

    def refuses(self, descriptor=None, keyset=None, match=""):
        with self.assertRaisesRegex(o.FederationError, match):
            o.verify(self.descriptor if descriptor is None else descriptor,
                     self.keyset if keyset is None else keyset)

    def test_any_change_to_a_signed_field_is_caught(self):
        for field, value in (("status", "Suspended"), ("revision", 99),
                             ("services", {"discovery": "https://evil.example/d"})):
            tampered = {**self.descriptor, field: value}
            with self.subTest(field=field):
                self.refuses(tampered)

    def test_malformed_signatures_fail_cleanly(self):
        for signature in (None, {}, {"algorithm": "EdDSA"},
                          {**self.descriptor["signature"], "keyId": "nope"},
                          {**self.descriptor["signature"], "value": "!!not-base64!!"},
                          {**self.descriptor["signature"], "value": ""}):
            with self.subTest(signature=signature):
                self.refuses({**self.descriptor, "signature": signature})

    def test_malformed_jwks(self):
        dup = copy.deepcopy(self.keyset)
        dup["keys"].append(dict(dup["keys"][0]))
        short = copy.deepcopy(self.keyset)
        short["keys"][0]["x"] = o.b64url(b"short")
        for keyset in ({}, {"keys": "x"}, {"keys": [{"kty": "OKP"}]}, dup, short):
            with self.subTest(keyset=keyset):
                self.refuses(keyset=keyset)

    def test_contract_fields(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for field, value in (("version", "0.1"), ("networkId", "Not A Domain"),
                             ("name", ""), ("operator", {}), ("revision", 0),
                             ("revision", True), ("status", "active"), ("issuedAt", future),
                             ("issuedAt", "2026-02-30T00:00:00Z"), ("operatingProfile", []),
                             ("schemaPacks", ["https://x/y"]),
                             ("schemaPacks", f"http://{NETWORK}/.well-known/schema-packs.json"),
                             ("schemaPacks", "https://evil.example/.well-known/schema-packs.json"),
                             ("projectionTtl", -1)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(o.FederationError):
                    o.validate_descriptor({**self.descriptor, field: value})

    def test_schema_pack_list(self):
        self.assertEqual(o.validate_schema_packs(["https://a/b"]), ["https://a/b"])
        for packs in ("https://a/b", ["http://a/b"], ["https://a/b", "https://a/b"], {}):
            with self.subTest(packs=packs), self.assertRaises(o.FederationError):
                o.validate_schema_packs(packs)

    def test_key_named_by_a_different_kid_is_refused(self):
        renamed = copy.deepcopy(self.keyset)
        renamed["keys"][0]["kid"] = "1-00000000-0000-0000-0000-000000000000"
        self.refuses(keyset=renamed, match="no published")


PEER_PACK = "https://openagrinet.github.io/network-specs/schema/MandiPrice/v0.1"
OUR_PACK = "https://openagrinet.github.io/network-specs/schema/WeatherObservation/v0.1"


class OurRegistry:
    """Our registry in memory, behaving as Sunbird RC does where admission depends on it:
    a supplied osid gets "1-" back on create, lists are replaced, objects merged, {} clears."""
    own_id = "network-b.example.org"

    def __init__(self):
        self.rows = {}

    def participant(self, participant_id):
        return copy.deepcopy(self.rows.get(participant_id))

    def by_osid(self, osid):
        return [copy.deepcopy(r) for r in self.rows.values() if r["osid"] == osid]

    def schema_packs(self):
        return [OUR_PACK]

    def create(self, record):
        record = copy.deepcopy(record)
        record["osid"] = "1-" + record["osid"]
        self.rows[record["participantId"]] = record

    def update(self, record):
        held = self.rows[record["participantId"]]
        assert held["osid"] == record["osid"]
        new = copy.deepcopy(record)
        roles = new.pop("federationRole", None)
        held.update(new)
        if roles == {}:
            held.pop("federationRole", None)
        elif roles is not None:
            held["federationRole"] = {**held.get("federationRole", {}), **roles}


class Admission(Base):
    def setUp(self):
        super().setUp()
        self.ours = OurRegistry()
        fetcher = o.Fetcher()
        fetcher._get = lambda _, path: json.loads((self.dir / path.lstrip("/")).read_text())
        self._real_open, self._real_fetcher = o.open_registry, o.fetcher_from
        o.open_registry = lambda: self.ours
        o.fetcher_from = lambda _args: fetcher
        self.publish(revision=1)

    def tearDown(self):
        o.open_registry, o.fetcher_from = self._real_open, self._real_fetcher
        super().tearDown()

    def admit(self, direction="source", packs=PEER_PACK, network_id=NETWORK):
        o.cmd_admit(SimpleNamespace(network_id=network_id, direction=direction, schema_packs=packs))

    def refresh(self):
        o.cmd_refresh(SimpleNamespace(network_id=NETWORK))

    def suspend(self, direction):
        o.cmd_suspend(SimpleNamespace(network_id=NETWORK, direction=direction))

    def held(self):
        return self.ours.participant(NETWORK)

    def test_admit_files_the_peer_under_its_entry_osid(self):
        self.admit()
        held = self.held()
        self.assertEqual(held["osid"], KEY_ID)
        self.assertEqual((held["role"], held["revision"], held["baseUrl"]),
                         (["network"], 1, f"https://{NETWORK}/network/discover"))
        self.assertEqual([k["key"] for k in held["keys"]],
                         [base64.b64encode(raw_public(self.traffic)).decode()])
        self.assertEqual(held["federationRole"]["source"]["schemaPacks"], [PEER_PACK])
        self.assertEqual(held["federationRole"]["source"]["descriptorRevision"], 1)

    def test_admit_covers_only_packs_on_offer(self):
        with self.assertRaisesRegex(o.FederationError, "does not publish"):
            self.admit(packs=OUR_PACK)
        with self.assertRaisesRegex(o.FederationError, "this network does not publish"):
            self.admit(direction="requester", packs=PEER_PACK)
        self.assertIsNone(self.held())

    def test_a_second_direction_keeps_the_first(self):
        self.admit()
        self.admit(direction="requester", packs=OUR_PACK)
        roles = self.held()["federationRole"]
        self.assertEqual((roles["source"]["schemaPacks"], roles["requester"]["schemaPacks"]),
                         ([PEER_PACK], [OUR_PACK]))

    def test_refresh_follows_the_documents_but_never_the_admission(self):
        self.admit()
        before = self.held()["federationRole"]
        self.publish(revision=2)
        self.refresh()
        self.assertEqual(self.held()["revision"], 2)
        self.assertEqual(self.held()["federationRole"], before)

    def test_refresh_files_a_rotated_key(self):
        self.admit()
        self.traffic = Ed25519PrivateKey.generate()
        StubRegistry.entry["keys"] = [self.registry_key(self.traffic)]
        self.write_key(self.traffic, self.key_file)
        self.publish(revision=2)
        self.refresh()
        self.assertEqual([k["key"] for k in self.held()["keys"]],
                         [base64.b64encode(raw_public(self.traffic)).decode()])
        self.assertEqual(self.held()["osid"], KEY_ID)

    def test_replays_are_refused(self):
        self.admit()
        self.ours.rows[NETWORK]["revision"] = 5
        with self.assertRaisesRegex(o.FederationError, "replay"):
            self.refresh()
        self.ours.rows[NETWORK].update(revision=1, baseUrl="https://elsewhere.example/discover")
        with self.assertRaisesRegex(o.FederationError, "different contents"):
            self.refresh()

    def test_refused_kids(self):
        self.ours.rows["other.example.org"] = {"participantId": "other.example.org", "osid": KEY_ID}
        with self.assertRaisesRegex(o.FederationError, "already has osid"):
            self.admit()
        StubRegistry.entry["osid"] = "not-a-record-id"
        self.publish(revision=2)
        with self.assertRaisesRegex(o.FederationError, "not a registry record id"):
            self.admit()
        self.assertIsNone(self.held())

    def test_a_peer_cannot_change_the_osid_it_is_filed_under(self):
        self.admit()
        StubRegistry.entry["osid"] = "1-00000000-0000-4000-8000-000000000000"
        self.publish(revision=2)
        with self.assertRaisesRegex(o.FederationError, "cannot change"):
            self.refresh()

    def test_suspend_withdraws_one_direction(self):
        self.admit()
        self.admit(direction="requester", packs=OUR_PACK)
        self.suspend("requester")
        self.assertEqual(list(self.held()["federationRole"]), ["source"])
        self.suspend("source")
        self.assertNotIn("federationRole", self.held())
        with self.assertRaisesRegex(o.FederationError, "not admitted"):
            self.refresh()

    def test_our_own_id_and_inactive_peers_are_refused(self):
        with self.assertRaisesRegex(o.FederationError, "is this network"):
            o.cmd_suspend(SimpleNamespace(network_id=OurRegistry.own_id, direction="source"))
        self.publish(revision=2, status="Suspended")
        with self.assertRaisesRegex(o.FederationError, "declares itself Suspended"):
            self.admit()


if __name__ == "__main__":
    unittest.main()
