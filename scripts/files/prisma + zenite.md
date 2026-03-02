# 🔄 Prisma + Zenite — Sincronização Local → OneDrive

Script de integração entre o **Prisma** e o **Zenite** para sincronizar automaticamente backups fatiados (`.part_*`) com o OneDrive após a conclusão do split.

---

## 📋 Visão Geral

Após o **Prisma** concluir o processo de split de um backup, o hook `after_split` dispara o script `post_split.sh`, que:

1. Gera um arquivo `reconstruir.txt` com o comando necessário para reconstruir o backup localmente.
2. Inicia em background (via `screen`) a sincronização do diretório fatiado com o OneDrive usando o **Zenite**.

```
Prisma (split) ──► post_split.sh ──► Zenite (OneDrive sync)
                          │
                          └──► reconstruir.txt (instruções de rebuild)
```

---

## 🗂️ Estrutura

```
/root/.services/scheduled/
└── post_split.sh          # Script de pós-split
```

| Caminho local | Destino OneDrive |
|---|---|
| `/mnt/Local/Container/A/Backup/172_30_100_22/hsugisawa/Full/splitted` | `Backup/HS-STG-02/Full/hsugisawa/splitted` |

---

## ⚙️ Instalação

### 1. Criar o script `post_split.sh`

```bash
cat > /root/.services/scheduled/post_split.sh << 'EOF'
#!/bin/bash
echo 'cd /mnt/Local/Container/A/Backup/172_30_100_22/hsugisawa/Full/splitted && screen -d -m -S rebuilding_backup bash -c "cat *.part_* | pv -L 50M | ionice -c 3 nice -n 19 tar -I zstd -xvf - > happy.log 2>&1"' \
    > /mnt/Local/Container/A/Backup/172_30_100_22/hsugisawa/Full/splitted/reconstruir.txt

screen -d -m -S zenite_hsugisawa /usr/bin/python3 /root/.services/scheduled/zenite.py \
    --sync /mnt/Local/Container/A/Backup/172_30_100_22/hsugisawa/Full/splitted \
    "Backup/HS-STG-02/Full/hsugisawa/splitted" \
    --mirror --speed 8mb --yes
EOF

chmod 700 /root/.services/scheduled/post_split.sh
```

### 2. Configurar o hook no Prisma

No arquivo de configuração do **Prisma**, adicione o campo `after_split` apontando para o script:

```json
"after_split": "/root/.services/scheduled/post_split.sh"
```

---

## 🔁 Reconstruindo o Backup

Após o download das partes no destino, execute:

```bash
cat *.part_* | pv -L 50M | ionice -c 3 nice -n 19 tar -I zstd -xvf - > happy.log 2>&1
```

> Este comando também é salvo automaticamente em `reconstruir.txt` dentro do diretório de partes.

---

## 📦 Dependências

| Ferramenta | Função |
|---|---|
| [`Prisma`](https://github.com/) | Geração e split de backups |
| [`Zenite`](https://github.com/) | Sincronização com OneDrive |
| `screen` | Execução em background desanexada |
| `pv` | Monitoramento de progresso no pipe |
| `zstd` | Compressão/descompressão do tar |
| `ionice` / `nice` | Prioridade de I/O e CPU reduzida |
