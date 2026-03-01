#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import math
import shutil
import stat
import signal
import logging
import subprocess
import argparse
import tempfile
import textwrap
from datetime import datetime

# --- Texto de Ajuda Detalhado ---
HELP_TEXT = textwrap.dedent("""
    ════════════════════════════════════════════════════════════════════
    \033[1mfilesbkp.py  —  Gerenciador de Backup Corporativo (CIFS → Local)\033[0m
    ════════════════════════════════════════════════════════════════════

    \033[1mVISÃO GERAL\033[0m
    ─────────────────────────────────────────────────────────────────
    Faz backup de compartilhamentos de rede Windows/CIFS para disco
    local usando a seguinte estratégia em três camadas:

      1. \033[1mIncremental contínuo\033[0m  — rsync mantém um espelho atualizado dos
         arquivos remotos na pasta Incremental. Arquivos modificados ou
         excluídos são movidos automaticamente para a pasta Differential
         (mecanismo --backup / --backup-dir do rsync), preservando
         histórico de alterações sem duplicar dados inalterados.

      2. \033[1mDifferential\033[0m          — cada execução do rsync deposita aqui os
         arquivos que foram substituídos ou apagados na origem, datados
         pelo rsync. Funcionam como ponto de recuperação de versões
         anteriores.

      3. \033[1mFull (snapshot + .tar.zst)\033[0m — periodicamente (conforme a política
         de retenção), o espelho Incremental é copiado via Reflink
         (Copy-on-Write, instantâneo em filesystems como BTRFS/XFS) e
         depois comprimido com tar + zstd, gerando um arquivo portátil
         Full_AAAA-MM-DD_HH-MM-SS.tar.zst. Se já existir um Full
         dentro do prazo de retenção, a etapa é pulada sem erro.

    \033[1mESTRUTURA DE DIRETÓRIOS GERADA\033[0m
    ─────────────────────────────────────────────────────────────────
    A estrutura é derivada automaticamente de "remote_share".
    Pontos (.) são substituídos por (_).

    Exemplo  →  remote_share = "//192.168.0.100/Dados/Share"
                backup_root  = "/mnt/Backup"

    /mnt/Backup/
    └── 192_168_0_100/
        └── Dados/
            └── Share/
                ├── Incremental/   ← espelho rsync (fonte para recuperação)
                ├── Differential/  ← arquivos alterados / deletados
                └── Full/
                    ├── Full/                        ← snapshot (reflink)
                    ├── Full_2025-07-01_02-00-00.tar.zst
                    └── splitted/                    ← fragmentos (se split habilitado)
                        ├── Full_2025-07-01_02-00-00.tar.zst.part_001
                        └── Full_2025-07-01_02-00-00.tar.zst.part_002

    \033[1mFLUXO DE EXECUÇÃO\033[0m
    ─────────────────────────────────────────────────────────────────
    check_pre_flight()       → verifica dependências, espaço em disco
                               e permissões do arquivo JSON
    mount_share()            → monta o compartilhamento CIFS
    run_rsync()              → sincroniza origem → Incremental
                               (arquivos substituídos vão para Differential)
    cleanup_differential()   → remove arquivos Differential expirados
    run_full_backup()        → cria Full .tar.zst se necessário
    cleanup_logs()           → mantém apenas os N logs mais recentes
    cleanup()                → desmonta o compartilhamento

    \033[1mARGUMENTOS DA LINHA DE COMANDO\033[0m
    ─────────────────────────────────────────────────────────────────
    \033[1mfilesbkp.py [config.json] [--init] [--debug]\033[0m

      config.json   Caminho para o arquivo JSON de configuração do job.
                    Obrigatório para executar um backup. Se o arquivo não
                    existir, um modelo padrão é criado automaticamente com
                    chmod 600 e o script encerra (edite e re-execute).

      --init        Cria um arquivo JSON modelo (config_modelo.json) com
                    todos os campos preenchidos com valores de exemplo.
                    Combine com um nome personalizado:
                      $ python3 filesbkp.py clientes/novo.json --init
                    → cria clientes/novo.json com chmod 600.
                    Aborta se o arquivo já existir (nunca sobrescreve).

      --debug       Imprime no log cada comando externo executado
                    (rsync, mount, tar, zstd, ionice, nice, pv, split…)
                    antes de rodá-lo. Útil para diagnosticar falhas.
                    Credenciais de mount são automaticamente ocultadas
                    (username=***, password=***) mesmo em modo debug.

    \033[1mEXEMPLOS RÁPIDOS\033[0m
    ─────────────────────────────────────────────────────────────────
      # Criar modelo de configuração
      $ python3 filesbkp.py --init
      $ python3 filesbkp.py clientes/empresa_xyz.json --init

      # Executar backup (modo normal)
      $ python3 filesbkp.py clientes/empresa_xyz.json

      # Executar com saída de diagnóstico detalhada
      $ python3 filesbkp.py clientes/empresa_xyz.json --debug

      # Agendar via cron (diariamente às 02:00)
      0 2 * * * /usr/bin/python3 /opt/scripts/filesbkp.py /etc/backup/empresa_xyz.json

    ════════════════════════════════════════════════════════════════════
    \033[1mREFERÊNCIA COMPLETA DO ARQUIVO JSON DE CONFIGURAÇÃO\033[0m
    ════════════════════════════════════════════════════════════════════

    \033[1m┌─ SEÇÃO: "credentials"\033[0m
    │  Credenciais usadas pelo mount.cifs para autenticar no servidor.
    │  O arquivo JSON deve ter permissão 600 (o script avisa se não tiver).
    │
    │  "username"  : string  — login da conta com acesso ao compartilhamento.
    │                          Pode ser conta local ou de domínio.
    │                          Exemplo: "username": "svc_backup"
    │
    │  "password"  : string  — senha da conta.
    │                          Exemplo: "password": "S3nh@Fort3!"
    │
    │  "domain"    : string  — domínio Active Directory (opcional).
    │                          Se omitido ou vazio, o mount usa autenticação
    │                          de workgroup local.
    │                          Exemplo: "domain": "CORP"
    │                          Sem domínio: "domain": ""
    └──────────────────────────────────────────────────────────────────

    \033[1m┌─ SEÇÃO: "paths"\033[0m
    │
    │  "remote_share"  : string — Caminho UNC do compartilhamento CIFS a ser
    │                             montado e copiado.
    │                             Formato: "//IP_ou_HOSTNAME/Share/Subpasta"
    │                             O caminho (sem as barras iniciais) é usado
    │                             para derivar a estrutura de pastas local.
    │                             Pontos são trocados por underscores.
    │                             Exemplo: "//192.168.10.5/Vendas"
    │                             → pasta local: 192_168_10_5/Vendas/
    │
    │  "mount_point"   : string — Diretório local onde o compartilhamento
    │                             será montado temporariamente durante o job.
    │                             Deve existir ou ser criável. O script NÃO
    │                             cria este diretório; crie manualmente.
    │                             Exemplo: "/mnt/Remote/Vendas"
    │
    │  "backup_root"   : string — Raiz onde toda a estrutura de backup será
    │                             armazenada. O script cria as subpastas
    │                             necessárias automaticamente.
    │                             Exemplo: "/mnt/Backup"
    │                             → dados em: /mnt/Backup/192_168_10_5/Vendas/
    │
    │  "log_dir"       : string — Diretório onde os arquivos de log diários
    │                             serão gravados. O nome do log inclui o
    │                             identificador do job e o timestamp:
    │                             backup_<safe_name>_AAAA-MM-DD_HH-MM-SS.log
    │                             Exemplo: "/var/log/rsync"
    └──────────────────────────────────────────────────────────────────

    \033[1m┌─ SEÇÃO: "settings"\033[0m
    │
    │  "mount_options"       : string — Opções extras passadas ao mount.cifs
    │                                   via flag -o, ANTES das credenciais.
    │                                   As credenciais (username/password/domain)
    │                                   são adicionadas automaticamente.
    │                                   Valor recomendado: "ro" (somente leitura)
    │                                   para evitar alterações acidentais na
    │                                   origem durante o backup.
    │                                   Para leitura/escrita: "rw"
    │                                   Com versão SMB explícita: "ro,vers=2.1"
    │                                   Exemplo: "mount_options": "ro,vers=3.0"
    │
    │  "min_space_mb"        : inteiro — Espaço livre mínimo exigido no volume
    │                                   do backup_root antes de iniciar o job.
    │                                   Se o espaço livre for menor, o script
    │                                   aborta imediatamente com erro.
    │                                   Valor em megabytes.
    │                                   Exemplo: 1024  → exige pelo menos 1 GB livre
    │                                            51200 → exige pelo menos 50 GB livre
    │
    │  "bandwidth_limit_mb"  : número  — Limite de banda para o rsync,
    │                                   em MEGABYTES por segundo.
    │                                   Convertido internamente para KB/s e
    │                                   passado ao rsync via --bwlimit.
    │                                   Use para não saturar o link de rede.
    │                                   0 = sem limite.
    │                                   Exemplo: 10   → limita a 10 MB/s
    │                                            0.5  → limita a 512 KB/s
    │
    │  "transfer_rate_pv"    : string  — Taxa máxima de leitura aplicada pela
    │                                   ferramenta 'pv' no pipeline de compressão
    │                                   do Full (tar | pv | zstd).
    │                                   Controla a velocidade de leitura dos dados
    │                                   do disco durante a compressão, evitando
    │                                   que o processo consuma toda a I/O do servidor.
    │                                   Formato aceito pelo pv: número + unidade.
    │                                   Exemplos: "10m" → 10 MB/s
    │                                             "50m" → 50 MB/s
    │                                             "500k" → 500 KB/s
    │
    │  "ionice_class"        : inteiro — Classe de prioridade de I/O atribuída
    │                                   ao processo de compressão (tar + zstd)
    │                                   via ionice(1). Reduz o impacto do backup
    │                                   em outros processos que usam o disco.
    │                                   Valores possíveis:
    │                                     1 = Real-time  (alta prioridade, use
    │                                         com cautela — pode travar o sistema)
    │                                     2 = Best-effort (padrão do kernel)
    │                                     3 = Idle       (só usa I/O quando ninguém
    │                                         mais precisa — recomendado para backup)
    │                                   Exemplo: "ionice_class": 3  ← recomendado
    │                                   Equivale a executar:
    │                                     ionice -c 3 tar -cvf - ...
    │
    │  "nice_priority"       : inteiro — Prioridade de CPU (niceness) atribuída
    │                                   ao processo de compressão via nice(1).
    │                                   Valores de -20 (máxima prioridade de CPU)
    │                                   a 19 (mínima prioridade — "educado").
    │                                   Use 19 para que o backup não dispute
    │                                   CPU com aplicações em produção.
    │                                   Exemplo: "nice_priority": 19  ← recomendado
    │                                   Equivale a executar:
    │                                     nice -n 19 tar -cvf - ...
    │
    │  ATENÇÃO: ionice_class e nice_priority afetam SOMENTE a etapa de
    │  compressão do Full (tar | pv | zstd). O rsync roda sem ajuste de
    │  prioridade (use bandwidth_limit_mb para controlar seu impacto).
    │
    │  "rsync_user"          : string  — Usuário do sistema operacional sob o
    │                                   qual o rsync será executado (via su -c).
    │                                   Útil quando o usuário que roda o script
    │                                   é diferente do usuário com acesso ao
    │                                   diretório de destino.
    │                                   Se igual ao usuário atual, su não é usado.
    │                                   Exemplo: "rsync_user": "root"
    │                                            "rsync_user": "backup_svc"
    │
    │  "rsync_flags"         : lista   — Flags passadas diretamente ao rsync.
    │                                   Substitui o conjunto padrão inteiramente.
    │                                   Flags obrigatórias já adicionadas pelo
    │                                   script (não precisam estar aqui):
    │                                     --bwlimit, --backup, --backup-dir,
    │                                     --log-file, --exclude-from
    │                                   Flags padrão recomendadas:
    │                                     "-ahx"           → archive + human-readable
    │                                                        + não cruzar filesystems
    │                                     "--acls"         → preserva ACLs
    │                                     "--xattrs"       → preserva atributos estendidos
    │                                     "--numeric-ids"  → não mapeia UID/GID por nome
    │                                     "--chmod=ugo+r"  → garante leitura nos arquivos
    │                                     "--ignore-errors"→ não aborta em erros de leitura
    │                                     "--force"        → força substituição de dirs
    │                                     "--delete"       → remove no destino o que foi
    │                                                        excluído na origem
    │                                     "--info=del,name,stats2" → log detalhado
    │
    │  ┌─ SUBSEÇÃO: "retention_policy"\033[0m
    │  │
    │  │  "keep_logs_count"              : inteiro — Quantidade máxima de arquivos
    │  │                                             de log a manter para este job.
    │  │                                             Os logs são ordenados por data de
    │  │                                             modificação; os mais antigos além
    │  │                                             deste limite são excluídos.
    │  │                                             Exemplo: 31  → guarda os 31 logs
    │  │                                             mais recentes (≈ 1 mês diário)
    │  │
    │  │  "keep_full_backups_days"       : inteiro — Quantos dias um arquivo Full
    │  │                                             .tar.zst é considerado válido.
    │  │                                             Arquivos Full mais antigos que
    │  │                                             este valor são excluídos.
    │  │                                             Se não existir nenhum Full dentro
    │  │                                             deste prazo, um novo é gerado.
    │  │                                             Exemplo: 30 → mantém Fulls dos
    │  │                                             últimos 30 dias; gera novo Full
    │  │                                             se o mais recente tiver > 30 dias.
    │  │
    │  │  "keep_differential_files_days" : inteiro — Tempo máximo de retenção dos
    │  │                                             arquivos na pasta Differential.
    │  │                                             Arquivos com mtime maior que este
    │  │                                             valor (em dias) são apagados.
    │  │                                             Exemplo: 240 → mantém histórico
    │  │                                             de versões anteriores por 8 meses.
    │  │
    │  │  "cleanup_empty_dirs"           : bool    — Se true, subdiretórios vazios
    │  │                                             que ficaram em Differential após
    │  │                                             a limpeza de arquivos expirados
    │  │                                             são removidos automaticamente.
    │  │                                             Recomendado: true
    │  └──────────────────────────────────────────────────────────────

    │  ┌─ SUBSEÇÃO: "split"\033[0m
    │  │  Divide o arquivo Full .tar.zst em partes menores após a compressão.
    │  │  Útil para armazenamento em mídias com limite de tamanho de arquivo
    │  │  (FAT32: 4 GB, alguns backups em nuvem, fitas, etc.).
    │  │
    │  │  "enabled"                  : bool   — Ativa ou desativa o split.
    │  │                                        false → o .tar.zst não é fragmentado.
    │  │                                        true  → fragmenta imediatamente após
    │  │                                        a compressão bem-sucedida.
    │  │                                        Exemplo: "enabled": false
    │  │
    │  │  "chunk_size"               : string — Tamanho máximo de cada fragmento.
    │  │                                        Unidades aceitas (case-insensitive):
    │  │                                          "mb" → megabytes
    │  │                                          "gb" → gigabytes
    │  │                                          "tb" → terabytes
    │  │                                        Exemplos:
    │  │                                          "4gb"   → fragmentos de até 4 GB
    │  │                                          "500mb" → fragmentos de até 500 MB
    │  │                                          "1tb"   → fragmentos de até 1 TB
    │  │                                        Os fragmentos são nomeados:
    │  │                                          Full_<timestamp>.tar.zst.part_001
    │  │                                          Full_<timestamp>.tar.zst.part_002
    │  │                                          …
    │  │                                        O número de dígitos no sufixo é
    │  │                                        calculado automaticamente (mínimo 3).
    │  │                                        O split tem até 3 tentativas com
    │  │                                        validação de integridade (soma dos
    │  │                                        fragmentos deve igualar o original).
    │  │
    │  │  "keep_original_after_split" : bool   — Define se o .tar.zst original é
    │  │                                         mantido ou removido após a criação
    │  │                                         bem-sucedida dos fragmentos.
    │  │                                         true  → mantém o .tar.zst intacto
    │  │                                                  (ocupa mais espaço, mas
    │  │                                                  permite restaurar diretamente
    │  │                                                  sem concatenar fragmentos).
    │  │                                         false → remove o .tar.zst após o
    │  │                                                  split (economiza espaço).
    │  │                                         Exemplo: "keep_original_after_split": true
    │  └──────────────────────────────────────────────────────────────

    \033[1m┌─ SEÇÃO: "excludes"\033[0m
    │  Lista de padrões de arquivos/diretórios que devem ser ignorados pelo
    │  rsync. Usa a sintaxe de padrões do rsync (--exclude-from).
    │  Útil para evitar arquivos temporários, caches e lixo do Windows.
    │
    │  Exemplos de padrões comuns:
    │    "*.tmp"          → arquivos temporários de qualquer nome
    │    "Thumbs.db"      → cache de miniaturas do Windows Explorer
    │    "desktop.ini"    → arquivo de configuração de pasta do Windows
    │    "~$*"            → arquivos abertos/travados do Office
    │    "*.log"          → arquivos de log da aplicação remota
    │    ".Trash*"        → lixeira do sistema
    │    "pagefile.sys"   → memória virtual do Windows (enorme, inútil no backup)
    │    "hiberfil.sys"   → arquivo de hibernação do Windows
    │
    │  Exemplo completo:
    │    "excludes": ["*.tmp", "Thumbs.db", "desktop.ini", "~$*", "*.log"]
    └──────────────────────────────────────────────────────────────────

    \033[1m┌─ SEÇÃO: "hooks"\033[0m
    │  Comandos shell opcionais executados automaticamente ao término de
    │  etapas específicas do job. Cada campo aceita qualquer comando
    │  válido em bash (incluindo pipes, redirecionamentos e variáveis
    │  de ambiente). Campos ausentes ou com string vazia são ignorados.
    │
    │  Os hooks são disparados via "bash -c '<comando>'" de forma
    │  assíncrona (fire-and-forget): o script não aguarda conclusão,
    │  não verifica o código de retorno e não trata erros. O job
    │  prossegue normalmente independente do resultado do hook.
    │
    │  "after_rsync"  : string — Executado após o rsync (incremental)
    │                            concluir, com sucesso ou aviso (code 23).
    │                            Exemplo: "after_rsync": "touch ~/rsync_done.sh"
    │
    │  "after_full"   : string — Executado após run_full_backup() concluir,
    │                            independente de ter gerado um novo Full ou
    │                            pulado por já existir um válido.
    │                            Exemplo: "after_full": "touch ~/full_done.sh"
    │
    │  "after_split"  : string — Executado após o split concluir com sucesso.
    │                            Só disparado quando split.enabled = true e
    │                            o split foi realizado nesta execução.
    │                            Exemplo: "after_split": "touch ~/split_done.sh"
    │
    │  Exemplo completo:
    │    "hooks": {
    │        "after_rsync": "echo 'rsync ok' >> /var/log/hooks.log",
    │        "after_full":  "touch ~/full_done.sh",
    │        "after_split": ""
    │    }
    └──────────────────────────────────────────────────────────────────

    ════════════════════════════════════════════════════════════════════
    \033[1mEXEMPLO COMPLETO DE ARQUIVO JSON\033[0m
    ════════════════════════════════════════════════════════════════════

    {
        "description": "Backup do servidor de arquivos Vendas — sede SP",

        "credentials": {
            "username": "svc_backup",
            "password": "S3nh@Fort3!",
            "domain":   "CORP"
        },

        "paths": {
            "remote_share": "//192.168.10.5/Vendas",
            "mount_point":  "/mnt/Remote/Vendas",
            "backup_root":  "/mnt/Backup",
            "log_dir":      "/var/log/rsync"
        },

        "settings": {
            "mount_options":        "ro,vers=3.0",
            "min_space_mb":         51200,
            "bandwidth_limit_mb":   50,
            "transfer_rate_pv":     "50m",
            "ionice_class":         3,
            "nice_priority":        19,
            "rsync_user":           "root",
            "rsync_flags": [
                "-ahx", "--acls", "--xattrs", "--numeric-ids",
                "--chmod=ugo+r", "--ignore-errors", "--force", "--delete",
                "--info=del,name,stats2"
            ],
            "retention_policy": {
                "keep_logs_count":               31,
                "keep_full_backups_days":        30,
                "keep_differential_files_days":  240,
                "cleanup_empty_dirs":            true
            },
            "split": {
                "enabled":                    true,
                "chunk_size":                 "4gb",
                "keep_original_after_split":  false
            }
        },

        "excludes": ["*.tmp", "Thumbs.db", "desktop.ini", "~$*"],

        "hooks": {
            "after_rsync": "touch ~/rsync_done.sh",
            "after_full":  "touch ~/full_done.sh",
            "after_split": ""
        }
    }

    ════════════════════════════════════════════════════════════════════
    \033[1mDEPENDÊNCIAS DO SISTEMA\033[0m
    ════════════════════════════════════════════════════════════════════
    Ferramentas obrigatórias (verificadas automaticamente no pré-voo):
      rsync, mount, umount, find, du, df, cp, tar, zstd, pv, ionice,
      nice, su

    Ferramenta opcional (exigida somente se split.enabled = true):
      split

    Instalação em Debian/Ubuntu:
      apt install rsync cifs-utils tar zstd pv util-linux

    ════════════════════════════════════════════════════════════════════
    \033[1mSEGURANÇA\033[0m
    ════════════════════════════════════════════════════════════════════
    • O arquivo JSON contém credenciais — mantenha-o com chmod 600.
      O script exibe um AVISO DE SEGURANÇA se as permissões estiverem
      abertas para grupo ou outros usuários.
    • Credenciais nunca aparecem nos logs, mesmo com --debug.
      O valor de username, password e domain é substituído por "***"
      em qualquer saída de diagnóstico.
    • Use "mount_options": "ro" para montar a origem somente-leitura,
      protegendo os dados originais de modificações acidentais.
""")

# --- Configuração Padrão ---
DEFAULT_JSON_CONFIG = {
    "description": "Template de Configuração",
    "credentials": {
        "username": "usuario",
        "password": "senha",
        "domain": "dominio.local"
    },
    "paths": {
        "remote_share": "//192.168.0.100/Dados/Share",
        "mount_point": "/mnt/Remote/MountPoint",
        "backup_root": "/mnt/Backup",
        "log_dir": "/var/log/rsync"
    },
    "settings": {
        "mount_options": "ro",
        "min_space_mb": 1024,
        "bandwidth_limit_mb": 10,
        "transfer_rate_pv": "10m",
        "ionice_class": 3,
        "nice_priority": 19,
        "rsync_user": "root",
        "rsync_flags": [
            "-ahx", "--acls", "--xattrs", "--numeric-ids",
            "--chmod=ugo+r", "--ignore-errors", "--force", "--delete",
            "--info=del,name,stats2"
        ],
        "retention_policy": {
            "keep_logs_count": 31,
            "keep_full_backups_days": 30,
            "keep_differential_files_days": 240,
            "cleanup_empty_dirs": True
        },
        "split": {
            "enabled": False,
            "chunk_size": "4gb",
            "keep_original_after_split": True
        }
    },
    "excludes": ["*.tmp", "Thumbs.db"],
    "hooks": {
        "after_rsync": "",
        "after_full":  "",
        "after_split": ""
    }
}


class BackupJob:
    def __init__(self, config_path, debug=False):
        self.config_path = config_path
        self.debug = debug
        self.config = self._load_config()
        self.date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.setup_paths()
        self.setup_logging()

    # ------------------------------------------------------------------
    # Carregamento e validação do JSON
    # ------------------------------------------------------------------

    def _load_config(self):
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[\033[91mERRO\033[0m] Erro no JSON: {e}")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Configuração de caminhos
    # ------------------------------------------------------------------

    def setup_paths(self):
        paths = self.config['paths']
        root = paths['backup_root']

        self.orig_dir = paths['mount_point']

        remote_path = paths['remote_share']
        rel = remote_path[2:]                          # Remove prefixo "//"
        rel_sanitized = rel.replace('.', '_')

        self.client_root = os.path.join(root, rel_sanitized)
        self.incr_dir    = os.path.join(self.client_root, "Incremental")
        self.diff_dir    = os.path.join(self.client_root, "Differential")
        self.full_dir    = os.path.join(self.client_root, "Full")

        self.safe_name = rel_sanitized.replace('/', '_').replace('\\', '_')

        log_name     = f"backup_{self.safe_name}_{self.date_str}.log"
        self.log_file = os.path.join(paths['log_dir'], log_name)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def setup_logging(self):
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            logging.basicConfig(
                level=logging.DEBUG if self.debug else logging.INFO,
                format='[%(asctime)s] %(levelname)s: %(message)s',
                handlers=[
                    logging.FileHandler(self.log_file),
                    logging.StreamHandler(sys.stdout)
                ]
            )
            self.logger = logging.getLogger()
        except Exception as e:
            print(f"[\033[91mERRO\033[0m] Erro no Log: {e}")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Padrões de opções de mount que contêm credenciais e devem ser ocultados.
    _SENSITIVE_MOUNT_KEYS = {"username", "password", "domain"}

    @staticmethod
    def _redact_mount_opts(opts_str: str) -> str:
        """
        Recebe a string de opções do mount (ex: 'ro,username=foo,password=bar')
        e substitui o VALOR de cada chave sensível por '***'.
        """
        parts = opts_str.split(',')
        redacted = []
        for part in parts:
            if '=' in part:
                key, _ = part.split('=', 1)
                if key.strip() in BackupJob._SENSITIVE_MOUNT_KEYS:
                    redacted.append(f"{key}=***")
                    continue
            redacted.append(part)
        return ','.join(redacted)

    def _redact_cmd(self, cmd: list) -> str:
        """
        Converte a lista de argumentos do comando em string para log,
        ocultando o valor do argumento '-o' quando ele contiver credenciais
        de mount (username/password/domain).
        """
        parts = [str(x) for x in cmd]
        result = []
        skip_next = False
        for i, part in enumerate(parts):
            if skip_next:
                result.append(self._redact_mount_opts(part))
                skip_next = False
            elif part == '-o' and i + 1 < len(parts):
                result.append(part)
                skip_next = True   # próximo token é o valor de -o
            else:
                result.append(part)
        return ' '.join(result)

    def _run_cmd(self, cmd, check=True, **kwargs):
        if self.debug:
            self.logger.debug(
                f"[\033[36mDEBUG\033[0m] Executando: {self._redact_cmd(cmd)}"
            )
        return subprocess.run(cmd, check=check, **kwargs)

    def _check_config_file_permissions(self):
        """
        FIX 1 — Alerta se o arquivo de configuração (que contém credenciais)
        estiver com permissões abertas (leitura/escrita por grupo ou outros).
        """
        file_stat = os.stat(self.config_path)
        mode = file_stat.st_mode
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
            self.logger.warning(
                f"AVISO DE SEGURANÇA: '{self.config_path}' contém credenciais e está "
                f"acessível por grupo/outros (modo {oct(mode & 0o777)}). "
                f"Execute: chmod 600 {self.config_path}"
            )

    # ------------------------------------------------------------------
    # Verificação de dependências
    # ------------------------------------------------------------------

    REQUIRED_TOOLS = [
        "rsync", "mount", "umount", "find", "du", "df",
        "cp", "tar", "zstd", "pv", "ionice", "nice", "su",
    ]

    def check_dependencies(self):
        """
        Verifica se todas as ferramentas externas necessárias estão disponíveis
        no PATH antes de iniciar qualquer operação. Encerra com erro listando
        claramente o que está faltando, para que o administrador possa instalar
        os pacotes necessários antes de tentar novamente.

        'split' é verificado separadamente apenas se estiver habilitado no JSON,
        pois é uma dependência opcional.
        """
        missing = [tool for tool in self.REQUIRED_TOOLS if not shutil.which(tool)]

        # Verifica 'split' somente se o recurso estiver ativo no JSON
        split_cfg = self.config.get('settings', {}).get('split', {})
        if split_cfg.get('enabled', False) and not shutil.which('split'):
            missing.append('split')

        if missing:
            self.logger.error(
                "Ferramentas obrigatórias não encontradas no PATH: %s. "
                "Instale os pacotes correspondentes e tente novamente.",
                ", ".join(missing)
            )
            sys.exit(1)
        self.logger.info(
            "Dependências OK: todas as ferramentas necessárias foram encontradas."
        )

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def check_pre_flight(self):
        # Dependências primeiro — aborta imediatamente se algo faltar
        self.check_dependencies()

        # FIX 1 — verifica permissões do arquivo de config
        self._check_config_file_permissions()

        req_mb    = self.config['settings']['min_space_mb']
        req_bytes = req_mb * 1024 * 1024

        check_path = self.config['paths']['backup_root']
        while not os.path.exists(check_path):
            check_path = os.path.dirname(check_path)
            if not check_path or check_path == "/":
                break
        if not os.path.exists(check_path):
            check_path = "/"

        total, used, free = shutil.disk_usage(check_path)
        if free < req_bytes:
            raise Exception(f"Espaço insuficiente. Livre: {free/1024/1024:.2f} MB")

        self.logger.info(f"Disco OK. Livre: {free/1024/1024:.2f} MB")

        for d in [self.orig_dir, self.incr_dir, self.diff_dir, self.full_dir]:
            if not os.path.exists(d):
                self.logger.info(f"Criando: {d}")
                os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------
    # Montagem CIFS
    # ------------------------------------------------------------------

    def mount_share(self):
        if os.path.ismount(self.orig_dir):
            self.logger.info("Já montado. Pulando.")
            return False

        creds    = self.config.get('credentials', {})
        user     = creds.get('username')
        password = creds.get('password')
        domain   = creds.get('domain')

        remote = self.config['paths']['remote_share']
        opts   = self.config['settings'].get('mount_options', 'ro')

        if not user or not password:
            raise ValueError("Credenciais incompletas no JSON.")

        self.logger.info(f"Montando {remote}...")

        auth_opts  = f"username={user},password={password}"
        if domain:
            auth_opts += f",domain={domain}"
        final_opts = f"{opts},{auth_opts}"

        cmd = ["mount", "-t", "cifs", remote, self.orig_dir, "-o", final_opts]
        try:
            self._run_cmd(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            raise Exception(f"Erro no mount (Exit Code {e.returncode})")

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _run_hook(self, name: str):
        """
        Executa o comando shell associado ao hook 'name', se definido no JSON.
        Fire-and-forget: nenhum erro é capturado ou propagado.
        """
        cmd = self.config.get('hooks', {}).get(name, '').strip()
        if not cmd:
            return
        self.logger.info(f"Hook '{name}': {cmd}")
        subprocess.Popen(["bash", "-c", cmd])

    # ------------------------------------------------------------------
    # Rsync
    # ------------------------------------------------------------------

    def run_rsync(self):
        """
        FIX 3 — O rsync é executado sob o usuário configurado em 'rsync_user'
        utilizando 'su -c', replicando o comportamento do script shell original.

        GRACEFUL SHUTDOWN — Ao receber SIGINT (Ctrl+C), o sinal é encaminhado ao
        processo rsync filho e o script aguarda ele terminar antes de prosseguir
        com a desmontagem. Isso evita o 'Broken pipe' e garante que o rsync feche
        seus descritores e libere o mountpoint corretamente.
        """
        bw_kb       = int(self.config['settings']['bandwidth_limit_mb'] * 1024)
        rsync_user  = self.config['settings'].get('rsync_user', 'root')

        default_flags = [
            "-ahx", "--acls", "--xattrs", "--numeric-ids",
            "--chmod=ugo+r", "--ignore-errors", "--force", "--delete",
            "--info=del,name,stats2"
        ]
        rsync_flags = self.config['settings'].get('rsync_flags', default_flags)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.excl', delete=False) as tmp:
            if 'excludes' in self.config:
                tmp.write('\n'.join(self.config['excludes']))
            tmp_exclude = tmp.name

        # Permissão de leitura para o usuário que executa o rsync
        os.chmod(tmp_exclude, 0o644)

        proc = None
        interrupted = False

        try:
            src = self.orig_dir if self.orig_dir.endswith('/') else self.orig_dir + '/'

            rsync_parts = [
                "rsync",
                f"--bwlimit={bw_kb}",
                *rsync_flags,
                f"--exclude-from={tmp_exclude}",
                "--backup",
                f"--backup-dir={self.diff_dir}",
                f"--log-file={self.log_file}",
                src,
                self.incr_dir
            ]

            # Monta o comando su -c "<cmd>" se o usuário for diferente do atual
            current_user = os.environ.get('USER') or os.environ.get('LOGNAME') or 'root'
            if rsync_user != current_user:
                cmd_str = ' '.join(
                    subprocess.list2cmdline([p]) if ' ' in p else p
                    for p in rsync_parts
                )
                cmd = ["su", "-", rsync_user, "-c", cmd_str]
                self.logger.info(f"Executando Rsync como usuário '{rsync_user}'...")
            else:
                cmd = rsync_parts
                self.logger.info("Executando Rsync...")

            if self.debug:
                self.logger.debug("CMD: " + ' '.join(str(x) for x in cmd))

            # Usa Popen para manter referência ao processo filho e poder
            # encaminhar sinais de forma controlada.
            proc = subprocess.Popen(cmd)

            # Captura SIGINT enquanto o rsync está rodando.
            # Em vez de lançar KeyboardInterrupt imediatamente, encaminha o sinal
            # ao filho e aguarda ele finalizar — evitando broken pipe e umount
            # com mountpoint ocupado.
            original_sigint = signal.getsignal(signal.SIGINT)

            def _handle_sigint(signum, frame):
                nonlocal interrupted
                interrupted = True
                self.logger.warning(
                    "Interrupção recebida (Ctrl+C). Aguardando rsync finalizar "
                    "graciosamente antes de desmontar..."
                )
                if proc and proc.poll() is None:
                    proc.send_signal(signal.SIGINT)  # Encaminha ao rsync filho

            signal.signal(signal.SIGINT, _handle_sigint)

            try:
                proc.wait()  # Bloqueia até o rsync terminar (inclusive após SIGINT)
            finally:
                # Restaura o handler original independente do que acontecer
                signal.signal(signal.SIGINT, original_sigint)

            returncode = proc.returncode

            if interrupted:
                # Rsync recebeu SIGINT e saiu com code 20 — saída esperada
                self.logger.warning(
                    f"Rsync interrompido pelo usuário (code {returncode}). "
                    "Prosseguindo para desmontagem segura."
                )
                # Propaga KeyboardInterrupt para o fluxo principal (finally do main
                # garante a desmontagem)
                raise KeyboardInterrupt
            elif returncode == 0:
                self.logger.info("Rsync: Sucesso.")
                self._run_hook("after_rsync")
            elif returncode == 23:
                self.logger.warning(
                    "Rsync: Aviso (Code 23) — transferência parcial "
                    "(ex: permissão negada). Continuando."
                )
                self._run_hook("after_rsync")
            else:
                raise subprocess.CalledProcessError(returncode, cmd)

        finally:
            if os.path.exists(tmp_exclude):
                os.remove(tmp_exclude)

    # ------------------------------------------------------------------
    # Limpeza diferencial
    # ------------------------------------------------------------------

    def cleanup_differential(self):
        policy = self.config['settings'].get('retention_policy', {})
        days   = policy.get('keep_differential_files_days', 240)

        self.logger.info(f"Limpando Diff > {days} dias...")
        self._run_cmd(
            ["find", self.diff_dir, "-type", "f", "-mtime", f"+{days}", "-delete"],
            check=False
        )

        if policy.get('cleanup_empty_dirs', True):
            # "-mindepth 1" protege o próprio diretório raiz de ser removido
            # quando ele fica vazio — apenas subdiretórios órfãos são deletados.
            self._run_cmd(
                [
                    "find", self.diff_dir,
                    "-mindepth", "1",
                    "-type", "d",
                    "-empty",
                    "-delete"
                ],
                check=False
            )

    # ------------------------------------------------------------------
    # Full Backup
    # ------------------------------------------------------------------

    def run_full_backup(self):
        policy    = self.config['settings'].get('retention_policy', {})
        retention = policy.get('keep_full_backups_days', 30)

        self.logger.info(f"Verificando Fulls antigos (> {retention} dias)...")
        self._run_cmd(
            ["find", self.full_dir, "-type", "f", "-name", "Full_*.tar.zst",
             "-mtime", f"+{retention}", "-delete"],
            check=False
        )

        # Verifica se existe um Full válido (dentro do período de retenção)
        self.logger.info(f"Verificando validade do Full atual (< {retention} dias)...")
        cmd_check = [
            "find", self.full_dir,
            "-type", "f",
            "-name", "Full_*.tar.zst",
            "-mtime", f"-{retention}",
            "-print", "-quit"
        ]
        res = self._run_cmd(cmd_check, check=False, capture_output=True, text=True)
        if res.stdout and res.stdout.strip():
            recent_file = res.stdout.strip()
            self.logger.info(
                f"Backup Full válido encontrado ({recent_file}). Mantendo estrutura atual."
            )
            self._run_hook("after_full")
            return

        # ------------------------------------------------------------------
        # FIX 8 — Verifica espaço dinamicamente com base no tamanho real do INCR_DIR
        # Replica: INCR_SIZE=$(du -s --block-size=1K "$INCR_DIR") + 10% de margem
        # ------------------------------------------------------------------
        self.logger.info("Calculando espaço necessário para o Full (INCR_DIR + 10%)...")
        du_res = self._run_cmd(
            ["du", "-s", "--block-size=1K", self.incr_dir],
            check=True, capture_output=True, text=True
        )
        incr_size_kb = int(du_res.stdout.split()[0])
        min_space_kb  = incr_size_kb + incr_size_kb // 10   # +10% de margem
        min_space_mb  = min_space_kb / 1024

        df_res = self._run_cmd(
            ["df", "--output=avail", self.full_dir],
            check=True, capture_output=True, text=True
        )
        avail_kb = int(df_res.stdout.strip().splitlines()[-1])

        self.logger.info(
            f"INCR_DIR: {incr_size_kb} KB | Necessário (+ 10%): {min_space_kb} KB "
            f"| Disponível em FULL_DIR: {avail_kb} KB"
        )

        if avail_kb < min_space_kb:
            raise Exception(
                f"Espaço insuficiente em {self.full_dir}. "
                f"Necessário: {min_space_mb:.1f} MB, "
                f"Disponível: {avail_kb/1024:.1f} MB"
            )

        # Reflink / cp do snapshot
        persistent_full_dir = os.path.join(self.full_dir, "Full")
        if os.path.exists(persistent_full_dir):
            self.logger.info(f"Removendo '{persistent_full_dir}' antigo para atualização...")
            shutil.rmtree(persistent_full_dir)

        self.logger.info("Criando novo Snapshot 'Full' (Reflink)...")
        try:
            self._run_cmd(
                ["cp", "-a", "--reflink=always", self.incr_dir, persistent_full_dir],
                check=True, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError:
            self.logger.warning("Reflink falhou. Usando cp -a (cópia completa).")
            self._run_cmd(["cp", "-a", self.incr_dir, persistent_full_dir], check=True)

        # Compressão
        filename  = f"Full_{self.date_str}.tar.zst"
        zst_path  = os.path.join(self.full_dir, filename)

        ionice_class   = str(self.config['settings']['ionice_class'])
        nice_priority  = str(self.config['settings']['nice_priority'])
        transfer_rate  = self.config['settings'].get('transfer_rate_pv', '10m')

        self.logger.info(f"Compactando: {filename}")

        cmd_tar  = [
            "ionice", "-c", ionice_class,
            "nice", "-n", nice_priority,
            "tar", "-cvf", "-",
            "-C", self.full_dir,
            "Full"
        ]
        cmd_pv   = ["pv", "-q", "-L", transfer_rate]  # FIX 2 — restaura controle de taxa via pv
        cmd_zstd = ["zstd", "--threads=2"]

        if self.debug:
            self.logger.debug(
                f"Pipeline: {' '.join(cmd_tar)} | {' '.join(cmd_pv)} "
                f"| {' '.join(cmd_zstd)} > {zst_path}"
            )

        # FIX 4 — verifica exit code de TODOS os processos do pipeline (equivale a pipefail)
        try:
            p_tar  = subprocess.Popen(cmd_tar,  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p_pv   = subprocess.Popen(cmd_pv,   stdin=p_tar.stdout,  stdout=subprocess.PIPE)
            p_tar.stdout.close()   # Permite que p_tar receba SIGPIPE se p_pv sair

            with open(zst_path, "wb") as f_out:
                p_zstd = subprocess.Popen(cmd_zstd, stdin=p_pv.stdout, stdout=f_out)
            p_pv.stdout.close()    # Permite que p_pv receba SIGPIPE se p_zstd sair

            p_zstd.wait()
            p_pv.wait()
            p_tar.wait()

            errors = []
            if p_tar.returncode  != 0: errors.append(f"tar  (code {p_tar.returncode})")
            if p_pv.returncode   != 0: errors.append(f"pv   (code {p_pv.returncode})")
            if p_zstd.returncode != 0: errors.append(f"zstd (code {p_zstd.returncode})")

            if errors:
                raise Exception(f"Falha no pipeline de compressão: {', '.join(errors)}")

            zst_size_kb = int(
                self._run_cmd(
                    ["du", "-s", "--block-size=1K", zst_path],
                    check=True, capture_output=True, text=True
                ).stdout.split()[0]
            )
            self.logger.info(
                f"Full Backup concluído. Arquivo: {zst_path} ({zst_size_kb} KB). "
                f"Diretório 'Full' mantido no disco."
            )

        except Exception as e:
            # Remove arquivo parcial em caso de erro
            if os.path.exists(zst_path):
                os.remove(zst_path)
                self.logger.warning(f"Arquivo parcial '{zst_path}' removido.")
            self.logger.error(f"Erro na compactação: {e}")
            raise

        # ------------------------------------------------------------------
        # Split — executado logo após a compressão, se habilitado no JSON
        # ------------------------------------------------------------------
        split_cfg = self.config['settings'].get('split', {})
        if split_cfg.get('enabled', False):
            self._run_split(zst_path, split_cfg)
            self._run_hook("after_split")

        self._run_hook("after_full")

    # ------------------------------------------------------------------
    # Split do arquivo Full compactado
    # ------------------------------------------------------------------

    def _parse_chunk_size(self, chunk_size_str: str) -> tuple:
        """
        Converte uma string de tamanho de chunk (ex: '4gb', '500mb', '2tb')
        em (bytes: int, split_unit: str).

        split_unit é o sufixo aceito pelo comando split: 'M', 'G' ou 'T'.
        Unidades aceitas: mb, gb, tb (case-insensitive).
        Lança ValueError com mensagem clara para qualquer valor inválido.
        """
        UNITS = {
            'mb': (1024 ** 2, 'M'),
            'gb': (1024 ** 3, 'G'),
            'tb': (1024 ** 4, 'T'),
        }
        raw = str(chunk_size_str).strip().lower()
        # Separa número de unidade (ex: '4gb' → '4', 'gb')
        for suffix, (multiplier, split_letter) in UNITS.items():
            if raw.endswith(suffix):
                num_str = raw[: -len(suffix)].strip()
                try:
                    num = float(num_str)
                except ValueError:
                    raise ValueError(
                        f"Valor numérico inválido em chunk_size: '{chunk_size_str}'. "
                        f"Esperado ex: '4gb', '500mb', '2tb'."
                    )
                if num <= 0:
                    raise ValueError(
                        f"chunk_size deve ser maior que zero, recebido: '{chunk_size_str}'."
                    )
                chunk_bytes = int(num * multiplier)
                # Reconstrói a string para o split (ex: '4G', '500M')
                num_clean = int(num) if num == int(num) else num
                split_str  = f"{num_clean}{split_letter}"
                return chunk_bytes, split_str
        raise ValueError(
            f"Unidade desconhecida em chunk_size: '{chunk_size_str}'. "
            f"Use 'mb', 'gb' ou 'tb' (ex: '4gb', '500mb')."
        )

    def _run_split(self, zst_path: str, split_cfg: dict):
        """
        Divide o arquivo .tar.zst em partes conforme chunk_size definido no JSON.

        Parâmetros calculados automaticamente:
          - num_partes  = ceil(tamanho / chunk_bytes)
          - sufixo -a   = max(3, len(str(num_partes)))  → mínimo 3 dígitos
          - --numeric-suffixes=1                         → sempre começa em 001

        Retry: até 3 tentativas. A cada tentativa o diretório splitted/ é limpo.
        Validação: soma dos fragmentos deve ser igual ao tamanho do .tar.zst original.
        """
        MAX_RETRIES   = 3
        keep_original = split_cfg.get('keep_original_after_split', True)

        # Parseia e valida chunk_size antes de qualquer operação.
        # Sem fallback — a ausência do campo é um erro explícito para não
        # executar o split silenciosamente com um valor padrão inesperado.
        if 'chunk_size' not in split_cfg:
            raise Exception(
                "Configuração de split inválida: campo 'chunk_size' ausente no JSON. "
                "Exemplo: \"chunk_size\": \"4gb\""
            )
        chunk_size_raw = split_cfg['chunk_size']
        try:
            chunk_bytes, chunk_str = self._parse_chunk_size(chunk_size_raw)
        except ValueError as e:
            raise Exception(f"Configuração de split inválida: {e}")

        zst_size   = os.path.getsize(zst_path)
        num_partes = math.ceil(zst_size / chunk_bytes)
        suffix_len = max(3, len(str(num_partes)))

        split_dir      = os.path.join(self.full_dir, "splitted")
        # O prefixo preserva o nome original do arquivo + separador de parte
        prefix         = os.path.join(split_dir, os.path.basename(zst_path) + ".part_")

        self.logger.info(
            f"Split habilitado. Arquivo: {os.path.basename(zst_path)} "
            f"({zst_size / 1024**3:.2f} GB) → {num_partes} parte(s) de {chunk_size_raw.upper()} "
            f"(sufixo -{suffix_len} dígitos)."
        )

        def _prepare_split_dir():
            """Cria ou limpa o diretório splitted/ antes de cada tentativa."""
            if os.path.exists(split_dir):
                self.logger.info(f"Limpando '{split_dir}' antes do split...")
                for entry in os.scandir(split_dir):
                    try:
                        os.remove(entry.path)
                    except OSError as e:
                        self.logger.warning(f"Não foi possível remover '{entry.path}': {e}")
            else:
                self.logger.info(f"Criando '{split_dir}'...")
                os.makedirs(split_dir, exist_ok=True)

        def _validate_parts() -> bool:
            """Compara a soma dos tamanhos dos fragmentos com o arquivo original."""
            try:
                parts = sorted(
                    entry.path for entry in os.scandir(split_dir)
                    if entry.is_file()
                )
                if not parts:
                    self.logger.warning("Validação: nenhum fragmento encontrado.")
                    return False
                total = sum(os.path.getsize(p) for p in parts)
                if total != zst_size:
                    self.logger.warning(
                        f"Validação falhou: soma dos fragmentos ({total} bytes) "
                        f"≠ original ({zst_size} bytes)."
                    )
                    return False
                self.logger.info(
                    f"Validação OK: {len(parts)} fragmento(s), "
                    f"{total / 1024**3:.2f} GB totais."
                )
                return True
            except Exception as e:
                self.logger.warning(f"Erro durante validação: {e}")
                return False

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            self.logger.info(f"Split — tentativa {attempt}/{MAX_RETRIES}...")
            _prepare_split_dir()

            cmd = [
                "split",
                f"--bytes={chunk_str}",
                f"--numeric-suffixes=1",
                f"-a", str(suffix_len),
                "--verbose",
                zst_path,
                prefix,
            ]

            try:
                self._run_cmd(cmd, check=True)
            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Split retornou erro (code {e.returncode}) na tentativa {attempt}.")
                continue   # processo falhou → próxima tentativa

            # Processo saiu com 0 — valida integridade dos fragmentos
            if _validate_parts():
                success = True
                break
            # Validação falhou → próxima tentativa (split_dir será limpo no início do loop)

        if not success:
            # Esgotou as tentativas — limpa fragmentos corrompidos e mantém o .tar.zst intacto
            self.logger.error(
                f"Split falhou após {MAX_RETRIES} tentativas. "
                f"Fragmentos removidos. Arquivo original '{os.path.basename(zst_path)}' preservado."
            )
            _prepare_split_dir()   # deixa o diretório vazio mas existente
            raise Exception(f"Split de '{zst_path}' não concluído após {MAX_RETRIES} tentativas.")

        # ------------------------------------------------------------------
        # Pós-split bem-sucedido — decide se mantém ou remove o original
        # ------------------------------------------------------------------
        if not keep_original:
            self.logger.info(
                f"keep_original_after_split=false — removendo '{os.path.basename(zst_path)}'..."
            )
            try:
                os.remove(zst_path)
                self.logger.info("Arquivo original removido.")
            except OSError as e:
                self.logger.warning(f"Não foi possível remover o original: {e}")
        else:
            self.logger.info(
                f"keep_original_after_split=true — '{os.path.basename(zst_path)}' mantido."
            )
    # FIX 5 — retenção por CONTAGEM (keep_logs_count) em vez de só por data,
    # replicando o `tail -n +32` do script shell original.
    # ------------------------------------------------------------------

    def cleanup_logs(self):
        policy    = self.config['settings'].get('retention_policy', {})
        keep_count = policy.get('keep_logs_count', 31)
        log_dir   = self.config['paths']['log_dir']

        log_pattern = f"backup_{self.safe_name}_*.log"
        self.logger.info(
            f"Limpando logs de '{self.safe_name}', mantendo os {keep_count} mais recentes..."
        )

        # Lista todos os logs correspondentes, ordenados do mais novo ao mais velho
        try:
            all_logs = sorted(
                [
                    os.path.join(log_dir, f)
                    for f in os.listdir(log_dir)
                    if f.startswith(f"backup_{self.safe_name}_") and f.endswith(".log")
                ],
                key=os.path.getmtime,
                reverse=True
            )
        except FileNotFoundError:
            self.logger.warning(f"Diretório de log '{log_dir}' não encontrado. Pulando limpeza.")
            return

        logs_to_delete = all_logs[keep_count:]
        for log_path in logs_to_delete:
            try:
                os.remove(log_path)
                self.logger.info(f"Log removido: {log_path}")
            except OSError as e:
                self.logger.warning(f"Não foi possível remover '{log_path}': {e}")

    # ------------------------------------------------------------------
    # Cleanup final
    # ------------------------------------------------------------------

    def cleanup(self, did_mount):
        if did_mount:
            self.logger.info("Desmontando...")
            self._run_cmd(["umount", self.orig_dir], check=False)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Gerenciador de Backup Corporativo",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config_file", nargs="?",  help="Caminho do arquivo JSON de configuração")
    parser.add_argument("--init",      action="store_true", help="Cria um modelo de configuração padrão")
    parser.add_argument("--debug",     action="store_true", help="Exibe os comandos executados para depuração")
    args = parser.parse_args()

    if args.init:
        target = args.config_file if args.config_file else "config_modelo.json"
        if os.path.exists(target):
            print(f"[\033[93mAVISO\033[0m] '{target}' já existe. Abortando para não sobrescrever.")
            sys.exit(1)
        with open(target, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        os.chmod(target, 0o600)   # Proteção imediata das credenciais
        print(f"Modelo criado: {target} (chmod 600 aplicado)")
        sys.exit(0)

    if not args.config_file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.config_file):
        print(f"[\033[93mAVISO\033[0m] '{args.config_file}' não encontrado.")
        print("Criando modelo padrão...")
        with open(args.config_file, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        os.chmod(args.config_file, 0o600)
        print("Arquivo criado (chmod 600). Edite-o e tente novamente.")
        sys.exit(0)

    job = None
    did_mount = False
    try:
        job = BackupJob(args.config_file, debug=args.debug)
        job.logger.info(f"=== Job Iniciado: {args.config_file} ===")
        if args.debug:
            job.logger.info("MODO DEBUG ATIVADO")

        job.check_pre_flight()
        did_mount = job.mount_share()

        job.run_rsync()
        job.cleanup_differential()
        job.run_full_backup()
        job.cleanup_logs()

        job.logger.info("=== Sucesso ===")

    except KeyboardInterrupt:
        if job:
            job.logger.warning("=== Job interrompido pelo usuário (Ctrl+C) ===")
        else:
            print("\n[AVISO] Interrompido pelo usuário.")
        sys.exit(130)
    except Exception as e:
        if job:
            job.logger.error(f"FALHA: {e}")
        else:
            print(f"ERRO: {e}")
        sys.exit(1)
    finally:
        if job:
            job.cleanup(did_mount)


if __name__ == "__main__":
    main()