#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import math
import shlex
import shutil
import stat
import signal
import fcntl
import logging
import subprocess
import argparse
import tempfile
import textwrap
from datetime import datetime

# ---------------------------------------------------------------------------
# Detailed Help Text
# ---------------------------------------------------------------------------
HELP_TEXT = textwrap.dedent("""
    ════════════════════════════════════════════════════════════════════
    \033[1mprisma.py  —  Corporate Backup Manager (CIFS → Local)\033[0m
    ════════════════════════════════════════════════════════════════════

    \033[1mOVERVIEW\033[0m
    ─────────────────────────────────────────────────────────────────
    Backs up Windows/CIFS network shares to local disk using a
    three-layer strategy:

      1. \033[1mContinuous Incremental\033[0m — rsync maintains an up-to-date mirror
         of remote files in the Incremental folder. Modified or deleted
         files are automatically moved to the Differential folder
         (rsync --backup / --backup-dir mechanism), preserving change
         history without duplicating unchanged data.

      2. \033[1mDifferential\033[0m           — each rsync run deposits here the files
         that were replaced or deleted at the source, timestamped by
         rsync. They serve as recovery points for previous versions.

      3. \033[1mFull (snapshot + .tar.zst)\033[0m — periodically (per retention policy),
         the Incremental mirror is copied via Reflink (Copy-on-Write,
         instant on BTRFS/XFS filesystems) and then compressed with
         tar + zstd, producing a portable Full_YYYY-MM-DD_HH-MM-SS.tar.zst
         file. If a Full already exists within the retention window,
         this step is skipped without error.

    \033[1mGENERATED DIRECTORY STRUCTURE\033[0m
    ─────────────────────────────────────────────────────────────────
    The structure is derived automatically from "remote_share".
    Dots (.) are replaced by underscores (_).

    Example  →  remote_share = "//192.168.0.100/Data/Share"
                backup_root  = "/mnt/Backup"

    /mnt/Backup/
    └── 192_168_0_100/
        └── Data/
            └── Share/
                ├── Incremental/   ← rsync mirror (source for recovery)
                ├── Differential/  ← changed / deleted files
                └── Full/
                    ├── Full/                         ← snapshot (reflink)
                    ├── Full_2025-07-01_02-00-00.tar.zst
                    └── splitted/                     ← parts (if split enabled)
                        ├── Full_2025-07-01_02-00-00.tar.zst.part_001
                        └── Full_2025-07-01_02-00-00.tar.zst.part_002

    \033[1mEXECUTION FLOW\033[0m
    ─────────────────────────────────────────────────────────────────
    check_pre_flight()       → checks dependencies, disk space,
                               config file permissions, and acquires
                               an exclusive lock to prevent concurrent runs
    mount_share()            → mounts the CIFS share
    run_rsync()              → syncs source → Incremental
                               (replaced files go to Differential)
    cleanup_differential()   → removes expired Differential files
    run_full_backup()        → creates Full .tar.zst if needed
    cleanup_logs()           → keeps only the N most recent logs
    cleanup()                → unmounts the share and releases the lock

    \033[1mCOMMAND-LINE ARGUMENTS\033[0m
    ─────────────────────────────────────────────────────────────────
    \033[1mprisma.py [config.json] [--init] [--debug]\033[0m

      config.json   Path to the JSON configuration file for the job.
                    Required to run a backup. If the file does not exist,
                    a default template is created automatically with
                    chmod 600 and the script exits (edit and re-run).

      --init        Creates a template JSON file (config_template.json)
                    with all fields filled with example values.
                    Combine with a custom name:
                      $ python3 prisma.py clients/new.json --init
                    → creates clients/new.json with chmod 600.
                    Aborts if the file already exists (never overwrites).

      --debug       Prints each external command executed to the log
                    (rsync, mount, tar, zstd, ionice, nice, pv, split…)
                    before running it. Useful for diagnosing failures.
                    Mount credentials are automatically redacted
                    (username=***, password=***) even in debug mode.

    \033[1mQUICK EXAMPLES\033[0m
    ─────────────────────────────────────────────────────────────────
      # Create a configuration template
      $ python3 prisma.py --init
      $ python3 prisma.py clients/company_xyz.json --init

      # Run backup (normal mode)
      $ python3 prisma.py clients/company_xyz.json

      # Run with detailed diagnostic output
      $ python3 prisma.py clients/company_xyz.json --debug

      # Schedule via cron (daily at 02:00)
      0 2 * * * /usr/bin/python3 /opt/scripts/prisma.py /etc/backup/company_xyz.json

    ════════════════════════════════════════════════════════════════════
    \033[1mFULL JSON CONFIGURATION FILE REFERENCE\033[0m
    ════════════════════════════════════════════════════════════════════

    \033[1m┌─ SECTION: "credentials"\033[0m
    │  Credentials used by mount.cifs to authenticate on the server.
    │  The JSON file must have permission 600 (the script warns if not).
    │
    │  "username"  : string  — login for the account with share access.
    │                          Can be a local or domain account.
    │                          Example: "username": "svc_backup"
    │
    │  "password"  : string  — account password.
    │                          Example: "password": "Str0ngP@ss!"
    │
    │  "domain"    : string  — Active Directory domain (optional).
    │                          If omitted or empty, mount uses local
    │                          workgroup authentication.
    │                          Example: "domain": "CORP"
    │                          No domain: "domain": ""
    └──────────────────────────────────────────────────────────────────

    \033[1m┌─ SECTION: "paths"\033[0m
    │
    │  "remote_share"  : string — UNC path of the CIFS share to be
    │                             mounted and copied.
    │                             Format: "//IP_or_HOSTNAME/Share/Subfolder"
    │                             The path (without leading slashes) is used
    │                             to derive the local folder structure.
    │                             Dots are replaced by underscores.
    │                             Example: "//192.168.10.5/Sales"
    │                             → local folder: 192_168_10_5/Sales/
    │
    │  "mount_point"   : string — Local directory where the share will be
    │                             temporarily mounted during the job.
    │                             Must exist or be creatable. The script does
    │                             NOT create this directory; create it manually.
    │                             Example: "/mnt/Remote/Sales"
    │
    │  "backup_root"   : string — Root where the entire backup structure will
    │                             be stored. The script creates the necessary
    │                             subdirectories automatically.
    │                             Example: "/mnt/Backup"
    │                             → data in: /mnt/Backup/192_168_10_5/Sales/
    │
    │  "log_dir"       : string — Directory where daily log files will be
    │                             written. The log name includes the job
    │                             identifier and timestamp:
    │                             backup_<safe_name>_YYYY-MM-DD_HH-MM-SS.log
    │                             Example: "/var/log/prisma"
    └──────────────────────────────────────────────────────────────────

    \033[1m┌─ SECTION: "settings"\033[0m
    │
    │  "mount_options"       : string — Extra options passed to mount.cifs
    │                                   via -o flag, BEFORE credentials.
    │                                   Credentials (username/password/domain)
    │                                   are added automatically.
    │                                   Recommended value: "ro" (read-only)
    │                                   to prevent accidental changes to the
    │                                   source during backup.
    │                                   For read/write: "rw"
    │                                   With explicit SMB version: "ro,vers=2.1"
    │                                   Example: "mount_options": "ro,vers=3.0"
    │
    │  "min_space_mb"        : integer — Minimum free space required on the
    │                                   backup_root volume before starting.
    │                                   If free space is lower, the script
    │                                   aborts immediately with an error.
    │                                   Value in megabytes.
    │                                   Example: 1024  → requires at least 1 GB
    │                                            51200 → requires at least 50 GB
    │
    │  "bandwidth_limit_mb"  : number  — Bandwidth limit for rsync,
    │                                   in MEGABYTES per second.
    │                                   Internally converted to KB/s and
    │                                   passed to rsync via --bwlimit.
    │                                   Use to avoid saturating the network link.
    │                                   0 = no limit.
    │                                   Example: 10   → limit to 10 MB/s
    │                                            0.5  → limit to 512 KB/s
    │
    │  "transfer_rate_pv"    : string  — Maximum read rate applied by 'pv'
    │                                   in the Full compression pipeline
    │                                   (tar | pv | zstd).
    │                                   Controls disk read speed during
    │                                   compression to prevent the process
    │                                   from consuming all server I/O.
    │                                   Format accepted by pv: number + unit.
    │                                   Examples: "10m" → 10 MB/s
    │                                             "50m" → 50 MB/s
    │                                             "500k" → 500 KB/s
    │
    │  "ionice_class"        : integer — I/O priority class assigned to the
    │                                   compression process (tar + zstd)
    │                                   via ionice(1). Reduces backup impact
    │                                   on other disk-using processes.
    │                                   Possible values:
    │                                     1 = Real-time  (high priority, use
    │                                         with caution — may stall system)
    │                                     2 = Best-effort (kernel default)
    │                                     3 = Idle       (only uses I/O when
    │                                         nobody else needs it — recommended)
    │                                   Example: "ionice_class": 3  ← recommended
    │
    │  "nice_priority"       : integer — CPU priority (niceness) assigned to
    │                                   the compression process via nice(1).
    │                                   Values from -20 (highest CPU priority)
    │                                   to 19 (lowest — "polite").
    │                                   Use 19 so backup does not compete with
    │                                   production applications for CPU.
    │                                   Example: "nice_priority": 19  ← recommended
    │
    │  NOTE: ionice_class and nice_priority affect ONLY the Full compression
    │  step (tar | pv | zstd). rsync runs without priority adjustment
    │  (use bandwidth_limit_mb to control its impact).
    │
    │  "rsync_user"          : string  — OS user under which rsync will run
    │                                   (via su -c). Useful when the user
    │                                   running the script differs from the
    │                                   user with access to the destination.
    │                                   If equal to the current user, su is skipped.
    │                                   Example: "rsync_user": "root"
    │
    │  "rsync_flags"         : list    — Flags passed directly to rsync.
    │                                   Replaces the default set entirely.
    │                                   Flags already added by the script
    │                                   (do not include here):
    │                                     --bwlimit, --backup, --backup-dir,
    │                                     --log-file, --exclude-from
    │                                   Recommended default flags:
    │                                     "-ahx"           → archive + human-readable
    │                                                        + no filesystem crossing
    │                                     "--acls"         → preserve ACLs
    │                                     "--xattrs"       → preserve extended attributes
    │                                     "--numeric-ids"  → do not map UID/GID by name
    │                                     "--chmod=ugo+r"  → ensure file readability
    │                                     "--ignore-errors"→ do not abort on read errors
    │                                     "--force"        → force directory replacement
    │                                     "--delete"       → remove at destination what
    │                                                        was deleted at source
    │                                     "--info=del,name,stats2" → detailed logging
    │
    │  ┌─ SUBSECTION: "retention_policy"\033[0m
    │  │
    │  │  "keep_logs_count"              : integer — Maximum number of log files
    │  │                                             to keep for this job.
    │  │                                             Logs are sorted by modification
    │  │                                             date; oldest beyond this limit
    │  │                                             are deleted.
    │  │                                             Example: 31  → keeps the 31 most
    │  │                                             recent logs (≈ 1 daily month)
    │  │
    │  │  "keep_full_backups_days"       : integer — How many days a Full .tar.zst
    │  │                                             file is considered valid.
    │  │                                             Full files older than this are
    │  │                                             deleted. If no Full exists within
    │  │                                             this window, a new one is created.
    │  │                                             Example: 30 → keeps Fulls from
    │  │                                             last 30 days; creates new Full
    │  │                                             if most recent is > 30 days old.
    │  │
    │  │  "keep_differential_files_days" : integer — Maximum retention for files in
    │  │                                             the Differential folder.
    │  │                                             Files with mtime older than this
    │  │                                             value (in days) are deleted.
    │  │                                             Example: 240 → keeps version
    │  │                                             history for 8 months.
    │  │
    │  │  "cleanup_empty_dirs"           : bool    — If true, empty subdirectories
    │  │                                             remaining in Differential after
    │  │                                             expired file cleanup are
    │  │                                             automatically removed.
    │  │                                             Recommended: true
    │  └──────────────────────────────────────────────────────────────

    │  ┌─ SUBSECTION: "split"\033[0m
    │  │  Splits the Full .tar.zst into smaller parts after compression.
    │  │  Useful for storage on media with file size limits
    │  │  (FAT32: 4 GB, some cloud backups, tapes, etc.).
    │  │
    │  │  "enabled"                  : bool   — Enables or disables split.
    │  │                                        false → .tar.zst is not split.
    │  │                                        true  → splits immediately after
    │  │                                        successful compression.
    │  │
    │  │  "chunk_size"               : string — Maximum size of each part.
    │  │                                        Accepted units (case-insensitive):
    │  │                                          "mb" → megabytes
    │  │                                          "gb" → gigabytes
    │  │                                          "tb" → terabytes
    │  │                                        Examples:
    │  │                                          "4gb"   → parts up to 4 GB
    │  │                                          "500mb" → parts up to 500 MB
    │  │                                        Parts are named:
    │  │                                          Full_<timestamp>.tar.zst.part_001
    │  │                                          Full_<timestamp>.tar.zst.part_002
    │  │                                          …
    │  │                                        Suffix digit count is calculated
    │  │                                        automatically (minimum 3).
    │  │                                        Split has up to 3 retries with
    │  │                                        integrity validation (sum of part
    │  │                                        sizes must equal the original).
    │  │
    │  │  "keep_original_after_split" : bool   — Whether to keep or remove the
    │  │                                         original .tar.zst after successful
    │  │                                         part creation.
    │  │                                         true  → keeps the .tar.zst intact
    │  │                                                  (more disk space, but allows
    │  │                                                  direct restore without joining
    │  │                                                  parts).
    │  │                                         false → removes .tar.zst after split
    │  │                                                  (saves disk space).
    │  └──────────────────────────────────────────────────────────────

    \033[1m┌─ SECTION: "excludes"\033[0m
    │  List of file/directory patterns to be ignored by rsync.
    │  Uses rsync pattern syntax (--exclude-from).
    │  Useful to skip temp files, caches and Windows junk.
    │
    │  Common pattern examples:
    │    "*.tmp"          → any temporary file
    │    "Thumbs.db"      → Windows Explorer thumbnail cache
    │    "desktop.ini"    → Windows folder configuration file
    │    "~$*"            → open/locked Office files
    │    "*.log"          → remote application log files
    │    ".Trash*"        → system trash
    │    "pagefile.sys"   → Windows virtual memory (huge, useless in backup)
    │    "hiberfil.sys"   → Windows hibernation file
    │
    │  Full example:
    │    "excludes": ["*.tmp", "Thumbs.db", "desktop.ini", "~$*", "*.log"]
    └──────────────────────────────────────────────────────────────────

    \033[1m┌─ SECTION: "hooks"\033[0m
    │  Optional shell commands executed automatically at the end of
    │  specific job steps. Each field accepts any valid bash command
    │  (including pipes, redirections and environment variables).
    │  Missing or empty-string fields are ignored.
    │
    │  Hooks are dispatched via "bash -c '<command>'" and their output
    │  is captured and logged (stdout + stderr). The job logs the hook's
    │  exit code and continues regardless of the result.
    │
    │  "after_rsync"  : string — Executed after rsync (incremental)
    │                            completes, with success or warning (code 23).
    │                            Example: "after_rsync": "echo rsync done >> /var/log/hooks.log"
    │
    │  "after_full"   : string — Executed after run_full_backup() completes,
    │                            regardless of whether a new Full was created
    │                            or skipped because a valid one existed.
    │                            Example: "after_full": "touch ~/full_done.flag"
    │
    │  "after_split"  : string — Executed after split completes successfully.
    │                            Only fired when split.enabled = true and
    │                            the split was performed in this run.
    │                            Example: "after_split": "touch ~/split_done.flag"
    └──────────────────────────────────────────────────────────────────

    ════════════════════════════════════════════════════════════════════
    \033[1mFULL JSON EXAMPLE\033[0m
    ════════════════════════════════════════════════════════════════════

    {
        "description": "File server backup — Sales dept",

        "credentials": {
            "username": "svc_backup",
            "password": "Str0ngP@ss!",
            "domain":   "CORP"
        },

        "paths": {
            "remote_share": "//192.168.10.5/Sales",
            "mount_point":  "/mnt/Remote/Sales",
            "backup_root":  "/mnt/Backup",
            "log_dir":      "/var/log/prisma"
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
            "after_rsync": "echo rsync done >> /var/log/prisma_hooks.log",
            "after_full":  "touch ~/full_done.flag",
            "after_split": ""
        }
    }

    ════════════════════════════════════════════════════════════════════
    \033[1mSYSTEM DEPENDENCIES\033[0m
    ════════════════════════════════════════════════════════════════════
    Required tools (automatically checked during pre-flight):
      rsync, mount, umount, find, du, df, cp, tar, zstd, pv, ionice,
      nice, su

    Optional tool (required only if split.enabled = true):
      split

    Installation on Debian/Ubuntu:
      apt install rsync cifs-utils tar zstd pv util-linux

    ════════════════════════════════════════════════════════════════════
    \033[1mSECURITY\033[0m
    ════════════════════════════════════════════════════════════════════
    • The JSON file contains credentials — keep it with chmod 600.
      The script displays a SECURITY WARNING if permissions are open
      to group or other users.
    • Credentials never appear in logs, even with --debug.
      The values of username, password and domain are replaced by "***"
      in any diagnostic output.
    • Use "mount_options": "ro" to mount the source read-only,
      protecting original data from accidental modification.
    • A lock file prevents concurrent executions of the same job,
      avoiding data corruption if cron fires the job twice.
""")

# ---------------------------------------------------------------------------
# Default Configuration Template
# ---------------------------------------------------------------------------
DEFAULT_JSON_CONFIG = {
    "description": "Configuration Template",
    "credentials": {
        "username": "user",
        "password": "password",
        "domain": "domain.local"
    },
    "paths": {
        "remote_share": "//192.168.0.100/Data/Share",
        "mount_point": "/mnt/Remote/MountPoint",
        "backup_root": "/mnt/Backup",
        "log_dir": "/var/log/prisma"
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


# ---------------------------------------------------------------------------
# BackupJob
# ---------------------------------------------------------------------------

class BackupJob:
    def __init__(self, config_path: str, debug: bool = False):
        self.config_path = config_path
        self.debug       = debug
        self.config      = self._load_config()
        self.date_str    = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self._lock_fh    = None   # file handle for the exclusive lock
        self.setup_paths()
        self.setup_logging()

    # ------------------------------------------------------------------
    # JSON loading and validation
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as exc:
            print(f"[\033[91mERROR\033[0m] Failed to load JSON config: {exc}")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Path setup
    # ------------------------------------------------------------------

    def setup_paths(self):
        paths = self.config['paths']
        root  = paths['backup_root']

        self.orig_dir = paths['mount_point']

        remote_path   = paths['remote_share']
        rel           = remote_path.lstrip('/')          # Remove leading slashes
        rel_sanitized = rel.replace('.', '_')

        self.client_root = os.path.join(root, rel_sanitized)
        self.incr_dir    = os.path.join(self.client_root, "Incremental")
        self.diff_dir    = os.path.join(self.client_root, "Differential")
        self.full_dir    = os.path.join(self.client_root, "Full")

        self.safe_name = rel_sanitized.replace('/', '_').replace('\\', '_')

        log_name      = f"backup_{self.safe_name}_{self.date_str}.log"
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
        except Exception as exc:
            print(f"[\033[91mERROR\033[0m] Failed to initialize logging: {exc}")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _SENSITIVE_MOUNT_KEYS = {"username", "password", "domain"}

    @staticmethod
    def _redact_mount_opts(opts_str: str) -> str:
        """
        Receives the mount options string (e.g. 'ro,username=foo,password=bar')
        and replaces the VALUE of each sensitive key with '***'.
        """
        parts    = opts_str.split(',')
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
        Converts the command argument list to a loggable string,
        masking the value of the '-o' argument when it contains
        mount credentials (username/password/domain).
        """
        parts      = [str(x) for x in cmd]
        result     = []
        skip_next  = False
        for i, part in enumerate(parts):
            if skip_next:
                result.append(self._redact_mount_opts(part))
                skip_next = False
            elif part == '-o' and i + 1 < len(parts):
                result.append(part)
                skip_next = True   # next token is the -o value
            else:
                result.append(part)
        return ' '.join(result)

    def _run_cmd(self, cmd: list, check: bool = True, **kwargs):
        """Wrapper around subprocess.run with optional debug logging."""
        if self.debug:
            self.logger.debug(
                f"[\033[36mDEBUG\033[0m] Running: {self._redact_cmd(cmd)}"
            )
        return subprocess.run(cmd, check=check, **kwargs)

    def _check_config_file_permissions(self):
        """
        Warns if the configuration file (which contains credentials)
        has open permissions (group or world readable/writable).
        """
        file_stat = os.stat(self.config_path)
        mode      = file_stat.st_mode
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
            self.logger.warning(
                f"SECURITY WARNING: '{self.config_path}' contains credentials and is "
                f"accessible by group/others (mode {oct(mode & 0o777)}). "
                f"Run: chmod 600 {self.config_path}"
            )

    # ------------------------------------------------------------------
    # Exclusive lock — prevents concurrent runs of the same job
    # ------------------------------------------------------------------

    def _acquire_lock(self):
        """
        Creates and locks a .lock file derived from the config file path.
        Uses fcntl.LOCK_EX | fcntl.LOCK_NB so a second instance fails
        immediately instead of waiting silently.

        Raises RuntimeError if another instance is already running.
        """
        lock_path = self.config_path + ".lock"
        self._lock_fh = open(lock_path, 'w')
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fh.write(str(os.getpid()))
            self._lock_fh.flush()
            self.logger.info(f"Lock acquired: {lock_path}")
        except OSError:
            self._lock_fh.close()
            self._lock_fh = None
            raise RuntimeError(
                f"Another instance of this job is already running "
                f"(lock file: {lock_path}). Aborting."
            )

    def _release_lock(self):
        """Releases the exclusive lock and closes the lock file handle."""
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
                self._lock_fh.close()
            except OSError as exc:
                self.logger.warning(f"Failed to release lock: {exc}")
            finally:
                self._lock_fh = None

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    REQUIRED_TOOLS = [
        "rsync", "mount", "umount", "find", "du", "df",
        "cp", "tar", "zstd", "pv", "ionice", "nice", "su",
    ]

    def check_dependencies(self):
        """
        Verifies that all required external tools are available in PATH
        before any operation begins. Exits with a clear error listing
        what is missing so the administrator can install the packages
        before retrying.

        'split' is checked separately only if it is enabled in the JSON,
        as it is an optional dependency.
        """
        missing = [tool for tool in self.REQUIRED_TOOLS if not shutil.which(tool)]

        split_cfg = self.config.get('settings', {}).get('split', {})
        if split_cfg.get('enabled', False) and not shutil.which('split'):
            missing.append('split')

        if missing:
            self.logger.error(
                "Required tools not found in PATH: %s. "
                "Install the corresponding packages and try again.",
                ", ".join(missing)
            )
            sys.exit(1)

        self.logger.info(
            "Dependencies OK: all required tools found in PATH."
        )

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def check_pre_flight(self):
        # 1. Check dependencies — abort immediately if anything is missing
        self.check_dependencies()

        # 2. Warn about insecure config file permissions
        self._check_config_file_permissions()

        # 3. Acquire exclusive lock to prevent concurrent runs
        self._acquire_lock()

        # 4. Check available disk space at the backup root
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
            raise Exception(
                f"Insufficient disk space. Free: {free / 1024 / 1024:.2f} MB, "
                f"required: {req_mb} MB"
            )

        self.logger.info(f"Disk OK. Free: {free / 1024 / 1024:.2f} MB")

        # 5. Create backup subdirectories (NOT the mount point — must be pre-created)
        for d in [self.incr_dir, self.diff_dir, self.full_dir]:
            if not os.path.exists(d):
                self.logger.info(f"Creating directory: {d}")
                os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------
    # CIFS mount
    # ------------------------------------------------------------------

    def mount_share(self) -> bool:
        if os.path.ismount(self.orig_dir):
            self.logger.info("Share is already mounted. Skipping mount.")
            return False

        creds    = self.config.get('credentials', {})
        user     = creds.get('username')
        password = creds.get('password')
        domain   = creds.get('domain')

        remote = self.config['paths']['remote_share']
        opts   = self.config['settings'].get('mount_options', 'ro')

        if not user or not password:
            raise ValueError("Incomplete credentials in JSON config.")

        self.logger.info(f"Mounting {remote} ...")

        auth_opts  = f"username={user},password={password}"
        if domain:
            auth_opts += f",domain={domain}"
        final_opts = f"{opts},{auth_opts}"

        cmd = ["mount", "-t", "cifs", remote, self.orig_dir, "-o", final_opts]
        try:
            self._run_cmd(cmd, check=True)
            return True
        except subprocess.CalledProcessError as exc:
            raise Exception(f"Mount failed (exit code {exc.returncode})")

    # ------------------------------------------------------------------
    # Hooks — output is captured and logged; exit code is recorded
    # ------------------------------------------------------------------

    def _run_hook(self, name: str):
        """
        Executes the shell command associated with hook 'name', if defined
        in the JSON. Unlike the original fire-and-forget approach, this
        implementation:
          - Waits for the hook to complete
          - Captures stdout and stderr and writes them to the log
          - Logs the exit code and issues a WARNING if non-zero

        The job continues regardless of the hook's result.
        """
        cmd = self.config.get('hooks', {}).get(name, '').strip()
        if not cmd:
            return

        self.logger.info(f"Hook '{name}': {cmd}")
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=300   # 5-minute safety timeout per hook
            )
            if result.stdout.strip():
                self.logger.info(f"Hook '{name}' stdout: {result.stdout.strip()}")
            if result.stderr.strip():
                self.logger.warning(f"Hook '{name}' stderr: {result.stderr.strip()}")
            if result.returncode != 0:
                self.logger.warning(
                    f"Hook '{name}' exited with code {result.returncode}. "
                    "Job continues."
                )
            else:
                self.logger.info(f"Hook '{name}' completed successfully.")
        except subprocess.TimeoutExpired:
            self.logger.warning(
                f"Hook '{name}' timed out after 300 seconds and was killed. "
                "Job continues."
            )
        except Exception as exc:
            self.logger.warning(f"Hook '{name}' raised an unexpected error: {exc}")

    # ------------------------------------------------------------------
    # Rsync
    # ------------------------------------------------------------------

    def run_rsync(self):
        """
        Runs rsync under the user configured in 'rsync_user' using 'su -c'.

        SECURITY: When building the 'su -c' command string, all individual
        rsync arguments are properly shell-quoted using shlex.quote(), which
        prevents shell injection through crafted paths or flags.

        GRACEFUL SHUTDOWN: On SIGINT (Ctrl+C), the signal is forwarded to
        the rsync child process and the script waits for it to finish before
        proceeding with unmounting. This avoids 'Broken pipe' and ensures
        rsync closes its file descriptors and releases the mountpoint cleanly.
        """
        bw_kb      = int(self.config['settings']['bandwidth_limit_mb'] * 1024)
        rsync_user = self.config['settings'].get('rsync_user', 'root')

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

        # Grant read access to the user running rsync
        os.chmod(tmp_exclude, 0o644)

        proc        = None
        interrupted = False

        try:
            # Ensure source path ends with '/' so rsync copies contents, not the dir itself
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
                self.incr_dir,
            ]

            # Build the command, using 'su -c' when rsync_user differs from the current user.
            # Each rsync argument is individually shell-quoted via shlex.quote() to prevent
            # shell injection — this is the correct POSIX-safe approach (unlike list2cmdline
            # which uses Windows-style quoting).
            current_user = os.environ.get('USER') or os.environ.get('LOGNAME') or 'root'
            if rsync_user != current_user:
                cmd_str = ' '.join(shlex.quote(p) for p in rsync_parts)
                cmd = ["su", "-", rsync_user, "-c", cmd_str]
                self.logger.info(f"Running rsync as user '{rsync_user}' ...")
            else:
                cmd = rsync_parts
                self.logger.info("Running rsync ...")

            if self.debug:
                self.logger.debug("CMD: " + ' '.join(str(x) for x in cmd))

            # Use Popen to keep a reference to the child process so we can
            # forward signals in a controlled manner.
            proc = subprocess.Popen(cmd)

            # Capture SIGINT while rsync is running.
            # Instead of raising KeyboardInterrupt immediately, forward the signal
            # to the child and wait for it to finish — avoiding broken pipes and
            # umount with a busy mountpoint.
            original_sigint = signal.getsignal(signal.SIGINT)

            def _handle_sigint(signum, frame):
                nonlocal interrupted
                interrupted = True
                self.logger.warning(
                    "Interrupt received (Ctrl+C). Waiting for rsync to finish "
                    "gracefully before unmounting ..."
                )
                if proc and proc.poll() is None:
                    proc.send_signal(signal.SIGINT)  # Forward to rsync child

            signal.signal(signal.SIGINT, _handle_sigint)

            try:
                proc.wait()  # Block until rsync finishes (including after SIGINT)
            finally:
                # Always restore the original handler
                signal.signal(signal.SIGINT, original_sigint)

            returncode = proc.returncode

            if interrupted:
                # rsync received SIGINT and exited with code 20 — expected exit
                self.logger.warning(
                    f"Rsync interrupted by user (code {returncode}). "
                    "Proceeding to safe unmount."
                )
                raise KeyboardInterrupt
            elif returncode == 0:
                self.logger.info("Rsync completed successfully.")
                self._run_hook("after_rsync")
            elif returncode == 23:
                self.logger.warning(
                    "Rsync warning (code 23) — partial transfer "
                    "(e.g. permission denied on some files). Continuing."
                )
                self._run_hook("after_rsync")
            else:
                raise subprocess.CalledProcessError(returncode, cmd)

        finally:
            if os.path.exists(tmp_exclude):
                os.remove(tmp_exclude)

    # ------------------------------------------------------------------
    # Differential cleanup
    # ------------------------------------------------------------------

    def cleanup_differential(self):
        policy = self.config['settings'].get('retention_policy', {})
        days   = policy.get('keep_differential_files_days', 240)

        self.logger.info(f"Cleaning Differential files older than {days} day(s) ...")
        self._run_cmd(
            ["find", self.diff_dir, "-type", "f", "-mtime", f"+{days}", "-delete"],
            check=False
        )

        if policy.get('cleanup_empty_dirs', True):
            # "-mindepth 1" protects the root directory from being removed
            # when it becomes empty — only orphaned subdirectories are deleted.
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
    # Full backup
    # ------------------------------------------------------------------

    def run_full_backup(self):
        policy    = self.config['settings'].get('retention_policy', {})
        retention = policy.get('keep_full_backups_days', 30)

        self.logger.info(f"Removing Full backups older than {retention} day(s) ...")
        self._run_cmd(
            ["find", self.full_dir, "-type", "f", "-name", "Full_*.tar.zst",
             "-mtime", f"+{retention}", "-delete"],
            check=False
        )

        # Check whether a valid Full exists (within the retention window)
        self.logger.info(f"Checking for a valid Full backup (< {retention} day(s) old) ...")
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
                f"Valid Full backup found ({recent_file}). Keeping current structure."
            )
            self._run_hook("after_full")
            return

        # ------------------------------------------------------------------
        # Dynamic space check based on actual INCR_DIR size + 10% margin
        # ------------------------------------------------------------------
        self.logger.info("Calculating space needed for Full (INCR_DIR size + 10%) ...")
        du_res = self._run_cmd(
            ["du", "-s", "--block-size=1K", self.incr_dir],
            check=True, capture_output=True, text=True
        )
        incr_size_kb = int(du_res.stdout.split()[0])
        min_space_kb = incr_size_kb + incr_size_kb // 10   # +10% margin
        min_space_mb = min_space_kb / 1024

        df_res = self._run_cmd(
            ["df", "--output=avail", self.full_dir],
            check=True, capture_output=True, text=True
        )
        avail_kb = int(df_res.stdout.strip().splitlines()[-1])

        self.logger.info(
            f"INCR_DIR: {incr_size_kb} KB | Required (+10%): {min_space_kb} KB "
            f"| Available in FULL_DIR: {avail_kb} KB"
        )

        if avail_kb < min_space_kb:
            raise Exception(
                f"Insufficient space in {self.full_dir}. "
                f"Required: {min_space_mb:.1f} MB, "
                f"Available: {avail_kb / 1024:.1f} MB"
            )

        # Reflink snapshot (Copy-on-Write), fallback to regular copy
        persistent_full_dir = os.path.join(self.full_dir, "Full")
        if os.path.exists(persistent_full_dir):
            self.logger.info(
                f"Removing old snapshot '{persistent_full_dir}' before update ..."
            )
            shutil.rmtree(persistent_full_dir)

        self.logger.info("Creating new snapshot 'Full' (Reflink / CoW) ...")
        try:
            self._run_cmd(
                ["cp", "-a", "--reflink=always", self.incr_dir, persistent_full_dir],
                check=True, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError:
            self.logger.warning("Reflink not supported on this filesystem. Falling back to cp -a.")
            self._run_cmd(["cp", "-a", self.incr_dir, persistent_full_dir], check=True)

        # Compression pipeline: tar | pv | zstd
        filename  = f"Full_{self.date_str}.tar.zst"
        zst_path  = os.path.join(self.full_dir, filename)

        ionice_class  = str(self.config['settings']['ionice_class'])
        nice_priority = str(self.config['settings']['nice_priority'])
        transfer_rate = self.config['settings'].get('transfer_rate_pv', '10m')

        self.logger.info(f"Compressing: {filename}")

        cmd_tar  = [
            "ionice", "-c", ionice_class,
            "nice", "-n", nice_priority,
            "tar", "-cvf", "-",
            "-C", self.full_dir,
            "Full"
        ]
        cmd_pv   = ["pv", "-q", "-L", transfer_rate]
        cmd_zstd = ["zstd", "--threads=2"]

        if self.debug:
            self.logger.debug(
                f"Pipeline: {' '.join(cmd_tar)} | {' '.join(cmd_pv)} "
                f"| {' '.join(cmd_zstd)} > {zst_path}"
            )

        # Verify exit codes of ALL pipeline processes (equivalent to bash 'pipefail')
        try:
            p_tar  = subprocess.Popen(cmd_tar, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p_pv   = subprocess.Popen(cmd_pv,  stdin=p_tar.stdout, stdout=subprocess.PIPE)
            p_tar.stdout.close()   # Allows p_tar to receive SIGPIPE if p_pv exits

            with open(zst_path, "wb") as f_out:
                p_zstd = subprocess.Popen(cmd_zstd, stdin=p_pv.stdout, stdout=f_out)
            p_pv.stdout.close()    # Allows p_pv to receive SIGPIPE if p_zstd exits

            p_zstd.wait()
            p_pv.wait()
            p_tar.wait()

            errors = []
            if p_tar.returncode  != 0: errors.append(f"tar  (code {p_tar.returncode})")
            if p_pv.returncode   != 0: errors.append(f"pv   (code {p_pv.returncode})")
            if p_zstd.returncode != 0: errors.append(f"zstd (code {p_zstd.returncode})")

            if errors:
                raise Exception(f"Compression pipeline failed: {', '.join(errors)}")

            zst_size_kb = int(
                self._run_cmd(
                    ["du", "-s", "--block-size=1K", zst_path],
                    check=True, capture_output=True, text=True
                ).stdout.split()[0]
            )
            self.logger.info(
                f"Full backup complete. File: {zst_path} ({zst_size_kb} KB). "
                f"Snapshot directory 'Full' kept on disk."
            )

        except Exception as exc:
            # Remove partial file on error
            if os.path.exists(zst_path):
                os.remove(zst_path)
                self.logger.warning(f"Partial file '{zst_path}' removed.")
            self.logger.error(f"Compression error: {exc}")
            raise

        # Run split immediately after compression if enabled
        split_cfg = self.config['settings'].get('split', {})
        if split_cfg.get('enabled', False):
            self._run_split(zst_path, split_cfg)
            self._run_hook("after_split")

        self._run_hook("after_full")

    # ------------------------------------------------------------------
    # Split of the compressed Full file
    # ------------------------------------------------------------------

    def _parse_chunk_size(self, chunk_size_str: str) -> tuple:
        """
        Converts a chunk size string (e.g. '4gb', '500mb', '2tb')
        into (bytes: int, split_unit: str).

        split_unit is the suffix accepted by the split command: 'M', 'G' or 'T'.
        Accepted units: mb, gb, tb (case-insensitive).
        Raises ValueError with a clear message for any invalid value.
        """
        UNITS = {
            'mb': (1024 ** 2, 'M'),
            'gb': (1024 ** 3, 'G'),
            'tb': (1024 ** 4, 'T'),
        }
        raw = str(chunk_size_str).strip().lower()
        for suffix, (multiplier, split_letter) in UNITS.items():
            if raw.endswith(suffix):
                num_str = raw[: -len(suffix)].strip()
                try:
                    num = float(num_str)
                except ValueError:
                    raise ValueError(
                        f"Invalid numeric value in chunk_size: '{chunk_size_str}'. "
                        f"Expected e.g.: '4gb', '500mb', '2tb'."
                    )
                if num <= 0:
                    raise ValueError(
                        f"chunk_size must be greater than zero, got: '{chunk_size_str}'."
                    )
                chunk_bytes = int(num * multiplier)
                num_clean   = int(num) if num == int(num) else num
                split_str   = f"{num_clean}{split_letter}"
                return chunk_bytes, split_str

        raise ValueError(
            f"Unknown unit in chunk_size: '{chunk_size_str}'. "
            f"Use 'mb', 'gb' or 'tb' (e.g. '4gb', '500mb')."
        )

    def _run_split(self, zst_path: str, split_cfg: dict):
        """
        Splits the .tar.zst file into parts as configured in chunk_size.

        Automatically calculated parameters:
          - num_parts  = ceil(file_size / chunk_bytes)
          - suffix -a  = max(3, len(str(num_parts)))  → minimum 3 digits
          - --numeric-suffixes=1                       → always starts at 001

        Retry: up to 3 attempts. The splitted/ directory is cleaned before
        each attempt.
        Validation: sum of part sizes must equal the original .tar.zst size.
        """
        MAX_RETRIES   = 3
        keep_original = split_cfg.get('keep_original_after_split', True)

        # Parse and validate chunk_size before any operation.
        # No silent fallback — a missing field is an explicit error to prevent
        # running split with an unexpected default value.
        if 'chunk_size' not in split_cfg:
            raise Exception(
                "Invalid split configuration: 'chunk_size' field missing in JSON. "
                "Example: \"chunk_size\": \"4gb\""
            )
        chunk_size_raw = split_cfg['chunk_size']
        try:
            chunk_bytes, chunk_str = self._parse_chunk_size(chunk_size_raw)
        except ValueError as exc:
            raise Exception(f"Invalid split configuration: {exc}")

        zst_size   = os.path.getsize(zst_path)
        num_parts  = math.ceil(zst_size / chunk_bytes)
        suffix_len = max(3, len(str(num_parts)))

        split_dir = os.path.join(self.full_dir, "splitted")
        prefix    = os.path.join(split_dir, os.path.basename(zst_path) + ".part_")

        self.logger.info(
            f"Split enabled. File: {os.path.basename(zst_path)} "
            f"({zst_size / 1024 ** 3:.2f} GB) → {num_parts} part(s) of "
            f"{chunk_size_raw.upper()} (suffix: {suffix_len} digit(s))."
        )

        def _prepare_split_dir():
            """Creates or cleans the splitted/ directory before each attempt."""
            if os.path.exists(split_dir):
                self.logger.info(f"Cleaning '{split_dir}' before split ...")
                for entry in os.scandir(split_dir):
                    try:
                        os.remove(entry.path)
                    except OSError as exc:
                        self.logger.warning(f"Could not remove '{entry.path}': {exc}")
            else:
                self.logger.info(f"Creating '{split_dir}' ...")
                os.makedirs(split_dir, exist_ok=True)

        def _validate_parts() -> bool:
            """Compares the sum of part sizes to the original file size."""
            try:
                parts = sorted(
                    entry.path for entry in os.scandir(split_dir)
                    if entry.is_file()
                )
                if not parts:
                    self.logger.warning("Validation: no parts found.")
                    return False
                total = sum(os.path.getsize(p) for p in parts)
                if total != zst_size:
                    self.logger.warning(
                        f"Validation failed: sum of parts ({total} bytes) "
                        f"≠ original ({zst_size} bytes)."
                    )
                    return False
                self.logger.info(
                    f"Validation OK: {len(parts)} part(s), "
                    f"{total / 1024 ** 3:.2f} GB total."
                )
                return True
            except Exception as exc:
                self.logger.warning(f"Error during validation: {exc}")
                return False

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            self.logger.info(f"Split — attempt {attempt}/{MAX_RETRIES} ...")
            _prepare_split_dir()

            cmd = [
                "split",
                f"--bytes={chunk_str}",
                "--numeric-suffixes=1",
                "-a", str(suffix_len),
                "--verbose",
                zst_path,
                prefix,
            ]

            try:
                self._run_cmd(cmd, check=True)
            except subprocess.CalledProcessError as exc:
                self.logger.warning(
                    f"Split returned error (code {exc.returncode}) on attempt {attempt}."
                )
                continue   # process failed → next attempt

            # Process exited with 0 — validate part integrity
            if _validate_parts():
                success = True
                break
            # Validation failed → next attempt (split_dir will be cleaned at loop start)

        if not success:
            # Exhausted retries — clean up corrupted parts and preserve the .tar.zst
            self.logger.error(
                f"Split failed after {MAX_RETRIES} attempts. "
                f"Parts removed. Original file '{os.path.basename(zst_path)}' preserved."
            )
            _prepare_split_dir()   # leave directory empty but existing
            raise Exception(
                f"Split of '{zst_path}' did not complete after {MAX_RETRIES} attempts."
            )

        # ------------------------------------------------------------------
        # Post-split — decide whether to keep or remove the original
        # ------------------------------------------------------------------
        if not keep_original:
            self.logger.info(
                f"keep_original_after_split=false — removing '{os.path.basename(zst_path)}' ..."
            )
            try:
                os.remove(zst_path)
                self.logger.info("Original file removed.")
            except OSError as exc:
                self.logger.warning(f"Could not remove original file: {exc}")
        else:
            self.logger.info(
                f"keep_original_after_split=true — '{os.path.basename(zst_path)}' kept."
            )

    # ------------------------------------------------------------------
    # Log cleanup — retention by count (keep_logs_count)
    # ------------------------------------------------------------------

    def cleanup_logs(self):
        policy     = self.config['settings'].get('retention_policy', {})
        keep_count = policy.get('keep_logs_count', 31)
        log_dir    = self.config['paths']['log_dir']

        self.logger.info(
            f"Cleaning logs for job '{self.safe_name}', keeping the {keep_count} most recent ..."
        )

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
            self.logger.warning(f"Log directory '{log_dir}' not found. Skipping cleanup.")
            return

        # Exclude the current log file from deletion candidates to avoid
        # removing the active log in edge-case timing scenarios.
        candidates = [p for p in all_logs if os.path.abspath(p) != os.path.abspath(self.log_file)]
        logs_to_delete = candidates[keep_count:]

        for log_path in logs_to_delete:
            try:
                os.remove(log_path)
                self.logger.info(f"Log removed: {log_path}")
            except OSError as exc:
                self.logger.warning(f"Could not remove '{log_path}': {exc}")

    # ------------------------------------------------------------------
    # Final cleanup
    # ------------------------------------------------------------------

    def cleanup(self, did_mount: bool):
        """Unmounts the share (if mounted by this run) and releases the lock."""
        if did_mount:
            self.logger.info("Unmounting share ...")
            result = self._run_cmd(["umount", self.orig_dir], check=False)
            if result.returncode != 0:
                self.logger.warning(
                    f"umount exited with code {result.returncode}. "
                    "The share may still be mounted — verify manually."
                )
            else:
                self.logger.info("Share unmounted successfully.")

        self._release_lock()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Corporate Backup Manager (CIFS → Local)",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "config_file", nargs="?",
        help="Path to the JSON configuration file"
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Create a default configuration template"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print executed commands to the log for diagnostics"
    )
    args = parser.parse_args()

    if args.init:
        target = args.config_file if args.config_file else "config_template.json"
        if os.path.exists(target):
            print(f"[\033[93mWARNING\033[0m] '{target}' already exists. Aborting to avoid overwrite.")
            sys.exit(1)
        with open(target, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        os.chmod(target, 0o600)
        print(f"Template created: {target} (chmod 600 applied)")
        sys.exit(0)

    if not args.config_file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.config_file):
        print(f"[\033[93mWARNING\033[0m] '{args.config_file}' not found.")
        print("Creating default template ...")
        with open(args.config_file, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        os.chmod(args.config_file, 0o600)
        print("File created (chmod 600). Edit it and try again.")
        sys.exit(0)

    job       = None
    did_mount = False
    try:
        job = BackupJob(args.config_file, debug=args.debug)
        job.logger.info(f"=== Job started: {args.config_file} ===")
        if args.debug:
            job.logger.info("DEBUG MODE ENABLED")

        job.check_pre_flight()
        did_mount = job.mount_share()

        job.run_rsync()
        job.cleanup_differential()
        job.run_full_backup()
        job.cleanup_logs()

        job.logger.info("=== Job completed successfully ===")

    except KeyboardInterrupt:
        if job:
            job.logger.warning("=== Job interrupted by user (Ctrl+C) ===")
        else:
            print("\n[WARNING] Interrupted by user.")
        sys.exit(130)
    except Exception as exc:
        if job:
            job.logger.error(f"FAILURE: {exc}")
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        if job:
            job.cleanup(did_mount)


if __name__ == "__main__":
    main()