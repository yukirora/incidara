#!/usr/bin/env bash

# set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <hostlist> <bmc_user> <bmc_pass>" >&2
  exit 1
fi

HOSTLIST="$1"
BMC_USER="$2"
BMC_PASS="$3"

while IFS= read -r BMC_IP; do
  echo "processing $BMC_IP"
  USER_LIST=$(ipmitool -I lanplus -H "$BMC_IP" -U "$BMC_USER" -P "$BMC_PASS" user list 1)
  for user in root admin; do
    BMC_UID=$(awk -v u="$user" '$2==u {print $1; exit}' <<<"$USER_LIST")
    if [[ -n "$BMC_UID" ]]; then
      ipmitool -I lanplus -H "$BMC_IP" -U "$BMC_USER" -P "$BMC_PASS" chassis bootdev disk options=persistent,efiboot
    fi
  done
done < "$HOSTLIST"
