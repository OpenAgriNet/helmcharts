#!/usr/bin/env python3
"""Generate the secret material the OAN charts consume, once, and remember it.

Every chart in charts/ takes its secrets by reference -- a Secret name and a key
inside it -- and none of them render a credential themselves. That leaves the
question of where the values come from, which today is a set of
`kubectl create secret` commands pasted out of the examples/ files. This script
is that step, done once and reproducibly, in a form AWS Secrets Manager can
ingest.

ONE SECRET PER VALUE

A value that several namespaces need is still one Secret. It is created in the
namespace that owns it, pulled there from Secrets Manager by External Secrets
Operator, and mirrored into the others by Kubernetes Reflector. Nothing here
emits a second copy, and nothing should create one by hand: two copies are two
values that can drift, and a drifted database password fails as "password
authentication failed", which reads as a Postgres problem rather than a Secret
problem.

So the output is values only -- one top-level YAML key per secret, one Secrets
Manager entry each. The namespaces appear as comments, because they are the
reflection target list rather than anything ASM should store.

WHAT IT MODELS

Not "a password per chart value". A password belongs to a ROLE, and with the
CNPG Clusters in their own namespace, two different readers need the same one:

    registry-db/password    postgres ns: the operator sets the role from it
                            registry ns: the registry connects with it
    keycloak-db/password    postgres ns: operator side
                            keycloak ns: Keycloak connects with it

Generating those independently produces a stack that fails authentication in a
way that reads as a database problem. So the unit here is the generated VALUE:
each is produced once and projected into every namespace that needs it.

WHAT IT CANNOT GENERATE

One value is assigned by another system and is emitted as a sentinel, never
invented:

  keyId                      the registry's osid for a published key. Known only
                             after the public half is seeded -- see
                             quick-start/bin/setup.py key_osids().

Inventing it would produce a value that does not match what the registry holds,
so the script refuses to and tells you which are outstanding.

Every run generates fresh values: there is no local record, deliberately, since
a file of credentials beside a secrets manager is a second source of truth that
can only drift from it. Reuse and rotation belong to whatever backs these once
that is decided; until then, generate once and store the result.

USAGE

    ./scripts/gen-secrets.py --env dev                     # JSON bundle
    ./scripts/gen-secrets.py --env dev --format kubectl    # create-secret commands
    ./scripts/gen-secrets.py --env dev --out secrets.json  # written 0600

Needs python3 and the cryptography package -- the same dependency
quick-start/bin/setup.py already requires:

    pip install cryptography
"""

import argparse
import base64
import json
import os
import secrets
import stat
import string
import sys
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

# Assigned elsewhere. Carried through the output so the shape is complete and
# the gap is loud, rather than absent and discovered at install time.
PENDING = "<<PENDING: {}>>"

# Letters, digits, and @ ! only.
#
# The two specials are deliberate rather than arbitrary: of the punctuation that
# survives a DSN, a JDBC URL, YAML and a shell single-quoted string without
# needing a different escape in each, these are the two that read cleanly. Note
# that @ still has to be percent-encoded when it goes INTO a DSN -- see dsn().
#
# LENGTH IS A TRADE. Seven characters over this 64-character alphabet is about
# 42 bits (64**7, roughly 4.4e12). That is fine against online guessing, which
# Postgres and Keycloak both rate-limit, and weak against an offline attack on a
# stolen hash. It is short because a human types some of these; the ones nothing
# types could be longer at no cost.
SPECIALS = "@!"
ALPHABET = string.ascii_letters + string.digits + SPECIALS


def password(length=7):
    """At least one letter, one digit and one of @ !.

    Rejection sampling rather than composing the classes and shuffling: it stays
    uniform over the passwords that satisfy the rule, and at this length the
    retry rate is low. Without it, a random 7 from this alphabet contains no
    special about 80% of the time, so "has a special character" would be a
    property of roughly one password in five rather than of all of them.
    """
    while True:
        pw = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if (any(c.isalpha() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in SPECIALS for c in pw)):
            return pw


def b64(raw):
    return base64.b64encode(raw).decode()


def ed25519_pair():
    """Signing keypair, raw 32-byte halves, base64.

    Must stay byte-identical in format to quick-start/bin/setup.py: the adapter
    reads these with the same decoder, and a PEM or DER encoding here would be
    accepted as bytes and produce signatures nothing can verify.
    """
    k = Ed25519PrivateKey.generate()
    return {"private": b64(k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw,
                                           ser.NoEncryption())),
            "public": b64(k.public_key().public_bytes(ser.Encoding.Raw,
                                                      ser.PublicFormat.Raw))}


def x25519_pair():
    """Encryption keypair. Same format rule as ed25519_pair."""
    k = X25519PrivateKey.generate()
    return {"private": b64(k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw,
                                           ser.NoEncryption())),
            "public": b64(k.public_key().public_bytes(ser.Encoding.Raw,
                                                      ser.PublicFormat.Raw))}


def dsn(user, pw, host, dbname, port=5432):
    """Percent-encode the password: @ is the userinfo delimiter.

    postgresql://user:pa@ss@host/db is ambiguous, and a parser that takes the
    first @ resolves it to the host "ss@host". The password stored in the Secret
    stays raw -- that is what the service sends on the wire -- while the copy
    inside this URL is encoded, which is what makes the two agree.
    """
    return (f"postgresql://{quote(user, safe='')}:{quote(pw, safe='')}"
            f"@{host}:{port}/{dbname}")


def build(ns, domain, roles):
    """The values, and the namespaces each has to reach.

    One entry per value. A value needed in more than one namespace is still one
    Secret: it is created in the first namespace listed and mirrored into the
    rest by Kubernetes Reflector, so there is no second copy to drift from the
    first and nothing here emits one.

    `namespaces` is therefore not a list of copies to make -- it is the
    reflection target list, and what the Reflector annotations are built from.
    """
    svc = "svc.cluster.local"
    discovery_db_host = f"discovery-db-rw.{ns['postgres']}.{svc}"

    out = {}

    def secret(name, keys, namespaces, note):
        out[name] = {"keys": keys, "namespaces": namespaces, "note": note}

    secret("registry-db",
           {"username": "registry", "password": password()},
           [ns["postgres"], ns["registry"]],
           "CNPG sets the owner role from it; the registry connects with it")

    secret("keycloak-db",
           {"username": "keycloak", "password": password()},
           [ns["postgres"], ns["keycloak"]],
           "CNPG reconciles the keycloak role from it; Keycloak connects with "
           "it. The registry never reads this - it connects as `registry`")

    secret("keycloak-admin",
           {"keycloakAdminPassword": password()},
           [ns["keycloak"]],
           "Keycloak console login")

    # Two secrets, not one with two keys. Reflector mirrors a whole Secret and
    # cannot select keys, so bundling these would carry the registry's user
    # password into the keycloak namespace, which has no use for it -- and
    # keeping that blast radius small is the reason the namespaces are split.
    secret("keycloak-admin-api",
           {"keycloakAdminClientSecret": password()},
           [ns["registry"], ns["keycloak"]],
           "admin-api client credential: Keycloak stores it on the client at "
           "realm import, the registry presents it. The only value both read")

    # The identity registry-seed writes participants as. The realm ships the
    # `no-user` account carrying the `network_operator` role the registry
    # checks, and substitutes this into it at import -- so Keycloak needs it,
    # and so does the seeding Job wherever it runs.
    secret("registry-seed",
           {"registrySeedPassword": password()},
           [ns["keycloak"], ns["registry"]],
           "keycloak: realmImport.seedUserSecret (substituted into the no-user "
           "account); registry-seed: seedUser.passwordSecret")

    secret("registry-default-user",
           {"registryDefaultUserPassword": password()},
           [ns["registry"]],
           "password the registry sets on Keycloak users it creates. Registry "
           "only - never mirrored")

    discovery_pw = password()
    # The uri is built here rather than taken from CNPG's generated app Secret:
    # CNPG writes the bare in-namespace host, which does not resolve from the
    # discovery namespace. Supplying the owner Secret means the DSN carries the
    # FQDN and database.urlSecret keeps working.
    secret("discovery-db",
           {"username": "discovery", "password": discovery_pw,
            "uri": dsn("discovery", discovery_pw, discovery_db_host, "discovery")},
           [ns["postgres"], ns["discovery"]],
           "CNPG sets the owner role from it; discovery reads `uri` as its DSN")

    secret("registry-db-superuser",
           {"username": "postgres", "password": password()},
           [ns["postgres"]],
           "only with enableSuperuserAccess; postgresql-migration writes across "
           "databases")

    for role in roles:
        sign, encr = ed25519_pair(), x25519_pair()
        secret(f"{role}-adapter-keys",
               {"subscriberId": f"{role}.oan.{domain}",
                "keyId": PENDING.format(
                    "registry osid, read back after the public half is seeded"),
                "signingPrivateKey": sign["private"],
                "signingPublicKey": sign["public"],
                "encrPrivateKey": encr["private"],
                "encrPublicKey": encr["public"]},
               [ns["adapter"]],
               f"adapter-service ({role}): keys.existingSecret.name")

    return out


def yaml_scalar(v):
    """Single-quoted, always.

    Everything here is a string and several are hostile to bare YAML: base64
    carries + / and =, the DSN carries : / and @, and the pending sentinel opens
    with << . Single quotes cover all of it, with '' as the only escape.
    """
    return "'" + str(v).replace("'", "''") + "'"


def as_yaml(bundle):
    """Values only -- the thing that goes into Secrets Manager.

    One top-level key per secret, so each becomes one entry there and gets
    pulled into the cluster once. The namespaces are a comment rather than
    data: they say where Reflector has to mirror the Secret to, and are not
    something ASM should be asked to store.
    """
    out = ["# Generated by scripts/gen-secrets.py. Values only: one top-level",
           "# key per secret, each becoming one entry in AWS Secrets Manager.",
           "#",
           "# `namespaces:` in the comments is the reflection target list, not",
           "# data to upload. The Secret is created once in the first namespace",
           "# and mirrored into the rest by Kubernetes Reflector, so no value",
           "# here is ever written to two places.",
           ""]
    for name, spec in bundle.items():
        out.append(f"# {spec['note']}")
        out.append(f"# namespaces: {', '.join(spec['namespaces'])}"
                   + ("  (first is the source, rest are mirrors)"
                      if len(spec["namespaces"]) > 1 else ""))
        out.append(f"{name}:")
        for k, v in spec["keys"].items():
            out.append(f"  {k}: {yaml_scalar(v)}")
        out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(
        description="Generate the secret material the OAN charts consume.")
    p.add_argument("--env", default="dev", help="environment name")
    p.add_argument("--namespace", default="registry",
                   help="namespace the registry runs in (default: registry)")
    p.add_argument("--postgres-namespace", default="postgres",
                   help="namespace the CNPG Clusters run in (default: postgres)")
    p.add_argument("--keycloak-namespace", default="keycloak",
                   help="namespace Keycloak runs in (default: keycloak)")
    p.add_argument("--discovery-namespace", default="discovery",
                   help="namespace the discovery service runs in "
                        "(default: discovery)")
    p.add_argument("--adapter-namespace", default="oan",
                   help="namespace the adapters run in (default: oan)")
    p.add_argument("--domain", default=None,
                   help="subscriber id suffix, e.g. 'dev' -> consumer.oan.dev "
                        "(default: the --env value)")
    p.add_argument("--roles", default="consumer,network,provider",
                   help="adapter identities to generate keypairs for")
    p.add_argument("--format", choices=("yaml", "json"), default="yaml",
                   help="yaml: values only, for upload to Secrets Manager "
                        "(default). json: the same plus namespaces and notes.")
    p.add_argument("--out", default=None,
                   help="write here (mode 0600) instead of stdout")
    args = p.parse_args()

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    bundle = build({"registry": args.namespace,
                    "postgres": args.postgres_namespace,
                    "keycloak": args.keycloak_namespace,
                    "discovery": args.discovery_namespace,
                    "adapter": args.adapter_namespace},
                   args.domain or args.env, roles)

    body = (as_yaml(bundle) + "\n") if args.format == "yaml" \
        else (json.dumps(bundle, indent=2) + "\n")

    if args.out:
        # 0600 set at open time, so the file never exists readable with values
        # already in it.
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        print(f"wrote {args.out} (0600)", file=sys.stderr)
    else:
        sys.stdout.write(body)

    mirrored = [n for n, sp in bundle.items() if len(sp["namespaces"]) > 1]
    print(f"\n{len(bundle)} secrets, {len(mirrored)} of them needed in more "
          f"than one namespace:\n  {', '.join(mirrored)}\nThose are created "
          f"once and mirrored by Reflector - do not create them twice.\n"
          f"\nNothing is recorded locally: store this output before you lose "
          f"it, because re-running generates different values.",
          file=sys.stderr)

    pending = [(n, k) for n, s in bundle.items()
               for k, v in s["keys"].items() if str(v).startswith("<<PENDING")]
    if pending:
        print("\nOutstanding -- assigned by another system, not by this script:",
              file=sys.stderr)
        for name, key in pending:
            print(f"  {name}/{key}", file=sys.stderr)


if __name__ == "__main__":
    main()
