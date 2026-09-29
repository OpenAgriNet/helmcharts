# OpenBao's server config for the local Compose setup. Kept in step with
# standalone.config in charts/openbao/examples/openbao.dev.yaml -- a change to
# one belongs in the other.

ui = true

# Raft wants to know how to reach this node. One node, so both are itself.
api_addr     = "http://127.0.0.1:8200"
cluster_addr = "http://127.0.0.1:8201"

listener "tcp" {
  # Loopback-only on the host (see the ports: line in docker-compose.yml), so
  # plain HTTP is acceptable here and nowhere else.
  tls_disable     = 1
  address         = "[::]:8200"
  cluster_address = "[::]:8201"
}

storage "raft" {
  path    = "/openbao/file"
  node_id = "openbao-local"
}

# Auto-unseal from a key file, written once by the unseal-key service.
seal "static" {
  current_key_id = "oan-local-1"
  current_key    = "file:///openbao/unseal/unseal.key"
}

# Every request and response, with secret values HMAC'd rather than in clear.
# Declared here because OpenBao 2.x refuses `bao audit enable` over the API.
audit "file" "file" {
  description = "Every request, to the audit volume"
  options {
    file_path = "/openbao/logs/audit.log"
  }
}
