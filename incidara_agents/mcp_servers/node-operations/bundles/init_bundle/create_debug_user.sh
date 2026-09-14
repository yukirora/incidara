#!/usr/bin/env bash

set -euo pipefail

NEW_PASS="${1:-}"
BMC_NEW_PASS="${2:-}"
NEW_HOSTNAME="${3:-}"

USER="ubuntu"

# --- 0) Remove debug user if existing ---
if id -u "${USER}"; then
  sudo pkill -9 -u "${USER}" || true
  sudo userdel -r "${USER}"
fi
sudo rm -rf "/home/${USER}"

# --- 1) Create debug user ---
if ! id -u "${USER}" >/dev/null 2>&1; then
  sudo useradd -m -s /bin/bash "${USER}"
  echo "${USER}:${NEW_PASS}" | sudo chpasswd
fi

# --- 2) Add to sudo ---
sudo usermod -aG sudo "${USER}"

# --- 3) Passwordless sudo for USER ---
SUDO_RULE="/etc/sudoers.d/90-${USER}-nopasswd"
echo "${USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee "${SUDO_RULE}"
sudo chmod 0440 "${SUDO_RULE}"
sudo visudo -cf "${SUDO_RULE}"
