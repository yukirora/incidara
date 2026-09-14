#!/usr/bin/env bash

set -euo pipefail

# Enable persistent mode to make collection faster
sudo systemctl enable --now nvidia-persistenced || true
nvidia-smi

sudo apt update
sudo apt install -y python3-pip
PIP_MIRROR="${PIP_MIRROR:-192.0.2.10}"
sudo pip3 install -i "http://${PIP_MIRROR}/simple" --trusted-host "${PIP_MIRROR}" -r requirements.txt
mkdir -p /tmp/sbsysinfo
SB_SYS_INFO_STANDALONE_RUN=1 sudo -E python3 system_info.py --output /tmp/sbsysinfo/info.json
python3 gen_sku.py --json /tmp/sbsysinfo/info.json --output /tmp/sbsysinfo/sku.txt
