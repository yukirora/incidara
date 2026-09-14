#!/usr/bin/env bash

set -euo pipefail

output=$(sudo ipmitool chassis bootparam get 5) || {
    echo "failed to check bootparam before reboot"
    exit 1
}

if ! grep -q "Boot Device Selector : Force Boot from default Hard-Drive" <<< "$output"; then
    sudo ipmitool chassis bootdev disk options=persistent,efiboot || {
        echo "failed to set bootdev disk before reboot"
        exit 1
    }
    sudo -n sh -c 'nohup sh -c "sleep 2; ipmitool chassis power cycle" >/dev/null 2>&1 &'
else
    sudo -n sh -c 'nohup sh -c "sleep 2; /usr/bin/systemctl reboot" >/dev/null 2>&1 &'
fi
