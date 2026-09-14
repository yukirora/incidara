#!/usr/bin/env bash

set -euo pipefail

if [ -d deb ] && ls deb/*.deb >/dev/null 2>&1; then
  # Install from local deb packages (offline install)
  cd deb
  sudo dpkg -i ./ipmitool_*.deb \
    libfreeipmi17_*.deb \
    freeipmi-common_*.deb \
    nvme-cli_*.deb
  cd ..
else
  # Install from apt (online install)
  sudo apt-get update
  sudo apt-get install -y ipmitool freeipmi-common nvme-cli
fi
