#!/usr/bin/env python3
"""Sign this network's federation documents and put them where Kong can serve
them.

Three steps, because the key and the documents live in different places:

  1. assemble keys.json from the mounted Secret -- the SAME Secret the adapter
     reads, so the descriptor is signed with the key that signs traffic. A
     descriptor naming any other key is refused by oanfed itself.
  2. run oanfed publish, which reads our registry entry and signs.
  3. write the three documents into a ConfigMap, which the serving pod mounts.

The private key exists only here, for the seconds this runs. What reaches the
ConfigMap is public: the descriptor, the PUBLIC half of the key, and the list
of schema packs.

Stdlib plus cryptography (oanfed's only dependency). The Kubernetes API is
reached with the pod's own ServiceAccount token rather than kubectl, so the
image needs nothing but python.
"""
import json, os, pathlib, ssl, subprocess, sys, urllib.request

KEYS_DIR = pathlib.Path(os.environ["KEYS_DIR"])
OUT = pathlib.Path("/tmp/federation")
NAMESPACE = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read_text().strip()
TOKEN = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
API = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT']}"


def keys_json():
    """The Secret's fields, in the shape oanfed expects.

    Written to a file rather than passed as arguments: oanfed refuses a key
    file others can read, and a command line is visible in `ps` and in the
    pod's own events."""
    identity = {
        "participantId": os.environ["NETWORK_ID"],
        "signingPrivate": (KEYS_DIR / os.environ["SIGNING_PRIVATE_KEY"]).read_text().strip(),
        "signingPublic": (KEYS_DIR / os.environ["SIGNING_PUBLIC_KEY"]).read_text().strip(),
    }
    path = OUT / "keys.json"
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(identity))
    path.chmod(0o600)
    return path


def publish(keys):
    revision = next_revision()
    print(f"publishing revision {revision}", flush=True)
    subprocess.run(
        [sys.executable, "/opt/federation/oanfed.py", "publish",
         "--network-id", os.environ["NETWORK_ID"],
         "--registry-url", os.environ["REGISTRY_URL"],
         "--keys", str(keys),
         "--out", str(OUT),
         "--revision", str(revision)]
        + (["--operator", os.environ["OPERATOR"]] if os.environ.get("OPERATOR") else [])
        + (["--projection-ttl", os.environ["PROJECTION_TTL"]] if os.environ.get("PROJECTION_TTL") else []),
        check=True)


def next_revision():
    """One past what the ConfigMap already holds.

    oanfed refuses a revision that does not increase on the one published, and
    every network holding the old one would refuse it too -- so the number has
    to come from what is being served, not from a value in git that a re-run
    would repeat."""
    held = api("GET", f"/api/v1/namespaces/{NAMESPACE}/configmaps/{os.environ['DOCUMENTS_CONFIGMAP']}")
    if held is None:
        return 1
    served = (held.get("data") or {}).get("openagrinet")
    if not served:
        return 1
    return int(json.loads(served).get("revision", 0)) + 1


def store():
    data = {p.name: p.read_text() for p in sorted(OUT.glob("**/*"))
            if p.is_file() and p.name != "keys.json"}
    body = {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": os.environ["DOCUMENTS_CONFIGMAP"], "namespace": NAMESPACE},
            "data": data}
    path = f"/api/v1/namespaces/{NAMESPACE}/configmaps"
    if api("GET", f"{path}/{os.environ['DOCUMENTS_CONFIGMAP']}") is None:
        api("POST", path, body, expect=(200, 201))
    else:
        api("PUT", f"{path}/{os.environ['DOCUMENTS_CONFIGMAP']}", body, expect=(200,))
    print(f"stored {', '.join(sorted(data))} in {os.environ['DOCUMENTS_CONFIGMAP']}", flush=True)


def api(method, path, body=None, expect=(200,)):
    request = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30,
                                    context=ssl.create_default_context(cafile=CA)) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        if err.code == 404 and method == "GET":
            return None
        sys.exit(f"publish: kubernetes {method} {path} returned {err.code}: "
                 f"{err.read().decode()[:300]}")


if __name__ == "__main__":
    publish(keys_json())
    store()
