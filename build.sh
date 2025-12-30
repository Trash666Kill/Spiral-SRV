#!/bin/bash

# Disable bash history
unset HISTFILE

# Execution directory
cd $PWD/dep

update() {
    printf "\e[32m*\e[0m UPDATING EXISTING REPOSITORY AND PACKAGES\n"

    # Updates the list of available packages
    apt-get -y update > /dev/null 2>&1

    # Performs the update of installed packages
    apt-get -y upgrade > /dev/null 2>&1
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
    HOSTNAME="srv$(shuf -i 10000-99999 -n 1)"

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

passwords() {
    # Install the package required for password generation
    apt-get -y install pwgen > /dev/null 2>&1

    # Generate two secure passwords with special characters and 18 characters
    PASSWORD_ROOT=$(pwgen -s 18 1)
    PASSWORD_TARGET=$(pwgen -s 18 1)

    # Check if the TARGET_USER exists in the system
    if ! id "$TARGET_USER" &>/dev/null; then
        printf "\e[31m* ERROR:\e[0m USER '$TARGET_USER' DOES NOT EXIST. TERMINATING SCRIPT.\n"
        exit 1
    fi

    # Change the root user's password
    echo "root:$PASSWORD_ROOT" | chpasswd
    if [ $? -ne 0 ]; then
        printf "\e[31m* ERROR:\e[0m FAILED TO CHANGE ROOT PASSWORD.\n"
        exit 1
    fi

    # Change the TARGET_USER's password
    echo "$TARGET_USER:$PASSWORD_TARGET" | chpasswd
    if [ $? -ne 0 ]; then
        printf "\e[31m* ERROR:\e[0m FAILED TO CHANGE PASSWORD FOR USER '$TARGET_USER'.\n"
        exit 1
    fi

    echo -e "\033[32m*\033[0m GENERATED PASSWORD FOR \033[32mSysOp\033[0m USER: \033[32m\"$PASSWORD_TARGET\"\033[0m"
    echo -e "\033[32m*\033[0m GENERATED PASSWORD FOR \033[32mRoot\033[0m USER: \033[32m\"$PASSWORD_ROOT\"\033[0m"
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
        MONITORING="screen htop sysstat stress lm-sensors nload smartmontools"
        apt-get -y install $MONITORING > /dev/null 2>&1
    }

    fs_utils() {
        # Install disk and file system utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: DISK AND FILE SYSTEM UTILITIES\n"
        FS_UTILS="hdparm ntfs-3g dosfstools btrfs-progs mergerfs cryptsetup uuid rsync"
        apt-get -y install $FS_UTILS > /dev/null 2>&1
    }

    connectivity() {
        # Install connectivity utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: CONNECTIVITY UTILITIES\n"
        CONNECTIVITY="curl wget net-tools"
        apt-get -y install $CONNECTIVITY > /dev/null 2>&1
    }

    power_management() {
        # Install power and system management utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: POWER AND SYSTEM MANAGEMENT UTILITIES\n"
        POWER_MGMT="pm-utils acpi acpid fwupd"
        apt-get -y install $POWER_MGMT > /dev/null 2>&1
    }

    resource_control() {
        # Install resource limiting and control packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: RESOURCE LIMITING AND CONTROL\n"
        RESOURCE_CTRL="cpulimit"
        apt-get -y install $RESOURCE_CTRL > /dev/null 2>&1
    }

    graphics_network() {
        # Install graphics and network drivers and firmware packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: GRAPHICS AND NETWORK DRIVERS AND FIRMWARE\n"
        MISC="firmware-misc-nonfree"
        NETWORK="firmware-realtek firmware-atheros"
        GRAPHICS="firmware-amd-graphics"
        apt-get -y install $MISC $NETWORK > /dev/null 2>&1
    }

    extra_utils() {
        # Install additional utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: ADDITIONAL UTILITIES\n"
        EXTRA_UTILS="tree"
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
    power_management
    resource_control
    graphics_network
    extra_utils
}

directories() {
    printf "\e[32m*\e[0m CREATING DIRECTORIES\n"

    # Create directories for temporary services and data
    mkdir -p /mnt/{Temp,Local/{Container/{A,B},USB/{A,B}},Remote/Servers}
    mkdir -p /root/{Temp,.services/scheduled,.crypt} && chmod 600 /root/.crypt

    # Adjusting the location of standard environment variables
    mv /etc/environment /root/.services && ln -s /root/.services/environment /etc/environment

    # Create directory for rsync logs and adjust permissions
    mkdir /var/log/rsync && chown "$TARGET_USER":"$TARGET_USER" -R /var/log/rsync

    # Create specific directories for the target user
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
    cp systemd/scripts/network.sh /root/.services/ && chmod 700 /root/.services/network.sh

    # Disabling services (with full error suppression)
    systemctl disable networking --quiet 2>/dev/null || true
    systemctl disable ModemManager --quiet 2>/dev/null || true
    systemctl disable wpa_supplicant --quiet 2>/dev/null || true
    systemctl disable NetworkManager-wait-online --quiet 2>/dev/null || true
    systemctl disable NetworkManager.service --quiet 2>/dev/null || true

    # Collects the MAC address and stores it in the variable
    MAC=$(ip link show "$NIC0" | awk '/ether/ {print $2}')

    # Update the MAC address in /root/.services/network.sh
    sed -i "/ip link set dev br_vlan710 address/s/$/ $MAC/" /root/.services/network.sh

    ntp() {
        TIMEZONE="America/Sao_Paulo"

        # Install and configure the 'systemd-timesyncd' time synchronization service
        apt-get -y install systemd-timesyncd > /dev/null 2>&1

        # Disables and stops the systemd-timesyncd service
        systemctl disable --now systemd-timesyncd --quiet

        # Fixing NTP Server
        sed -i 's/#NTP=/NTP=10.0.6.1/' /etc/systemd/timesyncd.conf

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
        # Install and configure the 'dnsmasq' DNS Server
        apt-get -y install dnsmasq dnsutils tcpdump > /dev/null 2>&1

        # Disables and stops the dnsmasq service
        systemctl disable --now dnsmasq --quiet

        # Removing the default dnsmasq configuration
        rm /etc/dnsmasq.conf

        # Adding central configuration file
        cp systemd/scripts/main.conf /etc/dnsmasq.d/

        # Defining domain based on host
        sed -i "s/domain=.*/domain=$HOSTNAME.local/" /etc/dnsmasq.d/main.conf

        # Creating dnsmasq configuration directories
        mkdir /etc/dnsmasq.d/config

        # Adding the hostname to the hosts file
        printf '10.0.10.254 %s.local' "$HOSTNAME" > /etc/dnsmasq.d/config/hosts

        # Fixing DNS Server
        chattr + /etc/resolv.conf

        # Creates the Upstream DNS server declaration file that will be used by dnsmasq
        grep '^nameserver' /etc/resolv.conf | awk '{print "nameserver " $2}' | tee -a /etc/dnsmasq.d/config/resolv > /dev/null

        # Creating the dnsmasq IP reservations file
        touch /etc/dnsmasq.d/config/reservations
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
    chmod 700 /root/.services/firewall/*.sh && chattr +i /root/.services/firewall/{a.sh,b.sh}

    # Create the rsyslog configuration file to filter nftables logs
    cat <<EOF > /etc/rsyslog.d/50-nftables.conf
# /etc/rsyslog.d/50-nftables.conf
:msg, contains, "FORWARD_DROP: " /var/log/nftables.log
& stop
EOF

    # Create the configuration file for nftables log rotation
    cat <<'EOF' > /etc/logrotate.d/nftables
/var/log/nftables.log
{
    rotate 7
    daily
    missingok
    notifempty
    delaycompress
    compress
    postrotate
        systemctl restart rsyslog > /dev/null
    endscript
}
EOF
}

mount() {
    printf "\e[32m*\e[0m SETTING MOUNT POINTS AND FILE SHARING\n"

    # Install NFS sharing service
    apt-get -y install nfs-kernel-server > /dev/null 2>&1

    # Disable and stop NFS related service
    systemctl disable --now nfs-kernel-server --quiet

    # Adding Mount Configuration File
    cp systemd/scripts/mount.sh /root/.services/ && chmod 700 /root/.services/mount.sh

    # Create NFS export configuration
    printf '#/mnt/Local/Container/A 172.16.0.0(rw,sync,crossmnt,no_subtree_check,no_root_squash)' > /etc/exports
}

hypervisor() {
    printf "\e[32m*\e[0m SETTING UP HYPERVISOR\n"

    kvm() {
        # Identifies the processor manufacturer
        CPU=$(lscpu | grep -E 'Vendor ID|ID de fornecedor' | cut -f 2 -d ":" | sed -n 1p | awk '{$1=$1}1')

        # Install KVM and required dependencies
        apt-get -y install qemu-kvm libvirt0 libvirt-daemon-system > /dev/null 2>&1

        # Disable and stop libvirt service to configure manually
        systemctl disable --now libvirtd --quiet

        # Adds target user to 'libvirt' group for management permissions
        gpasswd libvirt -a "$TARGET_USER" > /dev/null 2>&1

        # Configures the kernel module with nested virtualization support.
        touch /etc/modprobe.d/kvm.conf
        case "$CPU" in
            GenuineIntel)
                printf 'options kvm_intel nested=1' > /etc/modprobe.d/kvm.conf
                /sbin/modprobe -r kvm_intel
                /sbin/modprobe kvm_intel
                ;;
            AuthenticAMD)
                printf 'options kvm_amd nested=1' > /etc/modprobe.d/kvm.conf
                /sbin/modprobe -r kvm_amd
                /sbin/modprobe kvm_amd
                ;;
            *)
                printf "\e[32m** UNKNOWN OR UNSUPPORTED CPU ARCHITECTURE **\e[0m\n"
                ;;
            esac

        # Creating directories for scripts and logs
        mkdir /var/log/virsh && chown "$TARGET_USER":"$TARGET_USER" -R /var/log/virsh

        # Add startup script to start libvirt service and start virtual machines
        cp systemd/scripts/virtual-machine.sh /root/.services/ && chmod 700 /root/.services/virtual-machine.sh
    }

    lxc() {
        # Install LXC and required dependencies
        apt-get -y install lxc > /dev/null 2>&1

        # Allow custom storage path
        tee /etc/apparmor.d/usr.bin.lxc-copy <<'EOF'
abi <abi/4.0>,
#include <tunables/global>

/usr/bin/lxc-copy flags=(attach_disconnected) {
  #include <abstractions/lxc/start-container>
  /mnt/Local/Container/A/lxc/** rw,
  mount options=(rw, move) -> /mnt/Local/Container/A/lxc/**,
}
EOF
        apparmor_parser -r /etc/apparmor.d/usr.bin.lxc-copy

        # Disable and stop lxc and lxc-net services
        systemctl disable --now lxc --quiet
        systemctl disable --now lxc-net --quiet && systemctl mask lxc-net --quiet

        # Remove lxc-net related configuration files
        rm /etc/default/lxc-net && rm /etc/lxc/default.conf

        # Create the LXC configuration file
        printf 'lxc.net.0.type = veth
lxc.net.0.link = br_tap110
lxc.net.0.flags = up

lxc.apparmor.profile = generated
lxc.apparmor.allow_nesting = 1' > /etc/lxc/default.conf

        # Create log directory and adjust permissions
        mkdir /var/log/lxc; chown "$TARGET_USER":"$TARGET_USER" -R /var/log/lxc

        # Add startup script to start lxc service and start containers
        cp systemd/scripts/container.sh /root/.services/ && chmod 700 /root/.services/container.sh
    }

    # Call
    kvm
    lxc
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
    sed -i "s/@debian$/@$HOSTNAME/" /root/.ssh/id_rsa.pub

    # Create SSH key for specified user and adjust permissions of .ssh folder
    su - "$TARGET_USER" -c "echo | ssh-keygen -t rsa -b 4096 -N '' <<<$'\n'" > /dev/null 2>&1
    su - "$TARGET_USER" -c "sed -i 's/@debian$/@$HOSTNAME/' /home/"$TARGET_USER"/.ssh/id_rsa.pub"
    chmod 700 /home/"$TARGET_USER"/.ssh

    # Create the user's authorized_keys file and adjust permissions
    su - "$TARGET_USER" -c "echo | touch /home/"$TARGET_USER"/.ssh/authorized_keys"
    chmod 600 /home/"$TARGET_USER"/.ssh/authorized_keys
}

spawn() {
    printf "\e[32m*\e[0m CONFIGURING SPAWN SERVICE\n"

    # Copy the necessary files to the service directory
    cp -r spawn /etc/ && chmod 700 -R /etc/spawn/*.sh
    ln -s /etc/spawn/CT/spawn.sh /home/"$TARGET_USER"/.spawn
    ln -s /etc/spawn/CT/spawn.sh /root/.spawn && chown sysop:sysop /root/.spawn
}

grub() {
    printf "\e[32m*\e[0m SETTING UP GRUB\n"

    # Remove the current GRUB configuration file (if it exists)
    rm -f /etc/default/grub

    # Create a new GRUB configuration file with custom parameters
    printf 'GRUB_DEFAULT=0
GRUB_TIMEOUT=0
GRUB_DISTRIBUTOR=`lsb_release -i -s 2> /dev/null || echo Debian`
GRUB_CMDLINE_LINUX_DEFAULT=""
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

    if [[ -z "$TARGET_USER" ]]; then
        printf "\e[33m* WARNING: No UID 1000 user found, skipping cleanup\e[0m\n"
        return  # Exit early if no user
    fi

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

# End all processes for user %s
pkill -u %s

# Remove the user %s and its home directory
userdel -r %s

# Remove the WS folder from the /root directory
rm -rf /root/Spiral-SRV-main

# Remove the init.d script after it is executed
rm -f /etc/init.d/later' "$TARGET_USER" "$TARGET_USER" "$TARGET_USER" "$TARGET_USER" > /etc/init.d/later && chmod +x /etc/init.d/later

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

    printf "\e[32m*\e[0m INSTALLATION COMPLETED SUCCESSFULLY!\n"

    read -p "DO YOU WANT TO RESTART? (Y/N): " response
    response=${response^^}
    if [[ "$response" == "Y" ]]; then
        printf "\e[32m*\e[0m RESTARTING...\n"
        systemctl reboot
    elif [[ "$response" == "N" ]]; then
        printf "\e[32m*\e[0m WILL NOT BE RESTARTED.\n"
    else
        printf "\e[31m*\e[0m ERROR: PLEASE ANSWER WITH 'Y' FOR YES OR 'N' FOR NO.\n"
    fi
}

main() {
    update
    interface
    hostname
    target_user
    passwords
    packages
    directories
    trigger
    network
    firewall
    mount
    hypervisor
    ssh
    spawn
    grub
    later
    finish
}

# Execute main function
main