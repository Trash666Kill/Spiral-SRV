#!/bin/bash

set -euo pipefail

print_ok() {
    printf "\e[32m*\e[0m %s\n" "$1"
}

print_err() {
    printf "\e[31m*\e[0m %s\n" "$1" >&2
    printf "\e[31m*\e[0m CRITICAL FAILURE: SYSTEM WILL NOT SHUT DOWN\n"
    exit 1
}

printf "\e[32m*\e[0m PERFORMING SUBSEQUENT PROCEDURES\n"

# Create systemd service file
printf "\e[33m*\e[0m ATTENTION: CREATING SYSTEMD SERVICE \033[32m/etc/systemd/system/prebuild.service\033[0m\n"
if cat << 'EOF' > /etc/systemd/system/prebuild.service
[Unit]
Description=Prebuild - Spawn Project
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c "\
  dhcpcd --release ens2
  rm /etc/network/interfaces; \
  ip link set ens2 up; \
  ip addr add 10.0.12.249/24 dev ens2; \
  ip route add default via 10.0.12.254 dev ens2; \
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
    print_ok "SYSTEMD SERVICE FILE CREATED SUCCESSFULLY"
else
    print_err "ERROR: FAILED TO CREATE SERVICE FILE \033[32m/etc/systemd/system/prebuild.service\033[0m"
fi

# Reload daemon
printf "\e[33m*\e[0m ATTENTION: RELOADING SYSTEMD DAEMON\n"
if systemctl daemon-reload; then
    print_ok "SYSTEMD DAEMON RELOADED SUCCESSFULLY"
else
    print_err "ERROR: FAILED TO RELOAD SYSTEMD DAEMON"
fi

# Enable service
printf "\e[33m*\e[0m ATTENTION: ENABLING SERVICE \033[32mprebuild.service\033[0m\n"
if systemctl enable prebuild.service --quiet; then
    print_ok "SERVICE \033[32mprebuild.service\033[0m ENABLED SUCCESSFULLY"
else
    print_err "ERROR: FAILED TO ENABLE SERVICE \033[32mprebuild.service\033[0m"
fi

# All steps succeeded → shutdown
print_ok "ALL OPERATIONS COMPLETED SUCCESSFULLY"
printf "\e[33m*\e[0m ATTENTION: SHUTTING DOWN SYSTEM IN 5 SECONDS...\n"
sleep 5
poweroff