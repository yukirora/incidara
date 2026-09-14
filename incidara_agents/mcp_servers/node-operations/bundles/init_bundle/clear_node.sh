#!/usr/bin/env bash

set -euo pipefail

SYS_MOUNT="${SYS_MOUNT:-/mntsys}"
EXT_MOUNT="${EXT_MOUNT:-/mntext}"


NEW_PASS="${1:-}"
BMC_NEW_PASS="${2:-}"
NEW_HOSTNAME="${3:-}"

# --- 1) Remove users ---
for USER in admin operator user; do
  if id -u "${USER}"; then
    sudo pkill -9 -u "${USER}" || true
    sudo userdel -r "${USER}"
  fi
  sudo rm -rf "/home/${USER}"
  sudo rm -f "/etc/sudoers.d/90-${USER}-nopasswd"
done

# --- 2) Change BMC root/admin password to ${BMC_NEW_PASS} ---
if [[ -n "$BMC_NEW_PASS" ]]; then
  for user in root admin; do
    BMC_UID="$(sudo ipmitool user list 1 | awk -v u="$user" '$2==u {print $1; exit}')"
    if [[ -n "$BMC_UID" ]]; then
      sudo ipmitool user set password "$BMC_UID" "$BMC_NEW_PASS" || true
    fi
  done
fi

# --- 3) Remove docker instances ---
sudo docker ps -aq | xargs -r sudo docker stop || true
sudo docker ps -aq | xargs -r sudo docker rm -f || true
sudo docker images -aq | xargs -r sudo docker rmi -f || true
sudo docker builder prune -af || true

# --- 4) Remove disk content if soft raid present ---
DATA_MOUNT="${DATA_MOUNT:-/mnt/md0}"
sudo rm -rf "${DATA_MOUNT}"/*
sudo rm -rf $EXT_MOUNT/*
sudo rm -rf $SYS_MOUNT/*
sudo systemctl stop raid-setup || true
sudo systemctl disable raid-setup || true
if [ -f /etc/fstab.bak ]; then
  sudo cp -a /etc/fstab.bak /etc/fstab
fi
