#!/usr/bin/env python3
import libvirt
import sys
import os
import shutil
import time
import argparse
import subprocess
import signal
import re
import fcntl
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import logging

# --- CONSTANTS ---
DISK_FORMAT = 'qcow2'
CONNECT_URI = 'qemu:///system'
SAFETY_MARGIN_PERCENT = 0.10
LOG_DIR = "/var/log/vhot"
FORBIDDEN_PATTERNS = ['_snap_', '_tmp_', 'snapshot', '.bak']
BACKUP_TIMEOUT_SECONDS = 14400  # 4 hours hard limit

# --- GLOBAL VARIABLES ---
CURRENT_DOMAIN_NAME = None
BACKUP_JOB_RUNNING = False
FILES_TO_CLEANUP = []       # Partial DESTINATION files (.bak)
LOCK_FILE_FD = None         # Lock file descriptor for single-instance enforcement

# --- LOGGER ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG)


def setup_logging(domain_name, timestamp):
    try:
        # FIX: Corrected fallback logic — check write access to LOG_DIR parent,
        # and also handle the case where LOG_DIR exists but is not writable.
        log_dir_final = LOG_DIR
        parent = os.path.dirname(LOG_DIR)
        dir_exists = os.path.isdir(LOG_DIR)
        parent_writable = os.access(parent, os.W_OK)
        dir_writable = dir_exists and os.access(LOG_DIR, os.W_OK)

        if not (parent_writable or dir_writable):
            log_dir_final = "/tmp/virsh_logs"

        os.makedirs(log_dir_final, exist_ok=True)
        log_path = os.path.join(log_dir_final, f"{domain_name}-{timestamp}.log")

        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(console_handler)

        logger.info(f"Log file: {log_path}")
    except Exception as e:
        print(f"LOG ERROR: {e}", file=sys.stderr)
        sys.exit(1)


# --- LOCK (single-instance per domain) ---

def acquire_lock(domain_name):
    """
    FIX: Prevent two instances of the script from running against the same
    domain simultaneously. Uses a per-domain lock file with fcntl advisory lock.
    """
    global LOCK_FILE_FD
    lock_path = f"/tmp/vhot_{domain_name}.lock"
    try:
        LOCK_FILE_FD = open(lock_path, 'w')
        fcntl.flock(LOCK_FILE_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        LOCK_FILE_FD.write(str(os.getpid()))
        LOCK_FILE_FD.flush()
    except OSError:
        print(
            f"FATAL: Another instance of vhot is already running for domain '{domain_name}'. "
            f"Lock file: {lock_path}",
            file=sys.stderr
        )
        sys.exit(1)


def release_lock():
    global LOCK_FILE_FD
    if LOCK_FILE_FD:
        try:
            fcntl.flock(LOCK_FILE_FD, fcntl.LOCK_UN)
            LOCK_FILE_FD.close()
        except Exception:
            pass
        LOCK_FILE_FD = None


# --- CLEANUP AND EMERGENCY ---

# FIX: Guard against perform_cleanup() being invoked twice (e.g., signal
# arrives while an internal exception is already running cleanup).
_CLEANUP_RUNNING = False


def perform_cleanup(exit_after=False):
    global BACKUP_JOB_RUNNING, CURRENT_DOMAIN_NAME, _CLEANUP_RUNNING

    if _CLEANUP_RUNNING:
        return
    _CLEANUP_RUNNING = True

    if sys.stdout.isatty():
        print()
    logger.warning("--- CLEANUP PROTOCOL INITIATED ---")

    # 1. Abort Libvirt Job (only if one was actually started by this script)
    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        logger.warning("Attempting to abort active Libvirt job...")
        try:
            subprocess.run(
                ['virsh', 'domjobabort', CURRENT_DOMAIN_NAME],
                check=True,
                capture_output=True
            )
            logger.info(" -> Libvirt job aborted successfully.")
        except subprocess.CalledProcessError as e:
            logger.critical(
                f" -> FAILED to abort job. VM restart might be required. "
                f"Error: {e.stderr.decode(errors='replace').strip()}"
            )
        BACKUP_JOB_RUNNING = False

    # 2. Remove partial .bak files
    if FILES_TO_CLEANUP:
        logger.info("Cleaning up partial destination files...")
        for f in list(FILES_TO_CLEANUP):
            if os.path.exists(f):
                try:
                    os.remove(f)
                    logger.info(f" -> Deleted: {os.path.basename(f)}")
                except Exception as e:
                    logger.warning(f" -> Could not delete {f}: {e}")

    release_lock()

    if exit_after:
        logger.warning("--- INTERRUPTED ---")
        sys.exit(1)


def signal_handler(sig, frame):
    perform_cleanup(exit_after=True)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# --- UTILS ---

def get_disk_details_from_xml(dom, target_devs_list):
    logger.info(f"Reading XML for disks: {target_devs_list}")
    details = {}
    found_devs = []
    try:
        root = ET.fromstring(dom.XMLDesc(0))
        for device in root.findall('./devices/disk'):
            target = device.find('target')
            if target is not None:
                dev_name = target.get('dev')
                if dev_name in target_devs_list:
                    source = device.find('source')
                    # FIX: Explicit error for unsupported non-file disk types
                    # (block devices, network disks, volume references).
                    if source is None:
                        logger.error(
                            f"Disk '{dev_name}' has no <source> element. "
                            "Diskless or unsupported disk type."
                        )
                        return None
                    if source.get('file') is None:
                        logger.error(
                            f"Disk '{dev_name}' is not a file-backed disk "
                            f"(found attributes: {source.attrib}). "
                            "Only file-backed qcow2/raw disks are supported."
                        )
                        return None
                    details[dev_name] = {'path': source.get('file')}
                    found_devs.append(dev_name)
    except Exception as e:
        logger.error(f"XML parse error: {e}")
        return None

    missing_devs = [d for d in target_devs_list if d not in found_devs]
    if missing_devs:
        logger.error("=" * 60)
        logger.error(f"FATAL ERROR: Requested disks not found in VM definition: {missing_devs}")
        logger.error(f"Disks found in VM: {found_devs}")
        logger.error("Check the --disk parameter.")
        logger.error("=" * 60)
        return None

    return details


def check_clean_state(dom, disk_details):
    # FIX: Removed the broad try/except that silently swallowed libvirt errors.
    # Each check is now individually guarded so partial failures are visible.
    try:
        job_type = dom.jobInfo()[0]
        if job_type != 0:
            return False, f"Active Libvirt job detected (type={job_type}). BlockCommit or Copy may be in progress."
    except libvirt.libvirtError as e:
        logger.warning(f"Could not query jobInfo (non-fatal, continuing): {e}")

    try:
        snap_count = dom.snapshotNum(0)
        if snap_count > 0:
            return False, f"VM has {snap_count} registered snapshot(s). Not supported with external backup."
    except libvirt.libvirtError as e:
        logger.warning(f"Could not query snapshotNum (non-fatal, continuing): {e}")

    # FIX: Also check the full path, not only the basename, to catch disks
    # stored in paths like /data/snapshots/vm-vda.qcow2.
    for dev, info in disk_details.items():
        full_path = info['path']
        basename = os.path.basename(full_path)
        if any(p in full_path for p in FORBIDDEN_PATTERNS):
            return False, (
                f"Disk '{dev}' path contains a forbidden pattern "
                f"(path='{full_path}', basename='{basename}'). "
                "The disk appears to be a temporary or dirty snapshot."
            )

    return True, "Clean"


def check_available_space(backup_dir, disk_details):
    needed = sum(
        os.path.getsize(i['path']) for i in disk_details.values()
    ) * (1 + SAFETY_MARGIN_PERCENT)

    os.makedirs(backup_dir, exist_ok=True)
    free_space = shutil.disk_usage(backup_dir).free

    if needed > free_space:
        logger.error(
            f"Insufficient space at destination. "
            f"Needed: {needed / 1024**3:.2f} GB | "
            f"Free: {free_space / 1024**3:.2f} GB"
        )
        return False

    logger.info(
        f"Space check passed. "
        f"Estimated needed: {needed / 1024**3:.2f} GB | "
        f"Free: {free_space / 1024**3:.2f} GB"
    )
    return True


# --- RETENTION MANAGEMENT ---

def _parse_timestamp_from_filename(filename):
    """
    FIX: Extract the date/time from the filename itself rather than relying
    on mtime, which can be modified by copies or filesystem mount options.
    Returns a datetime or None.
    """
    match = re.search(r'(\d{8})_(\d{6})', filename)
    if match:
        try:
            return datetime.strptime(f"{match.group(1)}_{match.group(2)}", "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return None


def _parse_identity_from_filename(filename):
    """
    FIX: Robust identity extraction that handles VM names containing hyphens
    and digits (e.g., 'vm-20250101-vda-20250311_235900.qcow2.bak').
    Strategy: strip the known suffix pattern from the right.
    Result: everything to the left of '-YYYYMMDD_HHMMSS' is the identity.
    """
    match = re.match(r'^(.+)-\d{8}_\d{6}', filename)
    if match:
        return match.group(1)
    return filename  # Fallback: use entire filename as identity


def manage_retention(backup_dir, days):
    if not os.path.isdir(backup_dir):
        return

    cutoff = datetime.now() - timedelta(days=days)
    backups = []

    try:
        for f in os.listdir(backup_dir):
            if not f.endswith('.bak'):
                continue
            fp = os.path.join(backup_dir, f)

            # FIX: Use timestamp embedded in the filename as primary date source.
            # Fall back to mtime only if the filename doesn't match the expected pattern.
            dt = _parse_timestamp_from_filename(f)
            if dt is None:
                logger.warning(
                    f"Could not parse timestamp from filename '{f}', "
                    "falling back to mtime."
                )
                dt = datetime.fromtimestamp(os.path.getmtime(fp))

            identity = _parse_identity_from_filename(f)
            date_str = dt.strftime('%Y-%m-%d')
            unique_key = f"{date_str}_{identity}"

            backups.append({'path': fp, 'dt': dt, 'unique_key': unique_key, 'name': f})

        backups.sort(key=lambda x: x['dt'], reverse=True)

    except Exception as e:
        logger.error(f"Error listing backups for retention: {e}")
        return

    if not backups:
        logger.info("--- RETENTION: No .bak files found. ---")
        return

    keep_list = []
    delete_list = []
    seen_keys = set()

    for b in backups:
        if b['unique_key'] in seen_keys:
            delete_list.append((b, "Redundant — newer backup exists for the same day and disk"))
        else:
            seen_keys.add(b['unique_key'])
            if b['dt'] < cutoff:
                delete_list.append((b, "Expired — older than retention limit"))
            else:
                keep_list.append(b)

    # Safety Lock: never delete the last available backup
    if not keep_list and delete_list:
        rescued, _ = delete_list.pop(0)
        keep_list.append(rescued)
        logger.warning(
            f"SAFETY LOCK: All backups are expired, but keeping the most recent one: "
            f"{os.path.basename(rescued['path'])}"
        )

    if sys.stdout.isatty():
        print()
    logger.info(f"--- RETENTION ANALYSIS ({days} days) ---")

    if keep_list:
        logger.info("VALID (Kept):")
        for b in keep_list:
            logger.info(f"   [OK] {b['name']} ({b['dt'].strftime('%Y-%m-%d %H:%M:%S')})")

    if delete_list:
        logger.info("CLEANUP (To be removed):")
        for b, reason in delete_list:
            logger.info(f"   [X]  {b['name']}")
            logger.info(f"        Reason: {reason}")
            try:
                os.remove(b['path'])
                logger.info("        -> Removed successfully.")
            except Exception as e:
                logger.error(f"        -> Failed to remove: {e}")

    logger.info("-" * 40)
    if sys.stdout.isatty():
        print()


# --- MONITORING AND BACKUP ---

def monitor_global_progress(target_files, total_bytes_all_disks):
    spinner = "|/-\\"
    spin = spinner[int(time.time() * 4) % 4]

    current_bytes_total = 0
    for fp in target_files:
        try:
            if os.path.exists(fp):
                current_bytes_total += os.path.getsize(fp)
        except OSError:
            pass

    perc = (current_bytes_total / total_bytes_all_disks * 100) if total_bytes_all_disks > 0 else 0
    curr_gb = current_bytes_total / (1024 ** 3)
    total_gb = total_bytes_all_disks / (1024 ** 3)

    msg = f"INFO: [ALL DISKS] [{spin}] {curr_gb:.2f} GB / {total_gb:.2f} GB ({perc:.1f}%)"

    if sys.stdout.isatty():
        sys.stdout.write(f"\r\033[K{msg}")
        sys.stdout.flush()

    return msg


def verify_backup_integrity(target_files_map):
    """
    FIX: Post-backup integrity check. Verifies that each .bak file:
      1. Exists on disk.
      2. Has a non-zero size (rules out empty files from truncated writes).
      3. Passes qemu-img check (detects internal qcow2 corruption).
    Returns True if all files pass, False otherwise.
    """
    logger.info("Running post-backup integrity checks...")
    all_ok = True

    for dev, fp in target_files_map.items():
        # Check 1: existence
        if not os.path.exists(fp):
            logger.error(f"   [FAIL] '{dev}': file not found at {fp}")
            all_ok = False
            continue

        # Check 2: non-zero size
        size = os.path.getsize(fp)
        if size == 0:
            logger.error(f"   [FAIL] '{dev}': file is empty (0 bytes) — likely a truncated write.")
            all_ok = False
            continue

        # Check 3: qemu-img structural check (only for qcow2)
        if DISK_FORMAT == 'qcow2':
            try:
                result = subprocess.run(
                    ['qemu-img', 'check', '-q', fp],
                    capture_output=True,
                    timeout=120
                )
                if result.returncode not in (0, 1):
                    # qemu-img check returns 0 (ok), 1 (errors fixed), 2+ (fatal)
                    logger.error(
                        f"   [FAIL] '{dev}': qemu-img check failed "
                        f"(rc={result.returncode}): "
                        f"{result.stderr.decode(errors='replace').strip()}"
                    )
                    all_ok = False
                else:
                    logger.info(f"   [OK]   '{dev}': {size / 1024**3:.2f} GB, qemu-img check passed.")
            except FileNotFoundError:
                logger.warning("   [SKIP] qemu-img not found — skipping structural check.")
            except subprocess.TimeoutExpired:
                logger.warning(f"   [SKIP] '{dev}': qemu-img check timed out.")
        else:
            logger.info(f"   [OK]   '{dev}': {size / 1024**3:.2f} GB (no structural check for format '{DISK_FORMAT}').")

    return all_ok


def run_atomic_backup(dom, backup_dir, disk_details, timestamp):
    global BACKUP_JOB_RUNNING, FILES_TO_CLEANUP

    logger.info("Starting ATOMIC backup (Parallel/Consistent)...")

    # 1. Prepare paths and calculate totals
    disk_xml_fragments = []
    target_files_map = {}
    total_bytes_source = 0

    for dev, info in disk_details.items():
        fp = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.{DISK_FORMAT}.bak")
        target_files_map[dev] = fp
        FILES_TO_CLEANUP.append(fp)

        disk_xml_fragments.append(
            f"<disk name='{dev}' type='file'>"
            f"<target file='{fp}'/>"
            f"<driver type='{DISK_FORMAT}'/>"
            f"</disk>"
        )

        total_bytes_source += os.path.getsize(info['path'])
        logger.info(f" -> Queued disk '{dev}': {fp}")

    # 2. Construct single atomic XML
    full_xml = (
        f"<domainbackup>"
        f"<disks>{''.join(disk_xml_fragments)}</disks>"
        f"</domainbackup>"
    )

    logger.info(f"Total source size: {total_bytes_source / (1024**3):.2f} GB")

    try:
        # 3. Start ONE job for ALL disks
        logger.info("[Libvirt] Requesting atomic backup...")
        dom.backupBegin(full_xml, None, 0)
        # FIX: BACKUP_JOB_RUNNING is set immediately after backupBegin() returns,
        # before entering the monitoring loop. This ensures that if any exception
        # fires before the flag was set, cleanup still attempts to abort the job.
        BACKUP_JOB_RUNNING = True
        logger.info("[Libvirt] Backup streams started.")

        last_log_time = 0
        target_files_list = list(target_files_map.values())
        job_start_time = time.time()

        while True:
            # FIX: Timeout watchdog — prevents infinite loop if QEMU hangs.
            elapsed = time.time() - job_start_time
            if elapsed > BACKUP_TIMEOUT_SECONDS:
                raise Exception(
                    f"Backup timeout exceeded ({BACKUP_TIMEOUT_SECONDS}s). "
                    "Job appears hung. Aborting."
                )

            # FIX: Distinguish between "job finished" and "communication error".
            # jobStats() returning an empty dict or raising an exception are
            # treated differently: only type==0 is a clean finish signal.
            try:
                stats = dom.jobStats()
            except libvirt.libvirtError as e:
                logger.warning(f"jobStats() communication error (will retry): {e}")
                time.sleep(2)
                continue

            job_type = stats.get('type', -1)

            if job_type == 0:
                # Clean finish: libvirt confirmed the job is gone.
                if sys.stdout.isatty():
                    print(f"\r\033[KINFO: [Success] Atomic backup finished.")
                BACKUP_JOB_RUNNING = False
                break

            if job_type == -1 and not stats:
                # Empty dict without explicit type — ambiguous. Verify with jobInfo.
                try:
                    if dom.jobInfo()[0] == 0:
                        if sys.stdout.isatty():
                            print(f"\r\033[KINFO: [Success] Atomic backup finished (confirmed via jobInfo).")
                        BACKUP_JOB_RUNNING = False
                        break
                except libvirt.libvirtError:
                    pass
                logger.warning("jobStats() returned empty without type=0. Retrying...")
                time.sleep(2)
                continue

            # FIX: Use the named constant for clarity.
            if stats.get('status') == libvirt.VIR_DOMAIN_JOB_STATUS_ACTIVE:
                pass  # Still running — normal path
            # Any explicit failure status triggers an exception.
            # libvirt does not always set 'status' on failure; the type going
            # back to 0 is the primary completion signal (handled above).

            current_time = time.time()
            if sys.stdout.isatty():
                monitor_global_progress(target_files_list, total_bytes_source)
            elif current_time - last_log_time > 60:
                curr_size = sum(
                    os.path.getsize(f) for f in target_files_list if os.path.exists(f)
                )
                pct = (curr_size / total_bytes_source * 100) if total_bytes_source else 0
                logger.info(
                    f"Progress: {curr_size / 1024**3:.2f} GB / "
                    f"{total_bytes_source / 1024**3:.2f} GB "
                    f"({pct:.1f}%) — elapsed {elapsed:.0f}s"
                )
                last_log_time = current_time

            time.sleep(0.5)

    except Exception as e:
        logger.error(f"ERROR during atomic backup: {e}")
        perform_cleanup()
        raise

    # 4. FIX: Verify integrity before declaring success.
    integrity_ok = verify_backup_integrity(target_files_map)
    if not integrity_ok:
        logger.error("Integrity check FAILED. Backup files may be corrupt.")
        # Add failed files back to cleanup so they are removed.
        for fp in target_files_map.values():
            if fp not in FILES_TO_CLEANUP:
                FILES_TO_CLEANUP.append(fp)
        perform_cleanup()
        raise Exception("Post-backup integrity check failed.")

    # 5. Success: remove files from cleanup list so they are NOT deleted.
    for fp in target_files_map.values():
        if fp in FILES_TO_CLEANUP:
            FILES_TO_CLEANUP.remove(fp)

    logger.info("Atomic backup completed and verified successfully!")


# --- MAIN ---
if __name__ == "__main__":

    example_text = '''Example (Cron):
  59 23 * * * /usr/bin/python3 /root/.services/scheduled/vhot.py \\
    --domain Trixie \\
    --backup-dir /mnt/Local/USB/A/Backup/srv17517/Container/B/Virt/Trixie \\
    --disk vda vdb
'''

    parser = argparse.ArgumentParser(
        description="Atomic Live KVM Backup (Libvirt API) - CLI",
        epilog=example_text,
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument('--domain', required=True, help="VM name (e.g., vm46176)")
    parser.add_argument('--backup-dir', required=True, help="Base destination directory")
    parser.add_argument('--disk', required=True, nargs='+', help="Disks to backup (e.g., vda vdb)")
    parser.add_argument('--retention-days', type=int, default=7,
                        help="Retention window in days (default: 7)")
    parser.add_argument('--timeout', type=int, default=BACKUP_TIMEOUT_SECONDS,
                        help=f"Maximum backup duration in seconds (default: {BACKUP_TIMEOUT_SECONDS})")

    args = parser.parse_args()
    BACKUP_TIMEOUT_SECONDS = args.timeout

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    setup_logging(args.domain, timestamp)

    # FIX: Acquire per-domain lock before doing anything with the hypervisor.
    acquire_lock(args.domain)

    conn = None
    try:
        conn = libvirt.open(CONNECT_URI)
        if conn is None:
            logger.error(f"Failed to connect to hypervisor at '{CONNECT_URI}'.")
            sys.exit(1)

        try:
            dom = conn.lookupByName(args.domain)
        except libvirt.libvirtError:
            logger.error(f"VM '{args.domain}' not found in hypervisor.")
            sys.exit(1)

        CURRENT_DOMAIN_NAME = args.domain

        # Silently abort any stale job from a previous crashed run.
        try:
            if dom.jobInfo()[0] != 0:
                logger.warning("Stale job detected from a previous run. Attempting to abort...")
                subprocess.run(
                    ['virsh', 'domjobabort', args.domain],
                    stderr=subprocess.DEVNULL
                )
        except Exception:
            pass

        bkp_dir = os.path.join(args.backup_dir, args.domain)

        details = get_disk_details_from_xml(dom, args.disk)
        if not details:
            raise Exception("Requested disks not found or not supported.")

        clean, msg = check_clean_state(dom, details)
        if not clean:
            logger.error(
                f"\nABORTED: {msg}\n"
                "The VM must be clean (no active snapshots or jobs) before backup."
            )
            sys.exit(1)

        if not check_available_space(bkp_dir, details):
            sys.exit(1)

        # Step 1: Atomic backup
        run_atomic_backup(dom, bkp_dir, details, timestamp)

        # Step 2: Retention cleanup
        logger.info("Starting retention check...")
        manage_retention(bkp_dir, args.retention_days)

        logger.info("PROCEDURE FINISHED SUCCESSFULLY.")

    except Exception as e:
        logger.exception(f"FATAL ERROR: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()
        release_lock()