#!/bin/bash

cloud_kernel() {
    # Checks if hardware type is 'vm'
    HTYPE=$(hostnamectl chassis)
    if [ "$HTYPE" != "vm" ]; then
        echo "AVISO: Este não é um ambiente 'vm' (hostnamectl chassis = $HTYPE)."
        echo "O script não será executado. Abortando."
        exit 1
    fi

    apt-get update
    apt-get install -y linux-image-cloud-amd64 linux-headers-cloud-amd64

    # Creates the cleanup script that will run at boot
    cat << 'EOF' > /usr/local/sbin/kernel-cleanup.sh
#!/bin/bash

# This script is called by systemd at boot to remove old kernels.

if ! uname -r | grep -q "cloud"; then
    exit 0
fi

TO_REMOVE=$(dpkg-query -W -f='${Package}\n' 'linux-image-[0-9]*' | grep -v "cloud" | grep -v "$(uname -r)")

if dpkg-query -W -f='${Status}' linux-image-amd64 2>/dev/null | grep -q "ok installed"; then
    TO_REMOVE="$TO_REMOVE linux-image-amd64"
fi

if [ -n "$TO_REMOVE" ]; then
    apt-get purge -y $TO_REMOVE
    apt-get autoremove --purge -y
fi

systemctl disable kernel-cleanup.service
rm /etc/systemd/system/kernel-cleanup.service
rm /usr/local/sbin/kernel-cleanup.sh
systemctl daemon-reload
EOF

    chmod +x /usr/local/sbin/kernel-cleanup.sh

    # Creates the systemd service file
    cat << EOF > /etc/systemd/system/kernel-cleanup.service
[Unit]
Description=Creates the systemd service file. Cleans up old generic kernels after switching to the cloud kernel.
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kernel-cleanup.sh

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload && systemctl enable kernel-cleanup.service --quiet
}