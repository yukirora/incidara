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


nvme_size_seq=\$(
  lsblk -b -dn -o NAME,SIZE |
  awk '\$1 ~ /^nvme[0-9]+n[0-9]+\$/ {print \$1,\$2}' |
  sort -V |
  awk '{printf "/dev/%s,%s,", \$1, \$2} END{print ""}'
)
echo \$nvme_size_seq
nvme_list=""
case "\$nvme_size_seq" in
  "/dev/nvme0n1,3840755982336,/dev/nvme1n1,3840755982336,/dev/nvme2n1,15360950534144,/dev/nvme3n1,15360950534144,/dev/nvme4n1,15360950534144,/dev/nvme5n1,15360950534144,/dev/nvme6n1,15360950534144,/dev/nvme7n1,15360950534144,/dev/nvme8n1,15360950534144,/dev/nvme9n1,15360950534144,/dev/nvme10n1,15360950534144,/dev/nvme11n1,15360950534144,/dev/nvme12n1,15360950534144,/dev/nvme13n1,15360950534144,/dev/nvme14n1,15360950534144,/dev/nvme15n1,15360950534144,/dev/nvme16n1,15360950534144,/dev/nvme17n1,15360950534144,")
    nvme_list="/dev/nvme2n1 /dev/nvme3n1 /dev/nvme4n1 /dev/nvme5n1 /dev/nvme6n1 /dev/nvme7n1 /dev/nvme8n1 /dev/nvme9n1 /dev/nvme10n1 /dev/nvme11n1 /dev/nvme12n1 /dev/nvme13n1 /dev/nvme14n1 /dev/nvme15n1 /dev/nvme16n1 /dev/nvme17n1" # h200 type-a
    ;;
  "/dev/nvme0n1,3840755982336,/dev/nvme1n1,3840755982336,/dev/nvme2n1,15360950534144,")
    nvme_list="/dev/nvme2n1" # h200 type-b
    ;;
  "/dev/nvme0n1,960197124096,/dev/nvme1n1,3840755982336,/dev/nvme2n1,3840755982336,/dev/nvme3n1,3840755982336,/dev/nvme4n1,3840755982336,")
    nvme_list="/dev/nvme1n1 /dev/nvme2n1 /dev/nvme3n1 /dev/nvme4n1" # h200 type-c
    ;;
  "/dev/nvme0n1,3840755982336,/dev/nvme1n1,3840755982336,")
    nvme_list="/dev/nvme1n1" # b300
    ;;
  "/dev/nvme0n1,3840755982336,/dev/nvme1n1,7681501126656,")
    nvme_list="/dev/nvme1n1" # cpu
    ;;
  "/dev/nvme0n1,7681501126656,/dev/nvme1n1,7681501126656,")
    nvme_list="/dev/nvme0n1 /dev/nvme1n1" # ctrl type-a
    ;;
  "/dev/nvme0n1,960197124096,/dev/nvme1n1,960197124096,/dev/nvme2n1,7681501126656,/dev/nvme3n1,7681501126656,")
    nvme_list="/dev/nvme2n1 /dev/nvme3n1" # ctrl type-b
    ;;
  "/dev/nvme0n1,960197124096,/dev/nvme1n1,960197124096,/dev/nvme2n1,7681501126656,/dev/nvme3n1,7681501126656,/dev/nvme4n1,7681501126656,/dev/nvme5n1,7681501126656,/dev/nvme6n1,7681501126656,/dev/nvme7n1,7681501126656,/dev/nvme8n1,7681501126656,/dev/nvme9n1,7681501126656,")
    nvme_list="/dev/nvme2n1 /dev/nvme3n1 /dev/nvme4n1 /dev/nvme5n1 /dev/nvme6n1 /dev/nvme7n1 /dev/nvme8n1 /dev/nvme9n1" # ctrl type-c
    ;;
  *)
    echo "ERROR: Unknown NVMe SKU sequence: \$nvme_size_seq"
    exit 1
    ;;
esac

known_md=0
unknown_md=0
for md in /dev/md*; do
  [[ -b \$md ]] || continue
  uuid=\$(blkid -s UUID -o value \$md 2>/dev/null || true)
  [[ -n \$uuid ]] || continue
  if grep \$uuid /etc/fstab; then
    known_md=\$((known_md + 1))
  else
    unknown_md=\$((unknown_md + 1))
  fi
done
if [[ \$known_md -eq 1 && \$unknown_md -eq 0 ]]; then
  exit 0
fi

for md in /dev/md*; do
  [[ -b \$md ]] || continue
  mdadm --stop \$md
  mdadm --remove \$md
done

mkdir -p $SYS_MOUNT

for d in \$nvme_list; do
  mdadm --zero-superblock \$d
done
nvme_count=\$(echo \$nvme_list |wc -w)
mdadm --create --run /dev/md0 --level=0 --raid-devices=\$nvme_count --force \$nvme_list
mkfs -t ext4 -F /dev/md0
sleep 5
lsblk -f
for ((i=0; i<10; i++)); do
  uuid=\$(blkid -s UUID -o value /dev/md0 2>/dev/null || true)
  if [ -n "\$uuid" ]; then
    break
  else
    echo "UUID not found. Attempt \$((i + 1))/10. Retrying..."
    sleep 5
  fi
done

output="UUID=\$uuid $SYS_MOUNT ext4 errors=remount-ro 0 1"
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
  if mount|grep md0; then
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
