#!/bin/bash

# Disable bash history
unset HISTFILE

connectiontest() {
    # Tests connectivity to Debian repositories
    if ! ping -4 -c 4 debian.org &>/dev/null; then
        printf "ERROR: UNABLE TO CONNECT TO \e[32mDEBIAN REPOSITORIES\e[0m\n"
        exit 1
    fi
}

# Execution directory
cd $PWD/dep

update() {
    printf "\e[32m*\e[0m UPDATING EXISTING REPOSITORY AND PACKAGES\n"

    # Updates the list of available packages
    apt-get -y update > /dev/null 2>&1

    # Performs the update of installed packages
    apt-get -y upgrade > /dev/null 2>&1
}

cloud_kernel() {
    printf "\e[32m*\e[0m INSTALLING AND CONFIGURING CLOUD KERNEL\n"

    # Checks if hardware type is 'vm'
    HTYPE=$(hostnamectl chassis)
    if [ "$HTYPE" != "vm" ]; then
        printf "\e[31m* ERROR:\e[0m THIS IS NOT A VIRTUAL ENVIRONMENT.\n"
        exit 1
    fi

    apt-get install -y linux-image-cloud-amd64 linux-headers-cloud-amd64 > /dev/null 2>&1

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

interface() {
    # Installing required packages
    apt-get -y install bc > /dev/null 2>&1
    printf "\e[32m*\e[0m CHOOSING THE BEST AVAILABLE INTERFACE, WAIT...\n"

    # Target IP for ping
    TARGET_IP="8.8.8.8"

    # Variables to store the interface with the lowest latency, its altname, IP, netmask, and gateway
    BEST_INTERFACE=""
    BEST_LATENCY=9999.0
    BEST_ALTNAME=""
    BEST_IP=""
    BEST_NETMASK=""
    BEST_GATEWAY=""
    DEST_SCRIPT_PATH="/root/.services/network.sh"

    # Iterate over active interfaces (status UP) starting with eth, en, or enp
    for IFACE in $(ip -o link show | awk -F': ' '/state UP/ && ($2 ~ /^(eth|en|enp)/) {sub(/@.*/, "", $2); print $2}'); do
        # Test ping on the interface with 3 packets, capture average latency
        LATENCY=$(ping -I "$IFACE" -4 -c 3 "$TARGET_IP" 2>/dev/null | awk -F'/' 'END {print $5}') || continue

        # Compare current latency with the best found so far
        if [[ -n "$LATENCY" && $(echo "$LATENCY < $BEST_LATENCY" | bc -l) -eq 1 ]]; then
            BEST_LATENCY="$LATENCY"
            BEST_INTERFACE="$IFACE"
            # Extract the first altname of the interface
            BEST_ALTNAME=$(ip addr show "$IFACE" | awk '/altname/ {print $2; exit}')
            # Extract the IP address and netmask (CIDR) of the interface
            IP_INFO=$(ip -4 addr show "$IFACE" | grep 'inet' | awk '{print $2}' | head -n1)
            BEST_IP=$(echo "$IP_INFO" | cut -d'/' -f1)
            BEST_NETMASK=$(echo "$IP_INFO" | cut -d'/' -f2)
            # Extract the gateway for the interface
            BEST_GATEWAY=$(ip route show dev "$IFACE" | awk '/default/ {print $3}')
        fi
    done

    # Check if a valid interface was found
    if [[ -z "$BEST_INTERFACE" ]]; then
        printf "\033[31m*\033[0m ERROR: NO VALID INTERFACE FOUND TO WRITE TO /etc/environment\n"
        exit 1
    fi

    # Convert CIDR to decimal netmask for /etc/environment
    case "$BEST_NETMASK" in
        8) DECIMAL_NETMASK="255.0.0.0" ;;
        16) DECIMAL_NETMASK="255.255.0.0" ;;
        24) DECIMAL_NETMASK="255.255.255.0" ;;
        32) DECIMAL_NETMASK="255.255.255.255" ;;
        *) printf "\033[31m*\033[0m ERROR: UNSUPPORTED NETMASK \033[32m/%s\033[0m\n" "$BEST_NETMASK"; exit 1 ;;
    esac

    # Assign to global variable
    NIC0="$BEST_INTERFACE"
    printf "\e[32m*\e[0m CHOSEN INTERFACE: \033[32m%s\033[0m, LATENCY OF \033[32m%s ms\033[0m FOR \033[32m%s\033[0m\n" "$NIC0" "$BEST_LATENCY" "$TARGET_IP"

    # Write NIC0, NIC0_ALT, IPV4, GW, and MASK to /etc/environment
    if [[ -n "$BEST_ALTNAME" && -n "$BEST_INTERFACE" && -n "$BEST_IP" && -n "$BEST_GATEWAY" && -n "$DECIMAL_NETMASK" ]]; then
        printf "\e[32m*\e[0m WRITING ALTNAME \033[32m%s\033[0m, INTERFACE \033[32m%s\033[0m, IP \033[32m%s\033[0m, GATEWAY \033[32m%s\033[0m, AND NETMASK \033[32m%s\033[0m TO /etc/environment\n" "$BEST_ALTNAME" "$NIC0" "$BEST_IP" "$BEST_GATEWAY" "$DECIMAL_NETMASK"
        touch /etc/environment
        sed -i '/^NIC0=/d' /etc/environment
        sed -i '/^NIC0_ALT=/d' /etc/environment
        sed -i '/^IPV4=/d' /etc/environment
        sed -i '/^GW=/d' /etc/environment
        sed -i '/^MASK=/d' /etc/environment
        echo "NIC0=$NIC0" >> /etc/environment
        echo "NIC0_ALT=$BEST_ALTNAME" >> /etc/environment
        echo "IPV4=$BEST_IP" >> /etc/environment
        echo "GW=$BEST_GATEWAY" >> /etc/environment
        echo "MASK=$DECIMAL_NETMASK" >> /etc/environment
    else
        printf "\033[31m*\033[0m ERROR: NO VALID INTERFACE, ALTNAME, IP, GATEWAY, OR NETMASK FOUND TO WRITE TO /etc/environment\n"
        exit 1
    fi
}

hostname() {
    # Install the required packages
    apt-get -y install uuid uuid-runtime > /dev/null 2>&1

    # Generates a new hostname based on the chassis type and a random value
    HOSTNAME="vm$(shuf -i 100000-999999 -n 1)"

    printf "\e[32m*\e[0m GENERATED HOSTNAME: \033[32m%s\033[0m\n" "$HOSTNAME"

    # Remove the /etc/hostname file and write the new hostname
    rm /etc/hostname
    printf "$HOSTNAME" > /etc/hostname

    # Remove the /etc/hosts file and writes the new hosts entries
    rm /etc/hosts
    printf "127.0.0.1       localhost
127.0.1.1       "$HOSTNAME"

::1     localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters" > /etc/hosts
}

target_user() {
    # Install the sudo package
    apt-get -y install sudo > /dev/null 2>&1

    # Modify /etc/profile to disable command history
    sed -i '$ a unset HISTFILE\nexport HISTSIZE=0\nexport HISTFILESIZE=0\nexport HISTCONTROL=ignoreboth' /etc/profile

    printf "\e[32m*\e[0m CREATING USER \e[32mSysOp\e[0m\n"

    # Create the sysop group with GID 1001
    groupadd -g 1001 sysop

    # Create the sysop user with UID 1001, sysop group, and bash shell
    useradd -m -u 1001 -g 1001 -c "SysOp" -s /bin/bash sysop

    # Get the name of the created user
    TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

    # Add the user to the sudo group
    /sbin/usermod -aG sudo "$TARGET_USER"
}

packages() {
    text_editor() {
        # Install text editor package
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: TEXT EDITOR\n"
        EDITOR="vim"
        apt-get -y install $EDITOR > /dev/null 2>&1
    }

    network_tools() {
        # Install network tools packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: NETWORK TOOLS\n"
        NETWORK="nfs-common tcpdump traceroute iperf ethtool geoip-bin socat speedtest-cli bridge-utils"
        apt-get -y install $NETWORK > /dev/null 2>&1
    }

    security() {
        # Install security tools
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: SECURITY TOOLS\n"
        SECURITY="apparmor-utils"
        apt-get -y install $SECURITY > /dev/null 2>&1
    }

    compression() {
        # Install compression and archiving packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: COMPRESSION AND ARCHIVING\n"
        COMPRESSION="unzip xz-utils bzip2 pigz"
        apt-get -y install $COMPRESSION > /dev/null 2>&1
    }

    scripting() {
        # Install scripting and automation support packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: SCRIPTING AND AUTOMATION SUPPORT\n"
        SCRIPTING="sshpass python3-apt-get"
        apt-get -y install $SCRIPTING > /dev/null 2>&1
    }

    monitoring() {
        # Install system monitoring and diagnostics packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: SYSTEM MONITORING AND DIAGNOSTICS\n"
        MONITORING="screen htop nload"
        apt-get -y install $MONITORING > /dev/null 2>&1
    }

    fs_utils() {
        # Install disk and file system utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: DISK AND FILE SYSTEM UTILITIES\n"
        FS_UTILS="cryptsetup uuid rsync"
        apt-get -y install $FS_UTILS > /dev/null 2>&1
    }

    connectivity() {
        # Install connectivity utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: CONNECTIVITY UTILITIES\n"
        CONNECTIVITY="curl wget net-tools"
        apt-get -y install $CONNECTIVITY > /dev/null 2>&1
    }

    extra_utils() {
        # Install additional utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: ADDITIONAL UTILITIES\n"
        EXTRA_UTILS="tree pwgen"
        apt-get -y install $EXTRA_UTILS > /dev/null 2>&1
    }

    # Call
    text_editor
    network_tools
    security
    compression
    scripting
    monitoring
    fs_utils
    connectivity
    extra_utils
}

directories() {
    printf "\e[32m*\e[0m CREATING DIRECTORIES\n"

    # Create directories for temporary services and data
    mkdir -p /mnt/{Temp,Services} && chown "$TARGET_USER":"$TARGET_USER" -R /mnt/*
    mkdir -p /root/{Temp,.services/scheduled,.crypt} && chmod 600 /root/.crypt

    # Create directory for rsync logs and adjust permissions
    mkdir /var/log/rsync && chown "$TARGET_USER":"$TARGET_USER" -R /var/log/rsync

    # Creates specific directories for the target user
    su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/{Temp,.services/scheduled,.crypt}"
}

trigger() {
    printf "\e[32m*\e[0m SETTING UP MAIN SYSTEMD SERVICE\n"

    # Adding the main start service
    cp systemd/trigger.service /etc/systemd/system && systemctl enable trigger --quiet

    # Adding central configuration file
    cp systemd/scripts/main.sh /root/.services && chmod 700 /root/.services/main.sh
}

network() {
    printf "\e[32m*\e[0m SETTING UP NETWORK\n"

    # Adding Network Configuration File
    cp systemd/scripts/network.sh /root/.services && chmod 700 /root/.services/network.sh

    # Disabling services (with full error suppression)
    systemctl disable networking --quiet 2>/dev/null || true
    systemctl disable ModemManager --quiet 2>/dev/null || true
    systemctl disable wpa_supplicant --quiet 2>/dev/null || true
    systemctl disable NetworkManager-wait-online --quiet 2>/dev/null || true
    systemctl disable NetworkManager.service --quiet 2>/dev/null || true

    ntp() {
        TIMEZONE="America/Sao_Paulo"

        # Install and configure the 'systemd-timesyncd' time synchronization service
        apt-get -y install systemd-timesyncd > /dev/null 2>&1

        # Disables and stops the systemd-timesyncd service
        systemctl disable --now systemd-timesyncd --quiet

        # Fixing NTP Server
        sed -i 's/#NTP=/NTP=10.0.6.62/' /etc/systemd/timesyncd.conf

        # Set the time zone
        export TZ=${TIMEZONE}

        # Remove the current time zone setting
        rm /etc/localtime

        # Copy time zone setting
        cp /usr/share/zoneinfo/${TIMEZONE} /etc/localtime

        # Update the system configuration to use the correct time zone
        timedatectl set-timezone ${TIMEZONE}
    }

    dns() {
        # Install and configure the 'systemd-resolved' time synchronization service
        apt-get -y install systemd-resolved > /dev/null 2>&1

        # Disables and stops the systemd-resolved service
        systemctl disable --now systemd-resolved --quiet
    }

    # Call
    ntp
    dns
}

firewall() {
    printf "\e[32m*\e[0m SETTING UP FIREWALL\n"

    # Install required dependencies
    apt-get -y install nftables rsyslog > /dev/null 2>&1

    # Configure firewall services and scripts
    systemctl disable --now nftables --quiet
    cp -r systemd/scripts/firewall /root/.services/
    chmod 700 /root/.services/firewall/*.sh && chattr +i /root/.services/firewall/{a.sh,c.sh}
}

mount() {
    printf "\e[32m*\e[0m SETTING MOUNT POINTS\n"

    # Adding Mount Configuration File
    cp systemd/scripts/mount.sh /root/.services && chmod 700 /root/.services/mount.sh
}

ssh() {
    printf "\e[32m*\e[0m SETTING UP SSH\n"

    # Install the required packages
    apt-get -y install openssh-server sshfs autossh > /dev/null 2>&1

    # Remove existing SSH configuration
    rm /etc/ssh/sshd_config

    # Add new SSH configuration file with custom parameters
    cp sshd_config /etc/ssh/ && chmod 644 /etc/ssh/sshd_config

    # Remove the old motd file and create a new empty one
    rm /etc/motd && touch /etc/motd

    # Adjust root .ssh folder permissions to ensure security
    chmod 600 /root/.ssh

    # Create root SSH key and adjust permissions of authorized keys folder
    touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
    ssh-keygen -t rsa -b 4096 -N '' <<<$'\n' > /dev/null 2>&1

    # Create SSH key for specified user and adjust permissions of .ssh folder
    su - "$TARGET_USER" -c "echo | ssh-keygen -t rsa -b 4096 -N '' <<<$'\n'" > /dev/null 2>&1
    chmod 700 /home/"$TARGET_USER"/.ssh

    # Create the user's authorized_keys file and adjust permissions
    su - "$TARGET_USER" -c "echo | touch /home/"$TARGET_USER"/.ssh/authorized_keys"
    chmod 600 /home/"$TARGET_USER"/.ssh/authorized_keys
}

grub() {
    printf "\e[32m*\e[0m SETTING UP GRUB\n"

    # Remove the current GRUB configuration file (if it exists)
    rm -f /etc/default/grub

    # Create a new GRUB configuration file with custom parameters
    printf 'GRUB_DEFAULT=0
GRUB_TIMEOUT=0
GRUB_DISTRIBUTOR=`lsb_release -i -s 2> /dev/null || echo Debian`
GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"
GRUB_CMDLINE_LINUX=""' > /etc/default/grub && chmod 644 /etc/default/grub

    # Update GRUB configuration
    update-grub

    # Completion message
    if [ $? -eq 0 ]; then
        printf "\e[32m*\e[0m GRUB CONFIGURATION UPDATED SUCCESSFULLY\n"
    else
        printf "\e[31m*\e[0m ERROR: FAILED TO UPDATE GRUB CONFIGURATION\n"
    fi
}

later() {
printf "\e[32m*\e[0m SCHEDULING SUBSEQUENT CONSTRUCTION PROCEDURES AFTER RESTART\n"

    # Grep for UID 1000 (temp ~ user)
    TARGET_USER=$(grep 1000 /etc/passwd | cut -f 1 -d ":")

    # Creates the startup script that will be executed after reboot
    printf '#!/bin/bash
### BEGIN INIT INFO
# Provides:          later
# Required-Start:    $all
# Required-Stop:     
# Default-Start:     2 3 4 5
# Default-Stop:      
# Short-Description: Procedures subsequent to instance construction only possible after reboot
### END INIT INFO

# Terminates all processes of user TARGET_USER
pkill -u %s

# Remove the user TARGET_USER and its home directory
userdel -r %s

# Remove the VM folder from the /root directory
rm -rf /root/build.sh

# Remove the init.d script after it runs
rm -f /etc/init.d/later' "$TARGET_USER" "$TARGET_USER" > /etc/init.d/later && chmod +x /etc/init.d/later

    # Add the script to the services that will start at boot
    update-rc.d later defaults
}

finish() {
    # Remove unused packages and unnecessary dependencies
    apt-get -y autoremove > /dev/null 2>&1

    # Remove default network configuration file to avoid conflicts, with warning if not found
    if [[ ! -f /etc/network/interfaces ]]; then
        printf "\e[33m* WARNING: /etc/network/interfaces does not exist, skipping removal\e[0m\n"
    else
        rm -f /etc/network/interfaces
    fi
}

# Main function to orchestrate the setup
main() {
    connectiontest
    update
    cloud_kernel
    interface
    hostname
    target_user
    packages
    directories
    trigger
    firewall
    mount
    ssh
    grub
    network
    later
    finish
}

# Execute main function
main