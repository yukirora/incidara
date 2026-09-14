#!/usr/bin/env bash

set -euo pipefail

sudo -n sh -c 'nohup sh -c "sleep 2; /usr/bin/systemctl reboot" >/dev/null 2>&1 &'
