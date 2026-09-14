#!/usr/bin/env bash

set -euo pipefail

if [ -d deb ] && ls deb/containerd.io_*.deb >/dev/null 2>&1; then
  # Offline install from local deb packages
  cd deb

  sudo dpkg -i ./containerd.io_*.deb \
    ./docker-ce_*.deb \
    ./docker-ce-cli_*.deb \
    ./docker-buildx-plugin_*.deb \
    ./docker-compose-plugin_*.deb

  sudo systemctl restart docker
  sudo systemctl status docker

  cd ..
else
  # Online install from apt
  if ! command -v docker &>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y containerd.io docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin || true
    sudo systemctl restart containerd || true
    sudo systemctl restart docker || true
  fi
fi
