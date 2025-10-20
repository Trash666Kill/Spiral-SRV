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