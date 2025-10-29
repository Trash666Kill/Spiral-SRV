#!/bin/bash

# Funções de saída colorida
print_ok() {
    printf "\e[32m[OK]\e[0m %s\n" "$1"
}

print_err() {
    printf "\e[31m[ERROR]\e[0m %s\n" "$1"
}

# Add new SSH configuration file with custom parameters
printf "\e[33m*\e[0m ATTENTION: CREATING SCRIPT \033[32m/root/prebuild.sh\033[0m\n"

if cat > /root/prebuild.sh << 'EOF'
#!/bin/bash
# Script to configure static network interface and enable root login via ssh

network() {
    dhcpcd --release ens2 > /dev/null 2>&1
    systemctl disable networking --quiet 2>/dev/null || true
    rm -f /etc/network/interfaces
    ip link set ens2 up
    ip addr add 10.0.12.249/24 dev ens2
    ip route add default via 10.0.12.254 dev ens2
    sed -i '1,$ c nameserver 10.0.6.62' /etc/resolv.conf
}

ssh_config() {
    sed -i '/^PermitRootLogin/d' /etc/ssh/sshd_config
    sed -i '/^PermitEmptyPasswords/d' /etc/ssh/sshd_config
    echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
    echo 'PermitEmptyPasswords yes' >> /etc/ssh/sshd_config
    systemctl try-restart sshd
}

main() {
    network
    ssh_config
    passwd -d root
}

main
EOF
then
    chmod 700 /root/prebuild.sh
    print_ok "SCRIPT /root/prebuild.sh CREATED AND MADE EXECUTABLE"
else
    print_err "FAILED TO CREATE /root/prebuild.sh"
    exit 1
fi

# Create systemd service file
printf "\e[33m*\e[0m ATTENTION: CREATING SYSTEMD SERVICE \033[32m/etc/systemd/system/prebuild.service\033[0m\n"

if cat > /etc/systemd/system/prebuild.service << 'EOF'
[Unit]
Description=Configure static network and enable root SSH (executed once at boot)
After=network-pre.target
Before=sshd.service
Wants=network-pre.target
ConditionPathExists=!/root/prebuild.sh.done

[Service]
Type=oneshot
ExecStart=/root/prebuild.sh
ExecStartPost=/bin/touch /root/prebuild.sh.done
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
then
    print_ok "SYSTEMD SERVICE FILE CREATED SUCCESSFULLY"
else
    print_err "ERROR: FAILED TO CREATE SERVICE FILE /etc/systemd/system/prebuild.service"
    exit 1
fi

# Reload daemon
printf "\e[33m*\e[0m ATTENTION: RELOADING SYSTEMD DAEMON\n"
if systemctl daemon-reload; then
    print_ok "SYSTEMD DAEMON RELOADED SUCCESSFULLY"
else
    print_err "ERROR: FAILED TO RELOAD SYSTEMD DAEMON"
    exit 1
fi

# Enable service
printf "\e[33m*\e[0m ATTENTION: ENABLING SERVICE \033[32mprebuild.service\033[0m\n"
if systemctl enable prebuild.service --quiet; then
    print_ok "SERVICE prebuild.service ENABLED SUCCESSFULLY"
else
    print_err "ERROR: FAILED TO ENABLE SERVICE prebuild.service"
    exit 1
fi

# Final success
print_ok "ALL OPERATIONS COMPLETED SUCCESSFULLY"
printf "\e[33m*\e[0m ATTENTION: SHUTTING DOWN SYSTEM IN 5 SECONDS...\n"
sleep 5

# Remove self
rm -f -- "$0"

# Power off
systemctl poweroff