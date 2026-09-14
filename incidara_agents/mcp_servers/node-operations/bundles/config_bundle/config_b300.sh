#!/usr/bin/env bash

set -euo pipefail

# Set fan speed to 85 percent
# ref: BMC IPMI OEM Command.pdf
sudo ipmitool raw 0x2e 0x10 0x0a 0x3c 0 64 1 0xd9 0xff

bash config_cpu_performance.sh
bash config_gpu_performance.sh
bash config_nvidia_peermem.sh
