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
    HOSTNAME="srv$(shuf -i 100000-999999 -n 1)"

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
    chmod 700 /root/.services/firewall/*.sh && chattr +i /root/.services/firewall/{a.sh,b.sh}
}

mount() {
    printf "\e[32m*\e[0m SETTING MOUNT POINTS\n"

    # Adding Mount Configuration File
    cp systemd/scripts/mount.sh /root/.services && chmod 700 /root/.services/mount.sh
}

ssh() {
printf "\e[32m*\e[0m SETTING UP SSH\n"

# Instala os pacotes necessários para o SSH
apt -y install openssh-server sshfs autossh > /dev/null 2>&1

# Remove o arquivo de configuração do SSH antigo
rm /etc/ssh/sshd_config

# Cria um novo arquivo de configuração para o SSH com as configurações desejadas
printf 'Include /etc/ssh/sshd_config.d/*.conf

Port 22
AllowTcpForwarding no
GatewayPorts no

PubkeyAuthentication yes
PermitRootLogin no

ChallengeResponseAuthentication no

UsePAM yes

X11Forwarding yes
PrintMotd no
PrintLastLog no

AcceptEnv LANG LC_*

Subsystem       sftp    /usr/lib/openssh/sftp-server' > /etc/ssh/sshd_config; chmod 644 /etc/ssh/sshd_config

# Remove o arquivo motd e cria um novo arquivo vazio
rm /etc/motd; touch /etc/motd

# Cria diretórios e arquivos necessários para a configuração SSH do usuário alvo
su - "$TARGET_USER" -c "mkdir /home/$TARGET_USER/.ssh"
chmod 700 /home/"$TARGET_USER"/.ssh

# Cria o arquivo authorized_keys e define permissões
su - "$TARGET_USER" -c "echo | touch /home/$TARGET_USER/.ssh/authorized_keys"
chmod 600 /home/"$TARGET_USER"/.ssh/authorized_keys

# Gera uma chave SSH para o usuário
su - "$TARGET_USER" -c "echo | ssh-keygen -t rsa -b 4096 -N '' <<<$'\n'" > /dev/null 2>&1

# Define permissões adequadas para o diretório .ssh do root e cria o arquivo authorized_keys
chmod 600 /root/.ssh
touch /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys

# Gera uma chave SSH para o root
ssh-keygen -t rsa -b 4096 -N '' <<<$'\n' > /dev/null 2>&1
}

de() {
printf "\e[32m*\e[0m SETTING UP DESKTOP ENVIRONMENT AND VNC SERVER\n"

TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

# Instala os pacotes necessários para o ambiente desktop
apt -y install xorg dbus-x11 lightdm openbox obconf hsetroot terminator lxpanel \
lxtask lxsession-logout lxappearance numlockx progress arc-theme ffmpegthumbnailer \
gpicview galculator l3afpad compton pcmanfm firefox-esr engrampa \
tigervnc-standalone-server tigervnc-common novnc > /dev/null 2>&1

# Configura o LightDM com o arquivo de greeter personalizado
rm /etc/lightdm/lightdm-gtk-greeter.conf
printf '[greeter]
background = #2e3436
default-user-image = #avatar-default-symbolic
indicators = ~host;~spacer;~spacer;~power' > /etc/lightdm/lightdm-gtk-greeter.conf

# Cria o grupo e atribui o usuário alvo necessário
groupadd -r autologin
gpasswd -a $TARGET_USER autologin > /dev/null 2>&1

# Configura a inicialização automatica no modo gráfico
rm /etc/lightdm/lightdm.conf
printf '[Seat:*]
autologin-user=%s
autologin-guest=false
autologin-user-timeout=0' "$TARGET_USER" > /etc/lightdm/lightdm.conf

# Instala e configura background, temas e ícones para o ambiente desktop
tar -xvf de/01-Qogir.tar.xz -C /usr/share/icons > /dev/null 2>&1
tar -xvf de/Arc-Dark.tar.xz -C /usr/share/themes > /dev/null 2>&1
cp de/debian-swirl.png /usr/share/icons/default
su - "$TARGET_USER" -c "rm -r /home/$TARGET_USER/.config" > /dev/null 2>&1
cp -r de/config /home/$TARGET_USER/.config; chown "$TARGET_USER":"$TARGET_USER" -R /home/$TARGET_USER/.config
cp de/gtkrc-2.0 /home/$TARGET_USER/.gtkrc-2.0; chown "$TARGET_USER":"$TARGET_USER" /home/$TARGET_USER/.gtkrc-2.0

# Criação do diretório de configuração do VNC para o usuário alvo
su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/.vnc"

# Criação do script de inicialização do VNC
su - "$TARGET_USER" -c "printf '#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec /bin/sh /etc/X11/xinit/xinitrc' > /home/$TARGET_USER/.vnc/xstartup; chmod +x /home/$TARGET_USER/.vnc/xstartup"

# Configuração da senha para o VNC
su - "$TARGET_USER" -c "echo -n "$PASSWORD_TARGET" | vncpasswd -f > /home/$TARGET_USER/.vnc/passwd; chmod 600 /home/$TARGET_USER/.vnc/passwd"

# Criação do serviço systemd para iniciar o VNC Server e o proxy noVNC
printf '[Unit]
Description=Start VNC Server and noVNC Proxy
After=network.target

[Service]
Type=simple
User=%s
Group=%s
ExecStartPre=/usr/bin/vncserver -verbose -geometry 1024x768 :1
ExecStart=/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901
ExecStop=/usr/bin/vncserver -kill :1
Environment=DISPLAY=:1

[Install]
WantedBy=multi-user.target' "$TARGET_USER" "$TARGET_USER" > /etc/systemd/system/novnc.service

# Recarrega os arquivos de configuração do systemd para registrar o novo serviço e ativa a inicialização automática
systemctl daemon-reload --quiet; systemctl enable novnc --quiet

# Define a inicialização padrão para o modo CLI
systemctl set-default multi-user.target --quiet
}

grub() {
printf "\e[32m*\e[0m SETTING UP GRUB\n"

# Remove o arquivo de configuração atual do GRUB (se existir)
rm -f /etc/default/grub

# Cria um novo arquivo de configuração do GRUB com parâmetros personalizados
printf 'GRUB_DEFAULT=0
GRUB_TIMEOUT=0
GRUB_DISTRIBUTOR=`lsb_release -i -s 2> /dev/null || echo Debian`
GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"
GRUB_CMDLINE_LINUX=""' > /etc/default/grub; chmod 644 /etc/default/grub

# Atualiza a configuração do GRUB
update-grub

# Mensagem de conclusão
if [ $? -eq 0 ]; then
    printf "\e[32m*\e[0m GRUB CONFIGURATION UPDATED SUCCESSFULLY\n"
else
    printf "\e[31m*\e[0m ERROR: FAILED TO UPDATE GRUB CONFIGURATION\n"
fi
}

later() {
printf "\e[32m*\e[0m SCHEDULING SUBSEQUENT CONSTRUCTION PROCEDURES AFTER RESTART\n"

# Obtém o nome do usuário com UID 1000, geralmente o primeiro usuário criado
TARGET_USER=$(grep 1000 /etc/passwd | cut -f 1 -d ":")

# Cria o script de inicialização que será executado após o reinício
printf '#!/bin/bash
### BEGIN INIT INFO
# Provides:          later
# Required-Start:    $all
# Required-Stop:     
# Default-Start:     2 3 4 5
# Default-Stop:      
# Short-Description: Procedures subsequent to instance construction only possible after reboot
### END INIT INFO

# Finaliza todos os processos do usuário TARGET_USER
pkill -u %s

# Remove o usuário TARGET_USER e seu diretório home
userdel -r %s

# Remove a pasta VM do diretório /root
rm -rf /root/VM

# Remove o script init.d depois que ele for executado
rm -f /etc/init.d/later' "$TARGET_USER" "$TARGET_USER" > /etc/init.d/later; chmod +x /etc/init.d/later

# Adiciona o script aos serviços que serão iniciados no boot
update-rc.d later defaults
}

finish() {
# Remove pacotes não utilizados e dependências não necessárias
apt -y autoremove > /dev/null 2>&1

# Remove arquivo de configuração de rede padrão para evitar conflitos
rm /etc/network/interfaces

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

# Main function to orchestrate the setup
main() {
    repositore
    global
    hostname
    target_user
    passwords
    packages
    directories
    trigger
    firewall
    mount
    ssh
    de
    grub
    network
    later
    finish
}

# Execute main function
main