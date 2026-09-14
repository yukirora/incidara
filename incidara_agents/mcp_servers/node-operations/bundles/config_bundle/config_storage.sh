#!/usr/bin/env bash

set -euo pipefail

FRU_ID="${FRU_ID:-0}"

fru_info="$(sudo ipmitool fru list "$FRU_ID")"

manufacturer="$(
    echo "$fru_info" |
    awk -F: '/Product Manufacturer/ {
        gsub(/^[ \t]+|[ \t]+$/, "", $2)
        print $2
        exit
    }'
)"

if [[ "$manufacturer" == "XFUSION" ]]; then
    # Set fan mode to power-saving (between low-noise and high-performance)
    # for xfusion 2288H V6, ref:
    # https://www.xfusion.com/support/#/en/docOnline/DOC2020000340?pid=23692812&relationId=EDOC1100136199&path=en-us_topic_0000001133327729

    sudo ipmitool raw 0x30 0x91 0x14 0xe3 0x00 0x01 0x00
    sudo ipmitool raw 0x30 0x92 0x14 0xe3 0x00 0x13 0x01 0x10

    bash config_cpu_performance.sh

elif [[ "$manufacturer" == "Maginfra" ]]; then
    bash config_cpu_performance.sh
    bash config_rdma.sh

else
    echo "[ERROR] Unknown machine manufacturer: ${manufacturer:-UNKNOWN}" >&2
    echo "[ERROR] Refusing to run machine-specific config." >&2
    exit 1
fi
