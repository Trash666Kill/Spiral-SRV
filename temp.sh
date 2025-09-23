#!/bin/bash

setup_vnc() {
    printf "\e[32m*\e[0m SETTING UP VNC SERVER\n"

    # Identify the user with UID 1001
    TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")
    if [ -z "$TARGET_USER" ]; then
        echo "Error: No user with UID 1001 found."
        exit 1
    fi

    # Check if the home directory exists
    if [ ! -d "/home/$TARGET_USER" ]; then
        echo "Error: Home directory /home/$TARGET_USER does not exist."
        exit 1
    fi

    # Install required packages
    apt-get install -y novnc tigervnc-standalone-server tigervnc-common xvfb x11-apps openbox x11-xserver-utils pwgen

    # Create configuration directories and files
    su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/.config/tigervnc"
    su - "$TARGET_USER" -c "touch /home/$TARGET_USER/.Xresources"

    # Generate a secure VNC password
    PASSWORD_TARGET=$(pwgen -s 18 1)
    su - "$TARGET_USER" -c "echo -n \"$PASSWORD_TARGET\" | vncpasswd -f > /home/$TARGET_USER/.vnc/passwd && chmod 600 /home/$TARGET_USER/.config/tigervnc/passwd"
    echo -e "\033[32m*\033[0m GENERATED PASSWORD FOR \033[32m$TARGET_USER\033[0m USER: \033[32m\"$PASSWORD_TARGET\"\033[0m"

    # Create the VNC xstartup script
    su - "$TARGET_USER" -c "printf '#!/bin/bash
Xvfb :1 -screen 0 1920x1080x24 +extension RANDR &
export DISPLAY=:1
xrdb \$HOME/.Xresources
openbox &
#firefox-esr &
' > /home/$TARGET_USER/.config/tigervnc/xstartup && chmod +x /home/$TARGET_USER/.config/tigervnc/xstartup"

    # Create the noVNC script
    su - "$TARGET_USER" -c "printf '#!/bin/bash
vncserver -kill :1 2>/dev/null || true
vncserver :1 -SecurityTypes VncAuth -AcceptSetDesktopSize
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901 --listen 0.0.0.0:6080 --web /usr/share/novnc >> /home/$TARGET_USER/.services/novnc.log 2>&1
' > /home/$TARGET_USER/.services/novnc.sh && chmod u+x /home/$TARGET_USER/.services/novnc.sh"

    # Create the systemd service file
    printf '[Unit]
Description=VNC Server and noVNC Proxy
After=network.target

[Service]
ExecStart=/bin/bash /home/%s/.services/novnc.sh
Restart=always
User=%s

[Install]
WantedBy=multi-user.target' "$TARGET_USER" "$TARGET_USER" > /etc/systemd/system/novnc.service

    # Reload and enable the systemd service
    systemctl daemon-reload --quiet && systemctl enable novnc --quiet && systemctl start novnc --quiet
}

main() {
    setup_vnc
}

# Execute main function
main