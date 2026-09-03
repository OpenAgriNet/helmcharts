#!/usr/bin/env bash
#
# Everything an Ubuntu VM needs before `make up` will run. Idempotent -- safe to
# re-run, and safe to run on a box that already has some of this.
#
#     curl -fsSL https://raw.githubusercontent.com/OpenAgriNet/helmcharts/<branch>/docker-deployment/bin/bootstrap-ubuntu.sh | bash
#
# or, once the repo is cloned:
#
#     bin/bootstrap-ubuntu.sh
#
# It does NOT clone the repo, write .env, or start anything. Those need
# decisions -- which branch, which credentials -- that do not belong in a
# script piped from the internet.

set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
info() { printf '    %s\n' "$1"; }
die()  { printf '\033[1;31mbootstrap: %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as a normal user, not root -- this uses sudo where it needs to"
command -v apt-get >/dev/null || die "not a Debian/Ubuntu system"

# ---------------------------------------------------------------- sizing

# Checked rather than assumed, because the failure mode of an undersized box is
# the OOM killer taking out a Postgres mid-write, which surfaces as data
# corruption rather than as "out of memory".
say "checking this VM against what the stack needs"
mem_gb=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024 / 1024 ))
disk_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
info "RAM  ${mem_gb} GB   (8 GB minimum, 16 GB if you run the observability profile)"
info "disk ${disk_gb} GB free   (20 GB minimum -- the images alone are ~6 GB)"
[ "$mem_gb" -ge 7 ]   || info "WARNING: under 8 GB. Two Postgres, two JVMs and three adapters will not fit."
[ "$disk_gb" -ge 20 ] || info "WARNING: under 20 GB free. A full disk corrupts the Docker VM rather than erroring cleanly."

# ------------------------------------------------------------------ apt

say "apt packages"
sudo apt-get update -qq
# python3-cryptography from apt rather than pip: Ubuntu 24.04 marks the system
# python as externally-managed (PEP 668), so `pip install cryptography` refuses
# without --break-system-packages. The apt build is the same library.
sudo apt-get install -y -qq \
    ca-certificates curl gnupg git make python3 python3-cryptography
info "git, make, python3, python3-cryptography"

# --------------------------------------------------------------- docker

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    say "docker already present"
    info "$(docker --version)"
    info "$(docker compose version)"
else
    # Docker's own apt repo, not the `docker.io` package and not snap. The
    # distro package lags, and the snap runs confined -- bind mounts out of a
    # home directory, which this stack does for every adapter config, fail
    # under it in ways that read as file-not-found.
    say "installing docker engine from docker's apt repository"
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    info "$(docker --version)"
fi

say "docker group"
if id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    info "$USER is already in the docker group"
else
    sudo usermod -aG docker "$USER"
    info "$USER added to the docker group"
    info "LOG OUT AND BACK IN before docker works without sudo -- group"
    info "membership is read at login, so this shell still cannot use it."
fi

say "enabling docker at boot"
sudo systemctl enable --now docker >/dev/null 2>&1 || true
info "$(systemctl is-active docker 2>/dev/null || echo unknown)"

# ------------------------------------------------------------------ ufw

# Only touched if it is already running. Turning a firewall on for someone is
# a good way to end a session, and on EC2 the security group is the control
# that matters anyway.
if command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "^Status: active"; then
    say "ufw is active -- opening what the edge needs"
    sudo ufw allow 80/tcp  >/dev/null
    sudo ufw allow 443/tcp >/dev/null
    info "80 and 443 allowed. Everything else in this stack binds 127.0.0.1"
    info "and is reached over the SSH tunnel, so nothing further is needed."
else
    say "ufw not active -- leaving it alone"
    info "On EC2 the security group is the real control. 80 and 443 must be"
    info "open to 0.0.0.0/0, not to your address: Let's Encrypt validates"
    info "HTTP-01 from its own servers."
fi

say "done"
cat <<'NEXT'

  If this added you to the docker group, log out and back in now.

  Then:

      git clone -b feat/4-docker-compose https://github.com/OpenAgriNet/helmcharts.git
      cd helmcharts/docker-deployment
      cp .env.example .env && nano .env      # change every credential
      make up

  `make up` includes the gateway and hyperdx. Use `make up-core` for neither.
  See CERTIFICATES.md before requesting a certificate.

NEXT
