de() {
    printf "\e[32m*\e[0m SETTING UP VNC SERVER\n"

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
vncserver -kill :1' > /home/$TARGET_USER/.services/novnc.sh && chmod +x /home/$TARGET_USER/.services/novnc.sh"
}

apt-get install -y tigervnc-standalone-server tigervnc-common xvfb x11-apps firefox-esr openbox x11-xserver-utils
su - sysop
touch ~/.Xresources
vncserver :1 *execute apenas para definir senha de acesso
vncserver -kill :1 *mata a instância em execução
vim ~/.vnc/xstartup
{
#!/bin/bash
# Inicia Xvfb com suporte a XRandr
Xvfb :1 -screen 0 1920x1080x24 +extension RANDR &
export DISPLAY=:1
# Carrega configurações XRandr (opcional)
xrdb $HOME/.Xresources
# Inicia o gerenciador de janelas openbox
openbox &
# Inicia o Firefox
firefox-esr &
# Mantém o script ativo
sleep infinity
}
chmod +x ~/.vnc/xstartup
vim ~/.services/vnc.sh
{
#!/bin/bash
# Tenta obter o IP público ou da interface de rede
IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || ip addr show | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | grep -v '127.0.0.1' | head -n 1)
if [ -z "$IP" ]; then
    IP="10.0.12.254"  # IP do seu servidor
fi
echo "Conecte-se com: http://$IP:6080/vnc.html?host=$IP&port=6080&resize=remote"
# Mata qualquer instância VNC existente e lock files
vncserver -kill :1 2>/dev/null || true
sudo rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 2>/dev/null || true
# Inicia o VNC com suporte a redimensionamento
vncserver :1 -SecurityTypes VncAuth -AcceptSetDesktopSize
# Inicia o noVNC sem SSL, suprimindo avisos
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5901 --listen 0.0.0.0:6080 --web /usr/share/novnc 2>/dev/null
# Mata o VNC ao finalizar
vncserver -kill :1
}
chmod u+x ~/.services/vnc.sh
