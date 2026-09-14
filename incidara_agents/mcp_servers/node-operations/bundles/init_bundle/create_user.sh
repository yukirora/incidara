#!/usr/bin/env bash

set -euo pipefail

for USER in admin operator user; do
  HOME_DIR="/home/${USER}"
  SSH_DIR="${HOME_DIR}/.ssh"
  AUTH_KEYS_DST="${SSH_DIR}/authorized_keys"

  # --- 1) Create user if missing ---
  if ! id -u "${USER}" >/dev/null 2>&1; then
    sudo useradd -m -s /bin/bash "${USER}"
  fi

  # --- 2) Overwrite authorized_keys ---
  sudo install -d -m 0700 -o "${USER}" -g "${USER}" "${SSH_DIR}"
  sudo install -m 0600 -o "${USER}" -g "${USER}" "./authorized_keys" "${AUTH_KEYS_DST}"

  # --- 3) Disable password ---
  sudo passwd -l "${USER}"
done

for USER in admin operator; do
  # --- 1) Add to sudo ---
  sudo usermod -aG sudo "${USER}"

  # --- 2) Passwordless sudo ---
  SUDO_RULE="/etc/sudoers.d/90-${USER}-nopasswd"
  echo "${USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee "${SUDO_RULE}"
  sudo chmod 0440 "${SUDO_RULE}"
  sudo visudo -cf "${SUDO_RULE}"
done
