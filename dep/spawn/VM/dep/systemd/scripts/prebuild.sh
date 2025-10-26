#!/bin/bash

set -euo pipefail  # Exit on error, undefined variable, or pipeline failure

printf "\e[32m*\e[0m PERFORMING SUBSEQUENT PROCEDURES\n"

# Function to log successful operations
success() {
    printf "\e[32mOK\e[0m %s\n" "$1"
}

# Function to log errors and exit
error() {
    printf "\e[31mERROR\e[0m %s\n" "$1" >&2
    printf "\e[31mERROR CRITICAL FAILURE: SYSTEM WILL NOT SHUT DOWN\e[0m\n"
    exit 1
}

# Create systemd service file
printf "\e[34m>\e[0m Creating systemd service: /etc/systemd/system/prebuild.service\n"
if cat << 'EOF' > /etc/systemd/system/prebuild.service
[Unit]
Description=Prebuild - Spawn Project
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c "\
  rm /etc/network/interfaces; \
  ip link set ens3 up; \
  ip addr add 10.0.12.249/24 dev ens3; \
  ip route add default via 10.0.12.254 dev ens3; \
  sed -i '1,$ c nameserver 10.0.6.62' /etc/resolv.conf; \
  sed -i '/^PermitRootLogin/d' /etc/ssh/sshd_config; \
  sed -i '/^PermitEmptyPasswords/d' /etc/ssh/sshd_config; \
  echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config; \
  echo 'PermitEmptyPasswords yes' >> /etc/ssh/sshd_config; \
  systemctl try-restart sshd; \
  passwd -d root"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
then
    success "Service file created successfully"
else
    error "Failed to create service file"
fi

# Reload systemd daemon and enable service
printf "\e[34m>\e[0m Reloading systemd daemon and enabling service\n"
if systemctl daemon-reload; then
    success "Systemd daemon reloaded successfully"
else
    error "Failed to reload systemd daemon"
fi

if systemctl enable prebuild.service --quiet; then
    success "Service 'prebuild.service' enabled successfully"
else
    error "Failed to enable service"
fi

# All operations completed successfully
printf "\e[32mOK ALL OPERATIONS COMPLETED SUCCESSFULLY\e[0m\n"
printf "\e[33m> Shutting down system in 5 seconds...\e[0m\n"
sleep 5
poweroff