de() {
    printf "\e[32m*\e[0m SETTING UP VNC SERVER\n"

    TARGET_USER=$(grep 1001 /etc/passwd | cut -f 1 -d ":")

    # Installs the packages
    apt-get install -y tigervnc-standalone-server tigervnc-common xvfb x11-apps firefox-esr openbox x11-xserver-utils

    # Criação do diretório de configuração do VNC para o usuário alvo
    su - "$TARGET_USER" -c "mkdir -p /home/$TARGET_USER/.vnc"
    su - "$TARGET_USER" -c "touch /home/$TARGET_USER/.Xresources"

    # Criação do script de inicialização do VNC
    su - "$TARGET_USER" -c "printf '#!/bin/bash
Xvfb :1 -screen 0 1920x1080x24 +extension RANDR &
export DISPLAY=:1
xrdb $HOME/.Xresources
openbox &
#firefox-esr &
sleep infinity' > /home/$TARGET_USER/.vnc/xstartup && chmod +x /home/$TARGET_USER/.vnc/xstartup"

    #
    su - "$TARGET_USER" -c "printf '#!/bin/bash
vncserver -kill :1 2>/dev/null || true
vncserver :1 -SecurityTypes VncAuth -AcceptSetDesktopSize
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901 --listen 0.0.0.0:6080 --web /usr/share/novnc 2>/dev/null
vncserver -kill :1' > /home/$TARGET_USER/.services/novnc.sh && chmod u+x /home/$TARGET_USER/.services/novnc.sh"

    # Criação do serviço systemd para iniciar o VNC Server e o proxy noVNC
printf '[Unit]
Description=VNC Server and noVNC Proxy
After=network.target

[Service]
ExecStart=/bin/bash /home/%s/.services/novnc.sh
Restart=always
User=%s
Environment=DISPLAY=:1

[Install]
WantedBy=multi-user.target' "$TARGET_USER" "$TARGET_USER" > /etc/systemd/system/novnc.service

# Recarrega os arquivos de configuração do systemd para registrar o novo serviço e ativa a inicialização automática
systemctl daemon-reload --quiet && systemctl enable novnc --quiet
}
