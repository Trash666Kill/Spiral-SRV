ssh -p 22 root@10.0.12.249 "mkdir /root/builder"
scp -P 22 /etc/spawn/VM/builder/basevm.sh root@10.0.12.249:/root/builder
scp -P 22 -r /etc/spawn/VM/systemd root@10.0.12.249:/root/builder
ssh -p 22 root@10.0.12.249 "cd /root/builder && chmod +x basevm.sh && ./basevm.sh"