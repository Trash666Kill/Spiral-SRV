#!/bin/bash

vnc_server() {
    printf "\e[32m*\e[0m SETTING UP DESKTOP ENVIRONMENT AND VNC SERVER\n"

    # Default user
    TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

    # Installing requirements
    apt-get install --no-install-recommends xorg openbox tigervnc-standalone-server tigervnc-common tigervnc-tools novnc

    # Building the environment
<<<<<<< HEAD
    su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/.config/tigervnc"
    
    su - "$TARGET_USER" -c "printf '#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec /bin/sh /etc/X11/xinit/xinitrc' > /home/$TARGET_USER/.config/tigervnc/xstartup && chmod +x /home/$TARGET_USER/.config/tigervnc/xstartup"
=======

>>>>>>> ecc77b567bbd30743d3fbb8b7f6dc420857fa6db

    su - "$TARGET_USER" -c "printf '#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec /bin/sh /etc/X11/xinit/xinitrc' > /home/$TARGET_USER/.config/tigervnc/xstartup && chmod +x /home/$TARGET_USER/.config/tigervnc/xstartup"

    su - "$TARGET_USER" -c "printf '#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec /bin/sh /etc/X11/xinit/xinitrc' > /home/$TARGET_USER/.config/openbox/autostart.sh && chmod +x /home/$TARGET_USER/.config/openbox/autostart.sh"

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
}





