#!/bin/bash

# Disable bash history
unset HISTFILE

NIC0=ens2

network() {
    # Restart the systemd-resolved service to ensure DNS resolution is active
    systemctl restart systemd-resolved

    # Stores the assigned IP address
    IP_ADDRESS=$(ip -4 addr show "$NIC0" | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
}

passwords() {
    # Stores the username 'sysop' in the variable TARGET_USER
    TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

    # Generates two secure passwords with special characters and 12 characters
    PASSWORD_TARGET=$(pwgen -s 18 1)

    # Checks if the user TARGET_USER exists in the system
    if ! id "$TARGET_USER" &>/dev/null; then
        printf "\e[31m* ERROR:\e[0m USER '$TARGET_USER' DOES NOT EXIST. TERMINATING SCRIPT.\n"
        exit 1
    fi

    # Changes the password of the user TARGET_USER
    echo "$TARGET_USER:$PASSWORD_TARGET" | chpasswd
    if [ $? -ne 0 ]; then
        printf "\e[31m* ERROR:\e[0m FAILED TO CHANGE PASSWORD FOR USER '$TARGET_USER'.\n"
        exit 1
    fi
}

baseboard() {
    echo -e "\033[32m*\033[0m GENERATED PASSWORD FOR \033[32mSysOp\033[0m USER: \033[32m\"$PASSWORD_TARGET\"\033[0m"
    printf "\e[32m*\e[0m IP ADDRESS: \e[32m%s\e[0m\n" "$IP_ADDRESS"
}

finish() {
    # Remove packages that are no longer needed
    apt-get -y autoremove > /dev/null 2>&1

    # Sealing ssh connection
    sed -i '/^PermitRootLogin/d' /etc/ssh/sshd_config
    sed -i '/^PermitEmptyPasswords/d' /etc/ssh/sshd_config
    echo 'PermitRootLogin prohibit-password' >> /etc/ssh/sshd_config
    echo 'PermitEmptyPasswords no' >> /etc/ssh/sshd_config

    # Removing the Pre-Build file Service
    rm /etc/systemd/system/prebuild.service
    # Disabling and Removing the Pre-Build Service
    systemctl disable prebuild --quiet

    # Remove the current script (the file that is running)
    rm -- "$0"
}

main() {
    network
    passwords
    baseboard
    finish
}

# Execute main function
main