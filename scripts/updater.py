#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os

def parse_containers(filepath):
    """Lê o ficheiro de configuração e extrai os containers da função main()."""
    containers = []
    in_main = False
    
    if not os.path.exists(filepath):
        print(f"[ERRO] Ficheiro de lista não encontrado: {filepath}")
        sys.exit(1)

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('main() {'):
                in_main = True
                continue
            
            if in_main and line == '}':
                break
                
            if in_main and line.startswith('ct'):
                containers.append(line)
                
    return containers

def get_container_state(name):
    """Verifica no LXC se o container existe e qual o seu estado."""
    try:
        result = subprocess.run(['lxc-info', '-n', name, '-s'], capture_output=True, text=True)
        if result.returncode == 0:
            if 'RUNNING' in result.stdout:
                return 'RUNNING'
            elif 'STOPPED' in result.stdout:
                return 'STOPPED'
        return 'NOT_FOUND'
    except FileNotFoundError:
        print("[ERRO] Comando lxc-info não encontrado. O LXC está instalado no host?")
        sys.exit(1)

def run_apt_update_upgrade(name):
    """Injeta os comandos do apt no container de forma não-interativa."""
    base_cmd = ['lxc-attach', '-n', name, '--', 'env', 'DEBIAN_FRONTEND=noninteractive']
    
    cmd_update = base_cmd + ['apt-get', 'update']
    cmd_upgrade = base_cmd + ['apt-get', 'upgrade', '-y', '-o', 'Dpkg::Options::=--force-confdef', '-o', 'Dpkg::Options::=--force-confold']
    
    try:
        subprocess.run(cmd_update, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(cmd_upgrade, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False

def needs_reboot(name):
    """Verifica se o apt gerou o ficheiro de solicitação de reboot dentro do container."""
    cmd = ['lxc-attach', '-n', name, '--', 'test', '-f', '/var/run/reboot-required']
    result = subprocess.run(cmd)
    return result.returncode == 0

def restart_container(name):
    """Executa um soft reboot no container via LXC."""
    try:
        subprocess.run(['lxc-stop', '-n', name, '--reboot'], check=True, stdout=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def main():
    if os.geteuid() != 0:
        print("[ERRO] Este script precisa de ser executado como root (sudo/su).")
        sys.exit(1)

    exemplos_de_uso = """
EXEMPLOS DE USO:
  1. Modo Interativo (Predefinição):
     # ./updater.py

  2. Atualizar todos de uma vez:
     # ./updater.py --all

  3. Atualização total com reinício inteligente (apenas se o sistema pedir):
     # ./updater.py --all --auto-restart

  4. Atualização total com reinício FORÇADO (reinicia todos os atualizados):
     # ./updater.py --all --force-restart
    """

    parser = argparse.ArgumentParser(
        description="Atualizador automatizado de containers LXC via APT.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=exemplos_de_uso
    )
    
    parser.add_argument('-l', '--list', default='/root/.services/container.sh', 
                        help="Caminho para o script contendo a lista (predefinição: /root/.services/container.sh)")
    parser.add_argument('--auto-restart', action='store_true', 
                        help="Reinicia o container automaticamente APENAS se a atualização exija.")
    parser.add_argument('--force-restart', action='store_true', 
                        help="Força o reinício do container após a atualização, mesmo que o sistema não peça.")
    parser.add_argument('--all', action='store_true', 
                        help="Atualiza todos os containers da lista sem perguntar.")
    
    args = parser.parse_args()
    
    target_containers = parse_containers(args.list)
    
    if not target_containers:
        print("[AVISO] Nenhum container com o prefixo 'ct' foi encontrado no bloco main().")
        sys.exit(0)

    if not args.all:
        print("\nContainers disponíveis para atualização:")
        for i, ct in enumerate(target_containers, 1):
            print(f"  {i}) {ct}")
            
        escolha = input("\nIntroduza o número do container (separe por vírgula para múltiplos, ex: 1,3): ")
        selecionados = []
        
        try:
            indices = [int(x.strip()) for x in escolha.split(',')]
            for idx in indices:
                if 1 <= idx <= len(target_containers):
                    selecionados.append(target_containers[idx-1])
                else:
                    print(f"[AVISO] Índice {idx} ignorado (fora do intervalo).")
        except ValueError:
            print("[ERRO] Entrada inválida. Introduza apenas os números correspondentes.")
            sys.exit(1)
            
        if not selecionados:
            print("[AVISO] Nenhum container válido selecionado. Operação cancelada.")
            sys.exit(0)
            
        target_containers = selecionados
        print("\nA iniciar a atualização dos containers selecionados...")
    else:
        print(f"\nModo --all ativado. A processar todos os {len(target_containers)} containers identificados...")

    report = {
        'success': [],
        'reboot_pending': [],
        'auto_restarted': [],
        'failed': [],
        'skipped': []
    }
    
    for ct in target_containers:
        print(f"\n[{ct}] A verificar o estado...")
        state = get_container_state(ct)
        
        if state == 'NOT_FOUND':
            print(f"  -> Ignorado: Container não existe no LXC.")
            report['skipped'].append(f"{ct} (Não encontrado)")
            continue
            
        if state == 'STOPPED':
            print(f"  -> Ignorado: Container está desligado.")
            report['skipped'].append(f"{ct} (Desligado)")
            continue
            
        print(f"  -> A atualizar via APT (isto pode levar alguns minutos)...")
        if run_apt_update_upgrade(ct):
            # Lógica de Reinício Forçado
            if args.force_restart:
                print(f"  -> Reinício forçado ativado. A reiniciar o container...")
                if restart_container(ct):
                    report['auto_restarted'].append(ct)
                else:
                    print(f"  -> Falha ao tentar reiniciar.")
                    report['reboot_pending'].append(ct)
            
            # Lógica de Reinício Inteligente
            elif needs_reboot(ct):
                if args.auto_restart:
                    print(f"  -> Reboot necessário. A reiniciar o container automaticamente...")
                    if restart_container(ct):
                        report['auto_restarted'].append(ct)
                    else:
                        print(f"  -> Falha ao tentar reiniciar.")
                        report['reboot_pending'].append(ct)
                else:
                    print(f"  -> Atualizado, mas [REBOOT PENDENTE].")
                    report['reboot_pending'].append(ct)
            else:
                print(f"  -> Atualizado com sucesso.")
                report['success'].append(ct)
        else:
            print(f"  -> [ERRO] Falha ao executar o apt no container.")
            report['failed'].append(ct)
            
    print("\n" + "="*40)
    print("RELATÓRIO FINAL DE ATUALIZAÇÃO LXC")
    print("="*40)
    
    if report['success']:
        print("\n[SUCESSO] Atualizados sem necessidade de reboot:")
        for c in report['success']: print(f"  - {c}")
        
    if report['auto_restarted']:
        print("\n[REINICIADOS] Atualizados e reiniciados automaticamente:")
        for c in report['auto_restarted']: print(f"  - {c}")
        
    if report['reboot_pending']:
        print("\n[ALERTA - REBOOT PENDENTE] Requerem reinicialização manual:")
        for c in report['reboot_pending']: print(f"  - {c}")
        
    if report['failed']:
        print("\n[FALHA] Erro durante a execução do apt:")
        for c in report['failed']: print(f"  - {c}")
        
    if report['skipped']:
        print("\n[IGNORADOS] Não processados:")
        for c in report['skipped']: print(f"  - {c}")
        
    print("\n" + "="*40)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[AVISO] Operação cancelada pelo utilizador (Ctrl+C). A sair graciosamente...")
        sys.exit(0)