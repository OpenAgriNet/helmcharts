# Certificates

Two different mechanisms, and which one you need depends on whether the name
you are certifying is reachable from the public internet.

|                | local (this laptop)      | the VM                        |
|----------------|--------------------------|-------------------------------|
| issuer         | mkcert, a private CA     | Let's Encrypt, a public CA    |
| trusted by     | only machines you set up | everything                    |
| validation     | none -- you own the CA   | HTTP-01, inbound from LE      |
| works offline  | yes                      | no                            |

The dividing line is not preference. A public CA will only certify a name it
can verify from the outside, and a loopback address is definitionally outside
that. `127.0.0.1.sslip.io` resolves correctly -- to `127.0.0.1` -- which is
precisely why Let's Encrypt cannot validate it: their servers resolve that name
and connect to their own loopback. They also refuse by policy to issue for
names in reserved IP space, so this fails as a rejection and not a timeout.

## Local, with mkcert

    brew install mkcert

### 1. Create and trust the CA

    mkcert -install

Run this in a REAL terminal window. It needs an admin password, and a sudo
prompt with no TTY fails silently -- which leaves the CA generated but not
trusted, and every certificate signed by it then fails in every browser while
looking perfectly well-formed in `openssl`.

Verify before going further. This is the whole gate:

    security verify-cert -c "$(mkcert -CAROOT)/rootCA.pem"

`Cert Verify Result: Success` and nothing else. `CSSMERR_TP_NOT_TRUSTED` means
the CA is present but carries no trust setting, which is the same as absent.

### 2. Generate the leaf

    mkdir -p ~/oan-local-certs && cd ~/oan-local-certs
    mkcert -cert-file oan-local.pem -key-file oan-local-key.pem \
      "127.0.0.1.sslip.io" "*.127.0.0.1.sslip.io" \
      "oan.test" "*.oan.test" localhost 127.0.0.1 ::1

Name everything up front; adding one later means regenerating and re-uploading.

The sslip.io forms are worth having over the `.test` ones: sslip.io resolves
`<anything>.127.0.0.1.sslip.io` to `127.0.0.1` on its own, so those names need
no /etc/hosts entry. X.509 wildcards match one level only -- `exp.127.0.0.1.sslip.io`
is covered, `a.b.127.0.0.1.sslip.io` is not.

### 3. Upload, attach, restart

NPM -> SSL Certificates -> Add Certificate -> **Custom**. Key first, then cert.

Then per proxy host: Edit -> SSL -> select it -> Force SSL -> Save. This is not
global; a host you forget serves plain HTTP and reports no error.

Then QUIT the browser -- fully, not just the window. Chromium and Safari read
root certificates at process start, so a reload after `mkcert -install` shows
the old answer.

## On the VM, with Let's Encrypt

1. Elastic IP attached. Not optional with sslip.io: the hostname CONTAINS the
   address, so a stop/start invalidates every proxy host and every certificate
   at once, rather than needing one A record updated.

2. Security group: 80 and 443 open to `0.0.0.0/0`. Not to your address --
   HTTP-01 validation arrives from Let's Encrypt's own servers, whose addresses
   you do not get to enumerate. Scoping it to yourself fails with a challenge
   timeout that reads like a DNS problem.

3. `make gateway`, then create the proxy host with `<service>.<elastic-ip>.sslip.io`.
   No DNS record to create -- that is the entire point of sslip.io here.

4. Confirm it answers over plain HTTP first. Let's Encrypt allows 5 failed
   validations per hostname per hour, and debugging a misconfigured route
   through the certificate flow is how you spend them.

5. SSL tab -> Request a new SSL Certificate -> HTTP Validation -> Force SSL.

DNS-01 is unavailable with sslip.io -- you do not control that zone, so certbot
cannot write the TXT record. It is the better option on a domain you do own: it
needs no inbound reachability at all, and it is the only way to get a wildcard.

Renewal uses whatever method issued the certificate, so an SG rule that was
only temporarily correct fails in sixty days with nothing to announce it.

## Reading the failure

| symptom | cause |
|---|---|
| `CSSMERR_TP_NOT_TRUSTED` | CA not in the trust store -- step 1 |
| Safari: `"<name>" certificate is not trusted` | same, named after the cert's first SAN rather than the site |
| Brave: `Not Secure`, https struck through | same, with less detail. Safari's message is the useful one |
| `curl` verify=0 but browser red | `--cacert` bypasses the trust store; it proves the chain, not the trust |
| `tlsv1 unrecognized name`, SNI alert 112 | no 443 server block for that name -- the host has no certificate attached |
| cert fine, browser still red | browser not restarted since `mkcert -install` |
