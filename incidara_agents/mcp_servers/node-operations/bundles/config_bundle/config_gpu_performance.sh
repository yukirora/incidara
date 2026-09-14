#!/usr/bin/env bash

set -euo pipefail

NEW_PASS="${1:-}"
BMC_NEW_PASS="${2:-}"
NEW_HOSTNAME="${3:-}"

# Enable GPU persistent mode and fabric manager
sudo systemctl enable --now nvidia-persistenced
sudo systemctl enable --now nvidia-fabricmanager

sudo tee /usr/local/bin/gpu-clock-setup.sh > /dev/null << EOF
#!/bin/bash
set -x

# Pin GPU memory and graphics clock
GPU_IDS=\$(nvidia-smi --query-gpu=index --format=csv,noheader)
for gid in \$GPU_IDS; do
  read mclock gclock <<< \$(
    sudo nvidia-smi -i \$gid \
      --query-gpu=clocks.max.mem,clocks.max.graphics \
      --format=csv,noheader,nounits \
      | tr -d ' ' | tr ',' ' '
  )
  echo "clocks.max.mem" \${mclock} "clocks.max.graphics" \${gclock}
  sudo nvidia-smi -i \$gid -ac \${mclock},\${gclock}
done
EOF

sudo chmod +x /usr/local/bin/gpu-clock-setup.sh

sudo tee /etc/systemd/system/gpu-clock-setup.service > /dev/null << EOF
[Unit]
Description=gpu clock setup
After=multi-user.target
Wants=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/gpu-clock-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable gpu-clock-setup.service
sudo systemctl start gpu-clock-setup.service
