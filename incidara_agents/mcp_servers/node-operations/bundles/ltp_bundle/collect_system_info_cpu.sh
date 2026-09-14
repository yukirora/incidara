#!/usr/bin/env bash

set -euo pipefail

sudo apt update
sudo apt install -y python3-pip jq
PIP_MIRROR="${PIP_MIRROR:-192.0.2.10}"
sudo pip3 install -i "http://${PIP_MIRROR}/simple" --trusted-host "${PIP_MIRROR}" -r requirements.txt
mkdir -p /tmp/sbsysinfo
SB_SYS_INFO_STANDALONE_RUN=1 sudo -E python3 system_info.py --output /tmp/sbsysinfo/info.json
python3 gen_sku_cpu.py --json /tmp/sbsysinfo/info.json --output /tmp/sbsysinfo/sku.txt
