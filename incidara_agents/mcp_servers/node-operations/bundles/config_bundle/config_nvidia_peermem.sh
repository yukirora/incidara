#!/usr/bin/env bash

set -euo pipefail

sudo tee /etc/rc.local >/dev/null <<'EOF'
#!/bin/bash
modprobe nvidia_peermem
EOF

sudo chmod +x /etc/rc.local
sudo /etc/rc.local
