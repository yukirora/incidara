#!/usr/bin/env bash

set -euo pipefail

NEW_PASS="${1:-}"
BMC_NEW_PASS="${2:-}"
NEW_HOSTNAME="${3:-}"

sudo tee /usr/local/bin/cpu-performance-setup.sh > /dev/null << EOF
#!/bin/bash
set -x

for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  echo performance | sudo tee "\$gov"
done
EOF

sudo chmod +x /usr/local/bin/cpu-performance-setup.sh

sudo tee /etc/systemd/system/cpu-performance-setup.service > /dev/null << EOF
[Unit]
Description=cpu performance setup
After=multi-user.target
Wants=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/cpu-performance-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable cpu-performance-setup.service
sudo systemctl start cpu-performance-setup.service
