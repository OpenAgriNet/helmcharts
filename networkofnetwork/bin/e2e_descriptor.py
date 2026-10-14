#!/usr/bin/env python3
"""End-to-end check of a network's hosted Network Descriptor and JWKS.

    make e2e-descriptor NETWORK_ID=bharatvistar.oan.local PEER=mahavistara.oan.local

Needs both networks up (`make network`). It publishes a new revision with the
current oanfed.py, fetches it over TLS through the network's front door exactly
as a peer would, then tampers with the served files and checks each forgery is
refused.

It leaves the network as it found it: the served documents and the key file's
mode are restored on exit, pass or fail. Nothing is written to either registry --
admission is computed, not recorded.
"""

import base64, copy, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE.parent / "networks"
sys.path.insert(0, str(HERE))
import oanfed as o
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

results = []


def check(name, ok, detail=""):
    results.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail else ""))


def ports(network):
    out = subprocess.run([sys.executable, str(HERE / "network-ports.py"), "ports", network],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split())


def run(*args):
    p = subprocess.run([sys.executable, str(HERE / "oanfed.py"), *args],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def search(registry_port, network):
    request = urllib.request.Request(
        f"http://127.0.0.1:{registry_port}/api/v1/Participant/search",
        data=json.dumps({"filters": {"participantId": {"eq": network}}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        found = json.load(response)
    rows = found if isinstance(found, list) else found.get("data", [])
    return rows[0] if rows else None


def main(network, peer):
    net_dir = STATE / network
    well_known, key_file = net_dir / ".well-known", net_dir / "keys" / "network.key"
    ca = str(STATE / "ca" / "ca.crt")
    ours, theirs = ports(network), ports(peer)
    door, registry = ours["FRONT_DOOR_PORT"], ours["REGISTRY_PORT"]

    files = ("openagrinet", "jwks.json")
    original = {f: (well_known / f).read_bytes() if (well_known / f).exists() else None
                for f in files}
    key_mode = key_file.stat().st_mode & 0o777

    def publish(revision, *extra, key=key_file, network_id=network, registry_url=None, out=net_dir):
        return run("publish", "--network-id", network_id,
                   "--registry-url", registry_url or f"http://127.0.0.1:{registry}",
                   "--key", str(key), "--revision", str(revision),
                   "--out", str(out), *extra)

    def verify(target=network, *extra):
        return run("verify", "--network-id", target, "--resolve", f"127.0.0.1:{door}", *extra)

    def verify_trusted(target=network):
        return verify(target, "--ca-file", ca)

    def curl(path):
        p = subprocess.run(["curl", "-s", "-D", "-", "-o", "/dev/null", "--cacert", ca,
                            "--resolve", f"{network}:{door}:127.0.0.1",
                            f"https://{network}:{door}{path}"], capture_output=True, text=True)
        return p.stdout.lower()

    served = lambda f: json.loads((well_known / f).read_text())

    def body(f):
        """What the front door serves for a file right now; None for a 404."""
        p = subprocess.run(["curl", "-s", "-w", "\n%{http_code}", "--cacert", ca,
                            "--resolve", f"{network}:{door}:127.0.0.1",
                            f"https://{network}:{door}/.well-known/{f}"], capture_output=True)
        content, _, status = p.stdout.rpartition(b"\n")
        return None if status == b"404" else content

    def settle():
        """Wait until the front door serves exactly what is on disk.

        Docker Desktop's file sharing can lag a write by a few milliseconds and
        serve the new bytes at the old length meanwhile. Checking before it
        settles would test the lag, not the documents.
        """
        deadline = time.time() + 5
        while time.time() < deadline:
            if all(body(f) == ((well_known / f).read_bytes() if (well_known / f).exists() else None)
                   for f in files):
                return True
            time.sleep(0.05)
        return False

    try:
        # ---- happy path ----------------------------------------------------
        held = served("openagrinet")["revision"] if original["openagrinet"] else 0
        code, out = publish(held + 1)
        check("publish the next revision from the registry", code == 0, out.splitlines()[0])
        if code != 0:
            return
        check("the network key is mode 600", key_mode == 0o600)
        new = {f: (well_known / f).read_bytes() for f in files}
        check("the front door serves the new documents", settle())

        def restore_new():
            for f, b in new.items():
                (well_known / f).write_bytes(b)
            settle()

        d, k = served("openagrinet"), served("jwks.json")

        for path in ("/.well-known/openagrinet", "/.well-known/jwks.json"):
            h = curl(path)
            first = h.splitlines()[0] if h else "no response"
            check(f"GET {path} over TLS: 200, application/json, no-cache",
                  " 200" in first and "content-type: application/json" in h and "no-cache" in h,
                  first)
        h = curl("/.well-known/nothing")
        check("an unknown well-known path is 404", " 404" in (h.splitlines() or [""])[0])

        code, out = verify_trusted()
        check("a peer verifies it over TLS", code == 0 and f"revision {held + 1}" in out,
              out.splitlines()[0])

        entry = search(registry, network)
        traffic = base64.b64decode(entry["keys"][0]["key"].removeprefix("base64:"))
        identity = json.loads((net_dir / "keys/keys.json").read_text())["network"]
        signer = d["signature"]["keyId"]
        check("the one published key is the registry key and the adapter's signing key",
              signer == o.jwk(traffic)["kid"] and [x["kid"] for x in k["keys"]] == [signer]
              and base64.b64decode(identity["signingPublic"]) == traffic)
        check("Ed25519, version 1.0, every required field",
              d["signature"]["algorithm"] == "Ed25519" and d["version"] == "1.0"
              and all(f in d for f in ("networkId", "name", "operator", "revision", "status",
                                       "issuedAt", "operatingProfile", "schemaPacks", "services")))

        current = search(theirs["REGISTRY_PORT"], network)
        fetched_d, fetched_k = o.Fetcher(ca_file=ca, resolve=f"127.0.0.1:{door}").fetch(network)
        record = o.admit_record(current, fetched_d, fetched_k)
        check(f"{peer} would store the published key at the new revision (not written)",
              [x["key"] for x in record["keys"]] == [base64.b64encode(traffic).decode()]
              and record["revision"] == held + 1,
              f"{peer} holds revision {current.get('revision') if current else None}")
        try:
            o.admit_record({**(current or {"participantId": network}), "revision": held + 1},
                           fetched_d, fetched_k)
            check("re-reading the same revision changes nothing", False)
        except o.StaleRevision:
            check("re-reading the same revision changes nothing", True)

        # ---- publish refuses -----------------------------------------------
        unchanged = lambda: (well_known / "openagrinet").read_bytes() == new["openagrinet"]
        for revision in (held + 1, held, 0):
            code, out = publish(revision)
            check(f"publish refuses revision {revision} (not increasing)",
                  code != 0 and unchanged(), out)
        code, out = publish(held + 2, "--status", "Paused")
        check("publish refuses an unknown status", code != 0, out.splitlines()[-1])
        code, out = publish(held + 2, "--projection-ttl", "-5")
        check("publish refuses a negative projectionTtl, writes nothing",
              code != 0 and unchanged(), out)
        os.chmod(key_file, 0o644)
        code, out = publish(held + 2)
        os.chmod(key_file, key_mode)
        check("publish refuses a key file others can read", code != 0, out)
        stranger = pathlib.Path(tempfile.mkdtemp()) / "stranger.key"
        code, out = publish(held + 2, key=stranger)
        check("publish refuses a key the registry does not hold", code != 0, out)
        code, out = publish(held + 2, network_id="nobody.example.org",
                            out=pathlib.Path(tempfile.mkdtemp()))
        check("publish refuses a network not in the registry", code != 0, out)
        code, out = publish(held + 2, "--registry-timeout", "2",
                            registry_url="http://127.0.0.1:1")
        check("publish fails cleanly when the registry is down",
              code != 0 and "Traceback" not in out, out)

        # ---- a peer refuses tampered documents -----------------------------
        def tampered(name, expect, mutate_d=None, mutate_k=None, raw=None):
            dd, kk = copy.deepcopy(d), copy.deepcopy(k)
            if mutate_d:
                mutate_d(dd)
            if mutate_k:
                mutate_k(kk)
            (well_known / "openagrinet").write_text(raw if raw is not None else json.dumps(dd))
            (well_known / "jwks.json").write_text(json.dumps(kk))
            settle()
            code, out = verify_trusted()
            check(f"peer refuses: {name}", code != 0 and "Traceback" not in out and expect in out, out)
            restore_new()

        bad = "signature does not verify"
        tampered("status changed after signing", bad, lambda x: x.update(status="Retired"))
        tampered("discovery URL redirected", bad,
                 lambda x: x["services"].update(discovery="https://evil.example/d"))
        tampered("revision bumped without re-signing", bad,
                 lambda x: x.update(revision=x["revision"] + 100))
        tampered("signature removed", "no signature", lambda x: x.pop("signature"))
        tampered("algorithm set to none", "unsupported",
                 lambda x: x["signature"].update(algorithm="none"))
        tampered("signing key dropped from the JWKS", "no published",
                 mutate_k=lambda x: x.update(keys=[]))
        tampered("duplicate kid in the JWKS", "two keys",
                 mutate_k=lambda x: x["keys"].append(dict(x["keys"][0])))
        tampered("signing key swapped for an attacker's", bad,
                 mutate_k=lambda x: x["keys"].__setitem__(0, {
                     **o.jwk(o.public_of(Ed25519PrivateKey.generate())), "kid": signer}))
        tampered("descriptor names another network", "publishes a descriptor for",
                 lambda x: x.update(networkId=peer))
        tampered("unknown version", "version", lambda x: x.update(version="9.9"))
        tampered("not JSON", "parsing", raw="<html>oops</html>")
        tampered("larger than 1 MiB", "larger than", raw='{"pad":"' + "x" * (1 << 20) + '"}')
        (well_known / "openagrinet").unlink()
        settle()
        code, out = verify_trusted()
        check("peer refuses: descriptor missing (404)", code != 0 and "404" in out, out)
        restore_new()
        code, out = verify_trusted(peer)
        check(f"peer refuses: {peer}'s name at {network}'s door (TLS name mismatch)",
              code != 0 and "Traceback" not in out, out[:160])
        code, out = verify()
        check("peer refuses: certificate from an untrusted CA", code != 0, out[:160])

    finally:
        for f, b in original.items():
            if b is None:
                (well_known / f).unlink(missing_ok=True)
            else:
                (well_known / f).write_bytes(b)
        key_file.chmod(key_mode)

    restored = settle() and all(body(f) == b for f, b in original.items())
    check(f"{network} restored to what it served before", restored)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: e2e_descriptor.py NETWORK_ID PEER")
    main(sys.argv[1], sys.argv[2])
    failed = [name for ok, name in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
