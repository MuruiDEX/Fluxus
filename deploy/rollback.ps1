#Requires -Version 5.1
<#
.SYNOPSIS
  Conservative application-tier rollback for the Fluxus production Compose stack.

.DESCRIPTION
  Rolls back ONLY the backend and frontend containers to explicitly supplied,
  already-present image references. It never rebuilds, never migrates, never
  runs a schema downgrade, and never restores or drops a database.

  This is intentionally NOT a one-click database rollback. A database restore
  over live data requires separate, informed operator authorization and the
  manual sequence documented in docs/deployment.md (restore a verified backup
  to a scratch database first, verify it, then restore live or repoint
  APP_DATABASE_URL). This script stops before that boundary.

  Safety properties:
  - Requires -Yes.
  - Requires an explicit previous backend image and previous frontend image.
  - Requires an existing, non-empty backup archive and proves it is readable
    with `pg_restore --list` (this does NOT prove a full restore).
  - Rejects the current compose tags, `latest`, empty values, and malformed
    image references.
  - Requires deploy/prod.env to pass preflight and Compose config validation.
  - Recreates only `backend` and `frontend` with `--no-deps`; Postgres, Caddy,
    volumes, migrations, and data are untouched.
  - Never prints secret VALUES: only image refs, paths, and check names.

  Exit codes: 0 = app-tier rollback completed and containers healthy;
  1 = usage/confirmation problem; 2 = invalid input/config/archive;
  3 = required container not found; 4 = Docker daemon unavailable;
  5 = container recreation or health check failed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy/rollback.ps1 `
    -PreviousApiImage fluxus-api:0.1.0-20261001 `
    -PreviousWebImage fluxus-web:live-20261001 `
    -BackupFile D:\fluxus-backups\fluxus-prod-muse-20261021-120000.dump -Yes
#>
[CmdletBinding()]
param(
  [string]$EnvFile = '',
  [string]$BackupFile = '',
  [string]$PreviousApiImage = '',
  [string]$PreviousWebImage = '',
  [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$script:ExitCode = 0

function Write-Fail {
  param([int]$Code, [string]$Message)
  [Console]::Error.WriteLine("rollback FAILED: $Message")
  if ($Code -gt $script:ExitCode) { $script:ExitCode = $Code }
}

function Write-Step {
  param([string]$Message)
  [Console]::Out.WriteLine("rollback: $Message")
}

# --- 0. Explicit confirmation before touching the app tier. ---
if (-not $Yes) {
  Write-Fail -Code 1 -Message 'Refusing to roll back without -Yes. Re-run after reviewing the previous images and verified backup.'
  exit $script:ExitCode
}

# --- 1. Resolve and validate the deployment inputs. ---
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $EnvFile = Join-Path $PSScriptRoot 'prod.env'
}
$ComposeFile = Join-Path $PSScriptRoot 'docker-compose.prod.yml'
$Preflight = Join-Path $PSScriptRoot 'preflight.ps1'
$CaddyFile = Join-Path $PSScriptRoot 'Caddyfile'

foreach ($requiredFile in @($ComposeFile, $Preflight, $CaddyFile)) {
  if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
    Write-Fail -Code 1 -Message "required deployment file '$requiredFile' is missing."
  }
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
  Write-Fail -Code 1 -Message ("prod.env not found at '$EnvFile'. Never roll back without a real, preflighted environment file.")
  exit $script:ExitCode
}
$EnvFile = (Resolve-Path -LiteralPath $EnvFile).Path

if ([string]::IsNullOrWhiteSpace($BackupFile)) {
  Write-Fail -Code 1 -Message 'A verified backup is required: pass -BackupFile <path>.'
} elseif (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
  Write-Fail -Code 1 -Message "backup file '$BackupFile' does not exist."
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }
$BackupItem = Get-Item -LiteralPath $BackupFile
if ($BackupItem.Length -le 0) {
  Write-Fail -Code 2 -Message 'the backup file is empty; refusing to treat it as verified.'
  exit $script:ExitCode
}
$BackupFile = $BackupItem.FullName

$imagePattern = '^[A-Za-z0-9][A-Za-z0-9._/:@-]*$'
foreach ($item in @(
  @{ Name = 'PreviousApiImage'; Value = $PreviousApiImage; ComposeTag = 'fluxus-api:prod' },
  @{ Name = 'PreviousWebImage'; Value = $PreviousWebImage; ComposeTag = 'fluxus-web:live-prod' }
)) {
  $value = [string]$item.Value
  if ([string]::IsNullOrWhiteSpace($value)) {
    Write-Fail -Code 2 -Message ("{0} is required (an explicit previous image reference, never a guess)." -f $item.Name)
    continue
  }
  if ($value -notmatch $imagePattern) {
    Write-Fail -Code 2 -Message ("{0} contains characters this script will not pass to Docker/Compose." -f $item.Name)
    continue
  }
  if ($value -match '(^|:)latest$') {
    Write-Fail -Code 2 -Message ("{0} must be a pinned version or digest, not 'latest'." -f $item.Name)
  }
  if ($value -eq $item.ComposeTag) {
    Write-Fail -Code 2 -Message ("{0} equals the current production compose tag ('{1}'); that is not a rollback target." -f $item.Name, $item.ComposeTag)
  }
}
if ($PreviousApiImage -eq $PreviousWebImage) {
  Write-Fail -Code 2 -Message 'PreviousApiImage and PreviousWebImage must identify their respective images.'
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }

# --- 2. Docker must be available before any state-changing command. ---
& docker info --format '{{.ServerVersion}}' > $null 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Fail -Code 4 -Message 'Docker daemon is unreachable. Start Docker Desktop/server, then re-run.'
  exit $script:ExitCode
}

# --- 3. The environment must still pass the read-only preflight. ---
& powershell -NoProfile -ExecutionPolicy Bypass -File $Preflight -EnvFile $EnvFile -SkipPortCheck
if ($LASTEXITCODE -ne 0) {
  Write-Fail -Code 2 -Message 'preflight rejected the environment. Fix prod.env before rolling back.'
  exit $script:ExitCode
}

Push-Location $PSScriptRoot
try {
  & docker compose --env-file $EnvFile -f $ComposeFile config --quiet 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Fail -Code 2 -Message 'docker compose config --quiet failed for the production Compose file.'
    exit $script:ExitCode
  }
} finally {
  Pop-Location
}
Write-Step 'Production Compose configuration is valid.'

# --- 4. Both previous images must already exist locally. ---
Write-Step "Backend rollback image: $PreviousApiImage"
Write-Step "Frontend rollback image: $PreviousWebImage"
foreach ($image in @($PreviousApiImage, $PreviousWebImage)) {
  & docker image inspect $image > $null 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Fail -Code 2 -Message "image '$image' is not present locally. Pull/tag it explicitly, then re-run; this script never pulls or builds."
  }
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }

# --- 5. The backup must be a readable custom-format archive. ---
# Read-only inspection only. This does NOT prove the backup restores cleanly,
# and this script never restores it.
& docker run --rm --mount "type=bind,source=$BackupFile,target=/backup/rollback-check.dump,readonly" postgres:16-alpine pg_restore --list /backup/rollback-check.dump > $null 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Fail -Code 2 -Message "pg_restore --list could not read '$BackupFile'. Refusing to rely on an unreadable backup."
  exit $script:ExitCode
}
Write-Step 'Backup archive header is readable (pg_restore --list; full restore NOT proven).'

# --- 6. The stack must have its existing Postgres container. ---
Push-Location $PSScriptRoot
try {
  $postgresId = (& docker compose --env-file $EnvFile -f $ComposeFile ps -q postgres 2>$null | Select-Object -First 1)
} finally {
  Pop-Location
}
if ([string]::IsNullOrWhiteSpace($postgresId)) {
  Write-Fail -Code 3 -Message 'no production postgres container found. This is not a rollback target; investigate before touching anything.'
  exit $script:ExitCode
}
Write-Step 'Existing postgres container found; it will not be stopped, recreated, or restored.'

# --- 7. Recreate ONLY backend + frontend from the previous images. ---
$override = Join-Path ([System.IO.Path]::GetTempPath()) ("fluxus-rollback-" + [guid]::NewGuid().ToString('N') + ".yml")
$overrideBody = @"
services:
  backend:
    image: $PreviousApiImage
  frontend:
    image: $PreviousWebImage
"@
Set-Content -LiteralPath $override -Value $overrideBody -Encoding UTF8

try {
  Push-Location $PSScriptRoot
  try {
    Write-Step 'Recreating backend and frontend with previous images (no deps, no build, no migration).'
    & docker compose --env-file $EnvFile -f $ComposeFile -f $override up -d --no-build --no-deps --force-recreate backend frontend
    if ($LASTEXITCODE -ne 0) {
      Write-Fail -Code 5 -Message 'docker compose could not recreate the app tier with the previous images.'
      exit $script:ExitCode
    }
  } finally {
    Pop-Location
  }
} finally {
  Remove-Item -LiteralPath $override -Force -ErrorAction SilentlyContinue
}

# --- 8. Wait for the image healthchecks (bounded; never an infinite hang). ---
$deadline = (Get-Date).AddSeconds(120)
$healthy = $false
while ((Get-Date) -lt $deadline) {
  Push-Location $PSScriptRoot
  try {
    $ids = @(& docker compose --env-file $EnvFile -f $ComposeFile ps -q backend frontend 2>$null)
  } finally {
    Pop-Location
  }
  if ($ids.Count -eq 2 -and -not ($ids | Where-Object { [string]::IsNullOrWhiteSpace($_) })) {
    $states = @()
    foreach ($id in $ids) {
      $state = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $id 2>$null)
      $states += [string]$state
    }
    if (($states | Where-Object { $_ -ne 'healthy' }).Count -eq 0) {
      $healthy = $true
      break
    }
  }
  Start-Sleep -Seconds 3
}

if (-not $healthy) {
  Write-Fail -Code 5 -Message 'app tier did not become healthy within 120s. Inspect `docker compose ps` and logs; do not restore the database blindly.'
  exit $script:ExitCode
}
Write-Step 'Backend and frontend report healthy on the previous images.'

# --- 9. Separate automated actions from the manual database procedure. ---
[Console]::Out.WriteLine('')
[Console]::Out.WriteLine('AUTOMATED AND COMPLETED')
[Console]::Out.WriteLine('  - validated prod.env, Compose, previous images, and backup archive header')
[Console]::Out.WriteLine('  - recreated backend + frontend from the previous images')
[Console]::Out.WriteLine('  - waited for both image healthchecks to report healthy')
[Console]::Out.WriteLine('')
[Console]::Out.WriteLine('MANUAL AND NOT PERFORMED BY THIS SCRIPT')
[Console]::Out.WriteLine('  Database restore is intentionally not automated. No database was restored,')
[Console]::Out.WriteLine('  dropped, recreated, or migrated, and no schema downgrade was run.')
[Console]::Out.WriteLine('  1. Stop the backend before any database work:')
[Console]::Out.WriteLine('       docker compose --env-file deploy/prod.env -f deploy/docker-compose.prod.yml stop backend')
[Console]::Out.WriteLine('  2. Restore the verified backup to a scratch database FIRST and check schema,')
[Console]::Out.WriteLine('     representative rows, constraints, and `alembic current`.')
[Console]::Out.WriteLine('  3. Only with explicit authorization, restore live or create a fresh database')
[Console]::Out.WriteLine('     and repoint APP_DATABASE_URL. Never drop/recreate the live database blindly.')
[Console]::Out.WriteLine('  4. Start backend/frontend and run deploy/verify.ps1 against the public origin.')
[Console]::Out.WriteLine('  See docs/deployment.md sections 11, 12, and 14.5 before proceeding.')
[Console]::Out.WriteLine('')
[Console]::Out.WriteLine('rollback: application tier rolled back; database recovery remains manual.')
exit $script:ExitCode
