#!/usr/bin/env bash

set -euo pipefail

sudo tee /etc/modules-load.d/rdma-setup.conf >/dev/null <<'EOF'
mlx5_ib
ib_uverbs
rdma_ucm
ib_umad
EOF

sudo awk '{print "modprobe "$1}' /etc/modules-load.d/rdma-setup.conf | sudo bash
