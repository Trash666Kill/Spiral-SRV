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
        NETWORK="nfs-common net-tools"
        apt-get -y install $NETWORK > /dev/null 2>&1
    }

    scripting() {
        # Install scripting and automation support packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: SCRIPTING AND AUTOMATION SUPPORT\n"
        SCRIPTING="sshpass python3-apt"
        apt-get -y install $SCRIPTING > /dev/null 2>&1
    }

    monitoring() {
        # Install system monitoring and diagnostics packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: SYSTEM MONITORING AND DIAGNOSTICS\n"
        MONITORING="screen"
        apt-get -y install $MONITORING > /dev/null 2>&1
    }

    extra_utils() {
        # Install additional utilities packages
        printf "\e[32m*\e[0m INSTALLING PACKAGE CATEGORY: ADDITIONAL UTILITIES\n"
        EXTRA_UTILS="uuid-runtime pwgen"
        apt-get -y install $EXTRA_UTILS > /dev/null 2>&1
    }

    # Call
    text_editor
    network_tools
    scripting
    monitoring
    extra_utils
}

directories() {
    printf "\e[32m*\e[0m CREATING DIRECTORIES\n"

    # Creates directories for temporary services and data
    mkdir -p /mnt/{Temp,Services} && chown "$TARGET_USER":"$TARGET_USER" -R /mnt/*
    mkdir -p /root/{Temp,.services/scheduled,.crypt} && chmod 600 /root/.crypt

    # Creates specific directories for the target user
    su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/{Temp,.services/scheduled,.crypt}"
}

later() {
    printf "\e[32m*\e[0m PERFORMING SUBSEQUENT PROCEDURES\n"

    # Instala o systemd-resolved
    apt -y install systemd-resolved > /dev/null 2>&1

    # Desabilita o serviço systemd-networkd
    systemctl disable --now systemd-networkd --quiet

    # Desabilita o socket do systemd-networkd
    systemctl disable --now systemd-networkd.socket --quiet

    # Desabilita o serviço systemd-resolved
    systemctl disable --now systemd-resolved --quiet
}

finish() {
    # Remove pacotes desnecessários
    apt -y autoremove > /dev/null 2>&1

    # Remove o script que está sendo executado (o próprio arquivo do script)
    rm -- "$0"
}

main() {
    connectiontest
    target_user
    packages
    directories
    later
    finish
}

# Execute main function
main