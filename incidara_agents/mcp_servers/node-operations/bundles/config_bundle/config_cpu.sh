#!/usr/bin/env bash

set -euo pipefail

# Set Fan mode to optimal
# for H3C UniServer R4950 G6, ref:
# https://www.h3c.com/en/Support/Resource_Center/EN/Severs/Catalog/Rack_Server/R4950_G6/
sudo ipmitool raw 0x36 0x03 0xa2 0x63 0x00 0x60 0x03 0x01

bash config_cpu_performance.sh
bash config_rdma.sh
