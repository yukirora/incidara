#!/usr/bin/env bash

# 1) Remove docker instances
sudo docker ps -aq | xargs -r sudo docker stop
sudo docker ps -aq | xargs -r sudo docker rm -f
sudo docker images -aq | xargs -r sudo docker rmi -f
sudo docker builder prune -af
sudo apt purge -y docker*

# 2) Drain k8s
sudo systemctl stop kubelet
sudo systemctl disable kubelet
sudo apt purge -y kube*

sudo systemctl stop containerd
sudo systemctl disable containerd
sudo apt purge -y containerd*

timeout 60s sudo rm -rf /etc/kubernetes /var/lib/kubelet/* /etc/containerd /etc/cni /opt/cni /var/lib/containerd/* >/dev/null 2>&1
timeout 60s sudo rm -rf /etc/kubernetes /var/lib/kubelet/* /etc/containerd /etc/cni /opt/cni /var/lib/containerd/* |& awk -F "'" '{print "sudo umount "$2}' | timeout 60s bash >/dev/null 2>&1
timeout 60s sudo rm -rf /etc/kubernetes /var/lib/kubelet/* /etc/containerd /etc/cni /opt/cni /var/lib/containerd/* >/dev/null 2>&1

# 3) Remove disk content
sudo umount /var/lib/kubelet
sudo umount /var/lib/containerd
sudo umount $EXT_MOUNT
sudo umount $SYS_MOUNT
sudo systemctl stop raid-setup
sudo systemctl disable raid-setup
if [ -f /etc/fstab.bak ]; then
  sudo cp -a /etc/fstab.bak /etc/fstab
fi

# 4) Reboot
# sudo -n sh -c 'nohup sh -c "sleep 2; /usr/bin/systemctl reboot" >/dev/null 2>&1 &'
