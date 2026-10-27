"""Checks for publishing and verifying the Network Descriptor and JWKS.

    python3 -m unittest quick-start/bin/test_oanfed.py

No network, no registry: the registry is a stub returning one entry.
"""

import base64, copy, json, os, pathlib, sys, tempfile, unittest, uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import oanfed as o
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NETWORK = "network-a.example.org"
# The signing key's own osid: what a signature names, and what a verifier
# matches when it walks a record's keys.
KEY_OSID = "1-6b9f77c2-139a-4c26-a278-bbb73384ea8a"
# The entry's own osid. Nothing names a key by it.
ENTRY_OSID = "1-dd2e40a3-8d4d-427e-bc77-266befb01d00"


def raw_public(private):
    return o.public_of(private)


class StubRegistry:
    entry = None

    def __init__(self, *_):
        pass

    def participant(self, participant_id):
        return self.entry if participant_id == self.entry["participantId"] else None

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
        self._real = o.ReadOnlyRegistry
        o.ReadOnlyRegistry = StubRegistry
        self.key_file = self.write_key(self.traffic, self.dir / "keys" / "keys.json")

    def tearDown(self):
        o.ReadOnlyRegistry = self._real

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
                "key": base64.b64encode(raw_public(private)).decode(), "osid": KEY_OSID, **extra}

    def publish(self, revision=1, **overrides):
        args = SimpleNamespace(
            network_id=NETWORK, registry_url="http://registry", registry_timeout=1,
            operator="", revision=revision, operating_profile="OAN/v0.1,Beckn/v2",
            status="Active", projection_ttl=None, out=str(self.dir),
            keys=str(self.key_file))
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
        # Named by the key's osid, never the entry's: a verifier resolves a key
        # by walking the record's keys and matching on theirs.
        self.assertEqual(descriptor["signature"]["keyId"], KEY_OSID)
        self.assertEqual([k["kid"] for k in keyset["keys"]], [KEY_OSID])
        self.assertNotIn(ENTRY_OSID, json.dumps([descriptor, keyset]))
        self.assertEqual(keyset["keys"][0], {"kty": "OKP", "crv": "Ed25519", "kid": KEY_OSID,
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

    def test_a_key_without_an_osid_is_refused(self):
        StubRegistry.entry["keys"][0].pop("osid")
        with self.assertRaisesRegex(o.FederationError, "has no osid"):
            self.publish()
        self.assertFalse((self.dir / ".well-known" / "openagrinet").exists())

    def test_the_key_id_names_the_key_we_signed_with(self):
        # Two keys on the entry. The documents name the one holding the private
        # half we signed with, not whichever is listed first.
        other = Ed25519PrivateKey.generate()
        StubRegistry.entry["keys"].insert(0, self.registry_key(other, osid="1-next-key"))
        descriptor, keyset = self.publish()
        self.assertEqual(descriptor["signature"]["keyId"], KEY_OSID)
        self.assertEqual([k["kid"] for k in keyset["keys"]], [KEY_OSID])

    def test_readable_key_is_refused(self):
        os.chmod(self.key_file, 0o644)
        with self.assertRaisesRegex(o.FederationError, "chmod 600"):
            self.publish()

    def test_unregistered_key_is_refused(self):
        other = self.write_key(Ed25519PrivateKey.generate(), self.dir / "keys" / "other.json")
        with self.assertRaisesRegex(o.FederationError, "not an active key registered"):
            self.publish(keys=str(other))

    def test_missing_or_unreadable_keys_file_fails_cleanly(self):
        with self.assertRaisesRegex(o.FederationError, "run `make keys` first"):
            self.publish(keys=str(self.dir / "absent.json"))
        bad = self.dir / "keys" / "bad.json"
        bad.write_text("{}")
        bad.chmod(0o600)
        with self.assertRaisesRegex(o.FederationError, "no readable signingPrivate"):
            self.publish(keys=str(bad))

    def test_revision_must_increase(self):
        self.publish(3)
        for revision in (3, 2):
            with self.assertRaisesRegex(o.FederationError, "does not increase"):
                self.publish(revision)

    def test_corrupt_published_descriptor_is_not_read_as_revision_zero(self):
        self.publish(5)
        (self.dir / ".well-known" / "openagrinet").write_text("{")
        with self.assertRaises(o.FederationError):
            self.publish(1)

    def test_only_usable_registry_keys_can_sign(self):
        revoked, expired, upcoming = (Ed25519PrivateKey.generate() for _ in range(3))
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        StubRegistry.entry["keys"] += [
            self.registry_key(revoked, status="revoked"),
            self.registry_key(expired, validUntil="2020-01-01T00:00:00Z"),
            self.registry_key(upcoming, validFrom=future),
            {"alg": "x25519", "status": "active", "key": base64.b64encode(b"e" * 32).decode()},
        ]
        self.assertEqual(o.registered_signing_keys(StubRegistry.entry),
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
        self.assertEqual(o.registered_signing_keys(StubRegistry.entry), [])

    def test_the_same_registry_key_twice_still_signs(self):
        StubRegistry.entry["keys"].append(self.registry_key(self.traffic))
        self.assertEqual(o.registered_signing_keys(StubRegistry.entry), [raw_public(self.traffic)])
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
        self.assertEqual(o.registered_signing_keys(StubRegistry.entry), [])

    def test_key_path_that_is_a_folder_says_so(self):
        folder = self.dir / "keys-folder"
        folder.mkdir()
        with self.assertRaisesRegex(o.FederationError, "run `make keys` first"):
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
        with self.assertRaisesRegex(o.FederationError, "not in this registry"):
            self.publish(network_id="other.example.org")


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
        # Sunbird prepends "1-" to every osid it stores, including one supplied
        # for a nested key -- measured against a live registry, where passing a
        # kid whole produced 1-1-<uuid> and resolved to no key.
        record["osid"] = "1-" + (record.pop("osid", None) or str(uuid.uuid4()))
        for key in record.get("keys") or []:
            key["osid"] = "1-" + (key.pop("osid", None) or str(uuid.uuid4()))
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

    def test_admit_files_the_peer_key_under_the_kid_it_signs_with(self):
        self.admit()
        held = self.held()
        # On the KEY, not the entry: a verifier filters by participantId and then
        # matches a key's own osid, so a kid filed anywhere else resolves nothing.
        self.assertEqual([k["osid"] for k in held["keys"]], [KEY_OSID])
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

    def test_refresh_files_a_rotated_key_under_its_new_kid(self):
        self.admit()
        rotated = "1-11111111-2222-4333-8444-555555555555"
        self.traffic = Ed25519PrivateKey.generate()
        StubRegistry.entry["keys"] = [self.registry_key(self.traffic, osid=rotated)]
        self.write_key(self.traffic, self.key_file)
        self.publish(revision=2)
        self.refresh()
        held = self.held()
        self.assertEqual([k["key"] for k in held["keys"]],
                         [base64.b64encode(raw_public(self.traffic)).decode()])
        # A new key is a new kid, and the entry follows it rather than refusing.
        self.assertEqual([k["osid"] for k in held["keys"]], [rotated])

    def test_replays_are_refused(self):
        self.admit()
        self.ours.rows[NETWORK]["revision"] = 5
        with self.assertRaisesRegex(o.FederationError, "replay"):
            self.refresh()
        self.ours.rows[NETWORK].update(revision=1, baseUrl="https://elsewhere.example/discover")
        with self.assertRaisesRegex(o.FederationError, "different contents"):
            self.refresh()

    def test_a_kid_that_is_not_a_record_id_is_refused(self):
        StubRegistry.entry["keys"][0]["osid"] = "not-a-record-id"
        self.publish(revision=2)
        with self.assertRaisesRegex(o.FederationError, "not a registry record id"):
            self.admit()
        self.assertIsNone(self.held())

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
