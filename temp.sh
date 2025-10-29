#!/bin/bash

script() {
    # Remove existing SSH configuration
    rm -v /etc/ssh/sshd_config

    # Add new SSH configuration file with custom parameters
    if cat << 'EOF' > /usr/local/bin/prebuild.sh
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

main() {
    network
    passwd -d root
}

main
EOF
    chmod -v 700 /usr/local/bin/prebuild.sh
}

ssh_config() {
    # Remove existing SSH configuration
    rm -v /etc/ssh/sshd_config

    # Add new SSH configuration file with custom parameters
    if cat << 'EOF' > /etc/ssh/sshd_config
Include /etc/ssh/sshd_config.d/*.conf

#ListenAddress 10.0.10.0
Port 22
AllowTcpForwarding no
GatewayPorts no

PubkeyAuthentication yes
PermitRootLogin yes
PermitEmptyPasswords yes

ChallengeResponseAuthentication no

UsePAM yes

X11Forwarding no
PrintMotd no
PrintLastLog no

AcceptEnv LANG LC_*

Subsystem       sftp    /usr/lib/openssh/sftp-server
EOF
    systemctl daemon-reload
    systemctl enable prebuild
}

