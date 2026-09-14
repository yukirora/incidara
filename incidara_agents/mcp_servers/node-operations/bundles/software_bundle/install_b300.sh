#!/usr/bin/env bash

set -euo pipefail

# Enable persistent mode to make installation faster
sudo systemctl enable --now nvidia-persistenced || true

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

  sudo dpkg -i ./libnvidia-container1_*.deb \
    ./libnvidia-container1-dbg_*.deb \
    ./libnvidia-container-tools_*.deb \
    ./libnvidia-container-dev_*.deb \
    ./nvidia-container-toolkit-base_*.deb \
    ./nvidia-container-toolkit_*.deb \
    ./nvidia-container-toolkit-operator-extensions_*.deb

  # Configure nvidia runtime for both containerd and docker
  sudo nvidia-ctk runtime configure --runtime=containerd || true
  sudo nvidia-ctk runtime configure --runtime=docker || true
  sudo systemctl restart containerd || true
  sudo systemctl restart docker || true

  cd ..
else
  # Online install from apt (mirror already configured by config_apt.sh)
  if ! command -v docker &>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y containerd.io docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin || true
    sudo systemctl restart containerd || true
    sudo systemctl restart docker || true
  fi

  # NVIDIA container toolkit (if not already installed)
  if ! command -v nvidia-ctk &>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit || true
  fi

  # Configure nvidia runtime for both containerd and docker
  sudo nvidia-ctk runtime configure --runtime=containerd || true
  sudo nvidia-ctk runtime configure --runtime=docker || true
  sudo systemctl restart containerd || true
  sudo systemctl restart docker || true
fi
