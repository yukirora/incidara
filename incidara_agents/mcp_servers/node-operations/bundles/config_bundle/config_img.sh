#!/usr/bin/env bash

set -euo pipefail

SYS_MOUNT="${SYS_MOUNT:-/mntsys}"
EXT_MOUNT="${EXT_MOUNT:-/mntext}"


NEW_PASS="${1:-}"
BMC_NEW_PASS="${2:-}"
NEW_HOSTNAME="${3:-}"

sudo tee /usr/local/bin/raid-setup.sh > /dev/null << EOF
#!/bin/bash
set -x


IMG_PATH="/ltp.img"
IMG_SIZE="\$((128 * 1024 * 1024 * 1024))"

if grep "\$IMG_PATH" /etc/fstab; then
  if [[ -f "\$IMG_PATH" ]] && [[ "\$(stat -c%s "\$IMG_PATH")" -eq "\$IMG_SIZE" ]]; then
    exit 0
  fi
fi

rm -f "\$IMG_PATH"
mkdir -p $SYS_MOUNT
fallocate -l "\$IMG_SIZE" "\$IMG_PATH"
mkfs -t ext4 -F "\$IMG_PATH"
sleep 5

output="\$IMG_PATH $SYS_MOUNT ext4 loop,errors=remount-ro 0 1"
if [[ -f /etc/fstab.bak ]]; then
  cp /etc/fstab.bak /etc/fstab
else
  cp /etc/fstab /etc/fstab.bak
fi
echo \$output | tee --append /etc/fstab
systemctl daemon-reload

for ((i=0; i<5; i++))
do
mount $SYS_MOUNT
  if mount|grep $SYS_MOUNT|grep "\$IMG_PATH"; then
    break
  else
    sleep 2
  fi
done

mkdir -p $SYS_MOUNT/kubelet
mkdir -p /var/lib/kubelet
output="$SYS_MOUNT/kubelet /var/lib/kubelet ext4 defaults,bind,systemd.requires-mounts-for=$SYS_MOUNT 0 1"
echo \$output | tee --append /etc/fstab
systemctl daemon-reload
mount /var/lib/kubelet

mkdir -p $SYS_MOUNT/containerd
mkdir -p /var/lib/containerd
output="$SYS_MOUNT/containerd /var/lib/containerd ext4 defaults,bind,systemd.requires-mounts-for=$SYS_MOUNT 0 1"
echo \$output |  tee --append /etc/fstab
systemctl daemon-reload
mount /var/lib/containerd

mkdir -p $SYS_MOUNT$EXT_MOUNT
mkdir -p $EXT_MOUNT
output="$SYS_MOUNT$EXT_MOUNT $EXT_MOUNT ext4 defaults,bind,systemd.requires-mounts-for=$SYS_MOUNT 0 1"
echo \$output |  tee --append /etc/fstab
systemctl daemon-reload
mount $EXT_MOUNT
sleep 5
EOF

sudo chmod +x /usr/local/bin/raid-setup.sh

sudo tee /etc/systemd/system/raid-setup.service > /dev/null << EOF
[Unit]
Description=raid setup
DefaultDependencies=no
Before=local-fs-pre.target blk-availability.service
BindsTo=multipathd.service
After=multipathd.service

[Service]
TimeoutSec=600
ExecStartPre=/usr/local/bin/raid-setup.sh
ExecStart=/usr/bin/sleep infinity

[Install]
WantedBy=local-fs-pre.target
EOF

sudo systemctl enable raid-setup.service
sudo systemctl start raid-setup.service
