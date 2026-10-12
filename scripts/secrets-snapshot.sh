#!/usr/bin/env bash
#
# Fingerprint every Secret openbao-secrets (or asm-secrets) manages, in every
# namespace it reaches, so two runs can be diffed. Prints namespace/Secret, key
# and a sha256 of each value -- never the value itself.
#
#   ./scripts/secrets-snapshot.sh > before.txt   # still on AWS Secrets Manager
#   ... switch to OpenBao ...
#   ./scripts/secrets-snapshot.sh > after.txt
#   diff before.txt after.txt                     # empty means nothing changed
#
# The Secret list and namespaces come from the chart's values, so a Secret
# missing on one side shows up as a MISSING line rather than silently not
# appearing. Needs kubectl pointed at the cluster, and python3.
set -euo pipefail

cd "$(dirname "$0")/.."

python3 - "${1:-charts/openbao-secrets/values.yaml}" <<'EOF'
import base64, hashlib, json, re, subprocess, sys

# The secrets: list, read without a YAML library -- name, then namespaces: [..].
secrets, name = [], None
for line in open(sys.argv[1]):
    if m := re.match(r'\s*- name:\s*(\S+)', line):
        name = m.group(1)
    elif (m := re.match(r'\s*namespaces:\s*\[(.*)\]', line)) and name:
        secrets.append((name, [n.strip() for n in m.group(1).split(",")]))
        name = None

for name, namespaces in secrets:
    for ns in namespaces:
        r = subprocess.run(["kubectl", "-n", ns, "get", "secret", name, "-o", "json"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{ns}/{name} MISSING")
            continue
        for key, value in sorted((json.loads(r.stdout).get("data") or {}).items()):
            digest = hashlib.sha256(base64.b64decode(value)).hexdigest()[:16]
            print(f"{ns}/{name} {key} {digest}")
EOF
