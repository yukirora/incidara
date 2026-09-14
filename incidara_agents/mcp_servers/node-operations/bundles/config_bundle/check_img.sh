#!/usr/bin/env bash

set -euo pipefail

SYS_MOUNT="${SYS_MOUNT:-/mntsys}"
EXT_MOUNT="${EXT_MOUNT:-/mntext}"


sudo mount | grep $SYS_MOUNT | grep /ltp.img | grep ext4
sudo mount | grep /var/lib/kubelet | grep ext4
sudo mount | grep /var/lib/containerd | grep ext4
sudo mount | grep $EXT_MOUNT | grep ext4
sudo ls $SYS_MOUNT$EXT_MOUNT
