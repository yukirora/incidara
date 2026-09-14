#!/usr/bin/env bash

set -euo pipefail

NEW_PASS="${1:-}"
BMC_NEW_PASS="${2:-}"
NEW_HOSTNAME="${3:-}"

# --- 1) Permanently change host name to ${2} ---
sudo hostnamectl set-hostname "${NEW_HOSTNAME}"
# Update /etc/hosts
sudo tee /etc/hosts <<EOF
127.0.0.1 localhost
127.0.1.1 ${NEW_HOSTNAME}

# The following lines are desirable for IPv6 capable hosts
::1     ip6-localhost ip6-loopback
fe00::0 ip6-localnet
ff00::0 ip6-mcastprefix
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
EOF

# --- 2) Remove the following users ---
USERS_TO_REMOVE=(it infra IP-HOST-MAC toor ubuntu set_bios)

for u in "${USERS_TO_REMOVE[@]}"; do
  if id -u "${u}" &>/dev/null; then
    sudo pkill -9 -u "${u}" || true
    sudo userdel -r "${u}"
  fi
  sudo rm -rf "/home/${u}"
done

sudo find /etc/sudoers.d -type f \
  ! -name '90-admin-nopasswd' \
  ! -name '90-operator-nopasswd' \
  ! -name 'README' \
  -exec rm -f {} +

# --- 3) Disable root password ---
sudo passwd -l root

# --- 4) Change BMC root/admin password to ${BMC_NEW_PASS} ---
# Best-effort: some BMCs reject password changes for various reasons.
# Never fail the entire script over BMC password issues.
if [[ -n "$BMC_NEW_PASS" ]]; then
  for user in root admin; do
    BMC_UID="$(sudo ipmitool user list 1 2>/dev/null | awk -v u="$user" '$2==u {print $1; exit}')"
    if [[ -n "$BMC_UID" ]]; then
      BMC_OUT="$(sudo ipmitool user set password "$BMC_UID" "$BMC_NEW_PASS" 2>&1)" && {
        echo "BMC password set for user ${user} (uid=${BMC_UID})"
      } || {
        # Decode common IPMI error codes
        if echo "$BMC_OUT" | grep -q '0x19'; then
          echo "WARNING: BMC password set failed for user ${user} (uid=${BMC_UID}) — error 0x19: password already set to this value (no change needed), continuing"
        elif echo "$BMC_OUT" | grep -q '0x80'; then
          echo "WARNING: BMC password set failed for user ${user} (uid=${BMC_UID}) — error 0x80: password too long (max 16 chars for IPMI 1.5/2.0), continuing"
        else
          echo "WARNING: BMC password set failed for user ${user} (uid=${BMC_UID}): ${BMC_OUT} — continuing"
        fi
      }
    fi
  done
fi

# --- 5) Set timezone ---
sudo timedatectl set-timezone Asia/Shanghai

# --- 6) Disable cloud init ---
sudo touch /etc/cloud/cloud-init.disabled

# --- 7) Mitigate CVE-2026-31431
echo "install algif_aead /bin/false" | sudo tee /etc/modprobe.d/disable-algif-aead.conf
sudo rmmod algif_aead 2>/dev/null || true
python3 -c '
import socket, sys
try:
    s = socket.socket(38, 5, 0)
    s.bind(("aead", "authencesn(hmac(sha256),cbc(aes))"))
    sys.exit(1)
except Exception:
    sys.exit(0)
'
echo 'CVE-2026-31431 mitigated'

# --- 8) Keep foreign rules to avoid ip rule lost during networkd restart with k8s deployed
sudo mkdir -p /etc/systemd/networkd.conf.d
sudo tee /etc/systemd/networkd.conf.d/99-keep-foreign-rules.conf >/dev/null <<'EOF'
[Network]
ManageForeignRoutingPolicyRules=no
ManageForeignRoutes=no
EOF
sudo systemctl daemon-reload
sudo systemctl restart systemd-networkd
