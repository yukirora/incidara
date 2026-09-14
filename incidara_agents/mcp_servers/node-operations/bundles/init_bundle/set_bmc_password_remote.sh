#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <hostlist> <bmc_user> <bmc_pass> <bmc_new_pass>" >&2
  exit 1
fi

HOSTLIST="$1"
BMC_USER="$2"
BMC_PASS="$3"
BMC_NEW_PASS="$4"

while IFS= read -r BMC_IP; do
  echo "processing $BMC_IP"
  USER_LIST=$(ipmitool -I lanplus -H "$BMC_IP" -U "$BMC_USER" -P "$BMC_PASS" user list 1)
  for user in root admin; do
    BMC_UID=$(awk -v u="$user" '$2==u {print $1; exit}' <<<"$USER_LIST")
    if [[ -n "$BMC_UID" ]]; then
      ipmitool -I lanplus -H "$BMC_IP" -U "$BMC_USER" -P "$BMC_PASS" user set password "$BMC_UID" "$BMC_NEW_PASS" || true
    fi
  done
done < "$HOSTLIST"
