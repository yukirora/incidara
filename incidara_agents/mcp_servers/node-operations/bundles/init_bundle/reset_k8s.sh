sudo systemctl stop kubelet
sudo systemctl disable kubelet
sudo apt purge -y kubelet kubeadm kubectl

sudo systemctl stop containerd
sudo systemctl disable containerd
sudo apt purge -y containerd containerd.io

sudo rm -rf /etc/kubernetes /var/lib/kubelet/* /var/lib/containerd/* /etc/containerd /etc/cni /opt/cni
sudo rm -rf /etc/kubernetes /var/lib/kubelet/* /var/lib/containerd/* /etc/containerd /etc/cni /opt/cni |& awk -F "'" '{print "sudo umount "$2}' | bash
sudo rm -rf /etc/kubernetes /var/lib/kubelet/* /var/lib/containerd/* /etc/containerd /etc/cni /opt/cni
# sudo reboot
