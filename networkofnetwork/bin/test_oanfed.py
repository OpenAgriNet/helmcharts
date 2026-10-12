"""Checks for publishing and verifying the Network Descriptor and JWKS.

    python3 -m unittest networkofnetwork/bin/test_oanfed.py

No network, no registry: the registry is a stub returning one entry.
"""

import base64, copy, json, os, pathlib, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import oanfed as o
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NETWORK = "network-a.example.org"


def raw_public(private):
    return o.public_of(private)


class StubRegistry:
    entry = None

    def __init__(self, *_):
        pass

    def participant(self, participant_id):
        return self.entry if participant_id == NETWORK else None

    def schema_packs(self):
        return ["https://openagrinet.github.io/network-specs/schema/MandiPrice/v0.1"]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.traffic = Ed25519PrivateKey.generate()
        StubRegistry.entry = {
            "participantId": NETWORK, "name": "Network A",
            "baseUrl": f"https://{NETWORK}/network/discover",
            "keys": [self.registry_key(self.traffic)],
        }
        self._real = o.ReadOnlyRegistry
        o.ReadOnlyRegistry = StubRegistry

    def tearDown(self):
        o.ReadOnlyRegistry = self._real

    @staticmethod
    def registry_key(private, **extra):
        return {"alg": "ed25519", "status": "active", "validFrom": "2026-01-01T00:00:00Z",
                "key": base64.b64encode(raw_public(private)).decode(), **extra}

    def publish(self, revision=1, **overrides):
        args = SimpleNamespace(
            network_id=NETWORK, registry_url="http://registry", registry_timeout=1,
            operator="", revision=revision, operating_profile="OAN/v0.1,Beckn/v2",
            status="Active", projection_ttl=None, out=str(self.dir),
            governance_key=str(self.dir / "keys" / "governance.key"))
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
    def test_documents_verify_and_split_the_keys(self):
        descriptor, keyset = self.publish()
        o.verify(descriptor, keyset)
        governance = descriptor["signature"]["keyId"]
        self.assertEqual(descriptor["operationalKeys"], [o.jwk(raw_public(self.traffic))["kid"]])
        self.assertNotIn(governance, descriptor["operationalKeys"])
        self.assertEqual({k["kid"] for k in keyset["keys"]},
                         {governance, *descriptor["operationalKeys"]})
        for field in ("version", "networkId", "name", "operator", "revision", "status",
                      "issuedAt", "operatingProfile", "schemaPacks", "services"):
            self.assertIn(field, descriptor)
        self.assertEqual(descriptor["operatingProfile"], ["OAN/v0.1", "Beckn/v2"])

    def test_governance_key_is_created_private_and_reused(self):
        first, _ = self.publish(1)
        key = self.dir / "keys" / "governance.key"
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        second, _ = self.publish(2)
        self.assertEqual(first["signature"]["keyId"], second["signature"]["keyId"])

    def test_readable_governance_key_is_refused(self):
        self.publish(1)
        os.chmod(self.dir / "keys" / "governance.key", 0o644)
        with self.assertRaisesRegex(o.FederationError, "chmod 600"):
            self.publish(2)

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

    def test_governance_key_that_is_also_a_traffic_key_is_refused(self):
        self.publish(1)
        seed = o.unb64url((self.dir / "keys" / "governance.key").read_text().strip())
        governance = Ed25519PrivateKey.from_private_bytes(seed)
        StubRegistry.entry["keys"].append(self.registry_key(governance))
        with self.assertRaisesRegex(o.FederationError, "governance key must be separate"):
            self.publish(2)

    def test_only_usable_registry_keys_are_delegated(self):
        revoked, expired, upcoming = (Ed25519PrivateKey.generate() for _ in range(3))
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        StubRegistry.entry["keys"] += [
            self.registry_key(revoked, status="revoked"),
            self.registry_key(expired, validUntil="2020-01-01T00:00:00Z"),
            self.registry_key(upcoming, validFrom=future),
            {"alg": "x25519", "status": "active", "key": base64.b64encode(b"e" * 32).decode()},
        ]
        descriptor, _ = self.publish()
        self.assertEqual(descriptor["operationalKeys"],
                         [o.jwk(raw_public(k))["kid"] for k in (self.traffic, upcoming)])

    def test_no_usable_traffic_key_is_refused(self):
        StubRegistry.entry["keys"] = [self.registry_key(self.traffic, status="revoked")]
        with self.assertRaisesRegex(o.FederationError, "no active Ed25519 key"):
            self.publish()

    def test_invalid_descriptor_is_never_written(self):
        StubRegistry.entry["baseUrl"] = "http://insecure/discover"
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
                             ("services", {"discovery": "https://evil.example/d"}),
                             ("operationalKeys", [self.keyset["keys"][1]["kid"], "extra"])):
            tampered = {**self.descriptor, field: value}
            with self.subTest(field=field):
                self.refuses(tampered)

    def test_signer_must_not_also_be_delegated_for_traffic(self):
        governance = self.descriptor["signature"]["keyId"]
        d = {**self.descriptor, "operationalKeys": [governance]}
        self.refuses(d, match="also delegated for traffic")

    def test_delegated_key_missing_from_jwks(self):
        keyset = {"keys": [k for k in self.keyset["keys"]
                           if k["kid"] == self.descriptor["signature"]["keyId"]]}
        self.refuses(keyset=keyset, match="operationalKeys not in the JWKS")

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
                             ("schemaPacks", ["http://x/y"]), ("projectionTtl", -1)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(o.FederationError):
                    o.validate_descriptor({**self.descriptor, field: value})

    def test_admit_stores_only_delegated_keys(self):
        # A key added to the JWKS without the governance signature delegating it
        # must not reach the peer record either.
        injected = copy.deepcopy(self.keyset)
        injected["keys"].append(o.jwk(raw_public(Ed25519PrivateKey.generate())))
        record = o.admit_record(None, self.descriptor, injected)
        self.assertEqual([k["key"] for k in record["keys"]],
                         [base64.b64encode(raw_public(self.traffic)).decode()])


if __name__ == "__main__":
    unittest.main()
