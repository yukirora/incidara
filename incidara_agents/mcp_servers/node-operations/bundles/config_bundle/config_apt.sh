#!/usr/bin/env bash

set -euo pipefail

APT_MIRROR="${APT_MIRROR:-192.0.2.10}"

if [ -f /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "22.04" ]; then
    sudo rm -rf /etc/apt/sources.list*
    sudo mkdir -p /etc/apt/sources.list.d
    sudo rm -rf /etc/apt/apt.conf.d/90curtin-aptproxy
    sudo tee /etc/apt/sources.list > /dev/null << EOF
deb http://${APT_MIRROR}/ubuntu/ jammy main restricted universe multiverse
deb http://${APT_MIRROR}/ubuntu/ jammy-updates main restricted universe multiverse
deb http://${APT_MIRROR}/ubuntu/ jammy-backports main restricted universe multiverse
deb http://${APT_MIRROR}/ubuntu/ jammy-security main restricted universe multiverse
EOF
  fi
fi

# Fix broken dependency or dpkg status
sudo dpkg --configure -a || true
sudo apt --fix-broken install -y
sudo dpkg --configure -a
sudo apt update

# Disable apt auto upgrade
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer
sudo systemctl mask apt-daily.service apt-daily-upgrade.service
sudo systemctl mask unattended-upgrades.service
sudo sed -i 's/^APT::Periodic::Unattended-Upgrade "1";/APT::Periodic::Unattended-Upgrade "0";/' /etc/apt/apt.conf.d/20auto-upgrades
sudo sed -i 's/^APT::Periodic::Update-Package-Lists "1";/APT::Periodic::Update-Package-Lists "0";/' /etc/apt/apt.conf.d/20auto-upgrades
