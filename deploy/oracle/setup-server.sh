#!/usr/bin/env bash
# Run once on a fresh Ubuntu VM:  bash deploy/oracle/setup-server.sh
set -euo pipefail
sudo apt-get update && sudo apt-get -y upgrade
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# 2 GB swap (important on the 1 GB AMD Micro shape, harmless elsewhere)
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
# Oracle's Ubuntu images ship iptables rules that block 80/443 even after you open them in the cloud console
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install netfilter-persistent
sudo netfilter-persistent save
echo "Done. Log out and back in (so the docker group applies), then continue with the guide."
