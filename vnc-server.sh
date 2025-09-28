#!/bin/bash

vnc_server() {
    printf "\e[32m*\e[0m SETTING UP DESKTOP ENVIRONMENT AND VNC SERVER\n"

    # Default user
    TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

    # Installing requirements
    apt-get -y install --no-install-recommends xorg openbox tigervnc-standalone-server tigervnc-common tigervnc-tools novnc

    # Building the environment
    su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/.config/{tigervnc,openbox}"

    su - "$TARGET_USER" -c "printf '# Programs that will run after Openbox has started
# Always on
xset -dpms &
xset s off &
# Application
# firefox-esr & *Example' > /home/$TARGET_USER/.config/openbox/autostart && chmod +x /home/$TARGET_USER/.config/openbox/autostart"

    su - "$TARGET_USER" -c "printf '#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec /bin/sh /etc/X11/xinit/xinitrc' > /home/$TARGET_USER/.config/tigervnc/xstartup && chmod +x /home/$TARGET_USER/.config/tigervnc/xstartup"

    printf '[Unit]
Description=Start VNC Server and noVNC Proxy
After=network.target

[Service]
Type=simple
User=%s
Group=%s
ExecStartPre=/usr/bin/vncserver -AcceptSetDesktopSize :1
ExecStart=/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901
ExecStop=/usr/bin/vncserver -kill :1
Environment=DISPLAY=:1

[Install]
WantedBy=multi-user.target' "$TARGET_USER" "$TARGET_USER" > /etc/systemd/system/novnc.service && systemctl daemon-reload --quiet && systemctl enable novnc --quiet

    printf "\e[32m*\e[0m ENTER THE PASSWORD TO ACCESS THE REMOTE ENVIRONMENT\n"
    vncserver :1 && vncserver -kill :1 && systemctl restart novnc --quiet
}

main() {
    vnc_server
}

# Execute main function
main