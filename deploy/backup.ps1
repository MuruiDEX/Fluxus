#Requires -Version 5.1
<#
.SYNOPSIS
  Timestamped PostgreSQL backup for the Fluxus production Compose stack.

.DESCRIPTION
  Dumps the production Compose `postgres` service with `pg_dump -Fc` to a
  uniquely named file in a configurable backup directory, then verifies the
  file is non-empty and readable via `pg_restore --list`.

  Safety properties:
  - NEVER runs as part of preflight or deployment: requires explicit -Yes.
  - NEVER restores, deletes, or overwrites anything.
  - NEVER prints secret values (only variable names and the backup path).
  - No password ever appears on a command line: the dump runs inside the
    postgres container over its local socket, and compose receives secrets
    through --env-file only.
  - `pg_restore --list` proves the file is a readable archive header set,
    NOT a complete restorable backup. Test restores on a scratch database
    before relying on any file (see docs/deployment.md section 12).

  Exit codes: 0 = ok; 1 = missing files/usage/confirmation absent;
  2 = bad config, placeholders, or invalid Compose file;
  3 = database unavailable; 4 = Docker daemon down; 5 = dump/verify failed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy/backup.ps1 -Yes
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy/backup.ps1 -Yes -BackupDir D:\fluxus-backups
#>
[CmdletBinding()]
param(
  [string]$EnvFile = '',
  [string]$BackupDir = '',
  [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$script:ExitCode = 0

function Write-Fail {
  param([int]$Code, [string]$Message)
  [Console]::Error.WriteLine("backup FAILED: $Message")
  if ($Code -gt $script:ExitCode) { $script:ExitCode = $Code }
}

function Write-Info {
  param([string]$Message)
  [Console]::Out.WriteLine("backup: $Message")
}

# --- 0. Resolve defaults next to this script (works with -File invocation). ---
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $EnvFile = Join-Path $PSScriptRoot 'prod.env'
}
if ([string]::IsNullOrWhiteSpace($BackupDir)) {
  $BackupDir = Join-Path $PSScriptRoot 'backups'
}
$ComposeFile = Join-Path $PSScriptRoot 'docker-compose.prod.yml'
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
  Write-Fail -Code 1 -Message 'deploy/docker-compose.prod.yml not found next to this script.'
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
  Write-Fail -Code 1 -Message ("prod.env not found at '$EnvFile'. Copy deploy/prod.env.example to deploy/prod.env and fill in real values (never commit prod.env).")
  exit $script:ExitCode
}

# --- 1. Parse KEY=VALUE (comments/blank lines skipped; quotes stripped). ---
$values = @{}
$lineNo = 0
foreach ($raw in (Get-Content -LiteralPath $EnvFile)) {
  $lineNo += 1
  $t = $raw.Trim()
  if ($t -eq '' -or $t.StartsWith('#')) { continue }
  if ($t.StartsWith('export ')) { $t = $t.Substring(7).Trim() }
  $idx = $t.IndexOf('=')
  if ($idx -lt 1) {
    Write-Fail -Code 2 -Message "prod.env line $lineNo is malformed (expected KEY=VALUE)."
    continue
  }
  $key = $t.Substring(0, $idx).Trim()
  $val = $t.Substring($idx + 1).Trim()
  if ($val.Length -ge 2 -and (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'")))) {
    $val = $val.Substring(1, $val.Length - 2)
  }
  $values[$key] = $val
}

# Only the database trio is required here; the dump goes straight at the
# postgres service, not through APP_DATABASE_URL.
$Required = @('POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD')
foreach ($name in $Required) {
  if (-not $values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($values[$name])) {
    Write-Fail -Code 2 -Message "$name is missing or blank in prod.env (see deploy/prod.env.example)."
  }
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }

# --- 2. Placeholders must be gone (example template markers, never real). ---
if ($values['POSTGRES_PASSWORD'] -match 'CHANGE_ME') {
  Write-Fail -Code 2 -Message 'POSTGRES_PASSWORD still holds the template placeholder. Generate a fresh random password.'
}
if ($values['POSTGRES_PASSWORD'].Length -lt 16) {
  Write-Fail -Code 2 -Message 'POSTGRES_PASSWORD is shorter than 16 characters. Use a long random password.'
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }

# --- 3. Explicit confirmation: backups touch production data paths. ---
if (-not $Yes) {
  Write-Fail -Code 1 -Message 'Refusing to back up without -Yes. Re-run with -Yes to confirm you intend to read the production database.'
  exit $script:ExitCode
}

# --- 4. Docker daemon must answer (read-only probe, output suppressed). ---
& docker info --format '{{.ServerVersion}}' > $null 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Fail -Code 4 -Message 'Docker daemon is unreachable. Start Docker Desktop, then re-run backup.'
  exit $script:ExitCode
}

# --- 5. Compose file must be valid before anything runs. ---
Push-Location $PSScriptRoot
try {
  & docker compose --env-file $EnvFile -f docker-compose.prod.yml config --quiet 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Fail -Code 2 -Message 'docker compose config --quiet failed for deploy/docker-compose.prod.yml with this prod.env. Fix the configuration first (see preflight).'
    exit $script:ExitCode
  }
} finally {
  Pop-Location
}
Write-Info 'Compose configuration is valid.'

# --- 6. postgres service must be up (no auto-start: operator decides). ---
Push-Location $PSScriptRoot
try {
  $cid = (& docker compose --env-file $EnvFile -f docker-compose.prod.yml ps -q postgres 2>$null | Select-Object -First 1)
} finally {
  Pop-Location
}
if ([string]::IsNullOrWhiteSpace($cid)) {
  Write-Fail -Code 3 -Message 'postgres service is not running in the production Compose stack. Start it explicitly (see docs/deployment.md section 14.4), then re-run backup. This script never starts services.'
  exit $script:ExitCode
}
Write-Info 'postgres service is running.'

# --- 7. Prepare a unique backup path (never overwrite, never prune). ---
try {
  if (-not (Test-Path -LiteralPath $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force > $null
  }
} catch {
  Write-Fail -Code 5 -Message "Cannot create backup directory '$BackupDir'."
  exit $script:ExitCode
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dbName = $values['POSTGRES_DB']
$BackupPath = Join-Path $BackupDir ("fluxus-prod-{0}-{1}.dump" -f $dbName, $stamp)
if (Test-Path -LiteralPath $BackupPath) {
  Write-Fail -Code 5 -Message "Backup path '$BackupPath' already exists; refusing to overwrite. Wait a second and re-run."
  exit $script:ExitCode
}

# --- 8. Dump via `docker compose exec -T postgres` over the local socket. ---
# No password on any command line: pg_dump runs as the postgres superuser
# inside the container (trust over the loopback socket), while compose gets
# secrets via --env-file only. Stderr stays visible; stdout is the archive.
Write-Info "Writing backup to '$BackupPath' ..."
Push-Location $PSScriptRoot
try {
  & docker compose --env-file $EnvFile -f docker-compose.prod.yml exec -T postgres pg_dump -Fc -U $values['POSTGRES_USER'] -d $values['POSTGRES_DB'] > $BackupPath 2>$null
  $dumpExit = $LASTEXITCODE
} finally {
  Pop-Location
}
if ($dumpExit -ne 0 -or -not (Test-Path -LiteralPath $BackupPath)) {
  if (Test-Path -LiteralPath $BackupPath) { Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue }
  Write-Fail -Code 5 -Message 'pg_dump failed or produced no file. Partial output (if any) was deleted; nothing else was touched. Check `docker compose logs postgres` and re-run.'
  exit $script:ExitCode
}
$item = Get-Item -LiteralPath $BackupPath
if ($item.Length -eq 0) {
  Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
  Write-Fail -Code 5 -Message 'pg_dump produced an empty file. Empty output was deleted; the database was not modified.'
  exit $script:ExitCode
}
Write-Info ("Dump complete: {0} bytes." -f $item.Length)

# --- 9. Structural check only: `pg_restore --list` must parse the archive. ---
# This proves the file is a readable custom-format archive, NOT that a full
# restore will succeed. Always drill restores on a scratch database first
# (docs/deployment.md section 12). --mount handles Windows drive paths and
# spaces better than the legacy -v parser.
$BackupPath = (Resolve-Path -LiteralPath $BackupPath).Path
& docker run --rm --mount "type=bind,source=$BackupPath,target=/backup/restore-check.dump,readonly" postgres:16-alpine pg_restore --list /backup/restore-check.dump > $null 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Fail -Code 5 -Message "pg_restore --list could not read '$BackupPath'. The file is kept for inspection but must NOT be trusted; re-run backup."
  exit $script:ExitCode
}
Write-Info 'Archive header check passed (pg_restore --list). This does NOT prove a full restore - drill restores on scratch first.'
Write-Info ("Backup ready: '$BackupPath'. Keep it with restrictive permissions; never commit it.")
exit $script:ExitCode
