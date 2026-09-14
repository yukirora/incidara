#!/usr/bin/env bash

set -euo pipefail

# Set fan mode to optimal if not full
# for Supermicro SYS-922GE-TNHR, ref:
# https://www.supermicro.com/manuals/other/IPMI_Users_Guide.pdf
FAN_OPTIMAL_MODE_ID="$(sudo IPMICFG-Linux.x86_64 -fan | awk -F':' '$2=="Optimal"{print $1; exit}')"
CURRENT_FAN_MODE="$(sudo IPMICFG-Linux.x86_64 -fan | awk -F'[][]' '/Current Fan Speed Mode/ {print $2}' | xargs)"
if [[ "$CURRENT_FAN_MODE" != "Full Mode" ]]; then
    sudo IPMICFG-Linux.x86_64 -fan ${FAN_OPTIMAL_MODE_ID}
fi

bash config_cpu_performance.sh
bash config_gpu_performance.sh
bash config_nvidia_peermem.sh
