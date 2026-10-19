#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server (EC2): Docker with Compose, unzip, git.
#   sudo bash deploy/ec2-setup.sh
set -euo pipefail
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 unzip git curl
systemctl enable --now docker
[[ -n ${SUDO_USER:-} ]] && usermod -aG docker "$SUDO_USER" && echo "Log out and in again to use docker without sudo."
docker compose version
