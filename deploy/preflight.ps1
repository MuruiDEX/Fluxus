#Requires -Version 5.1
<#
.SYNOPSIS
  Production preflight for the Fluxus Compose stack (Stage 17).

.DESCRIPTION
  Read-only safety gate. Validates deploy/prod.env against
  deploy/docker-compose.prod.yml WITHOUT starting services, touching DNS,
  changing firewall rules, running migrations, or deleting data. Safe to
  run repeatedly. Never prints secret values - only variable NAMES appear
  in output.

  Exit codes: 0 = pass; 1 = missing files/usage; 2 = config/placeholder
  problem; 3 = required port occupied; 4 = Docker daemon unavailable.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy/preflight.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy/preflight.ps1 -EnvFile C:\ops\prod.env
#>
[CmdletBinding()]
param(
  [string]$EnvFile = '',
  [switch]$SkipPortCheck,
  [switch]$SkipDockerCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$script:ExitCode = 0

function Write-Fail {
  param([int]$Code, [string]$Message)
  [Console]::Error.WriteLine("preflight FAILED: $Message")
  if ($Code -gt $script:ExitCode) { $script:ExitCode = $Code }
}

function Write-Info {
  param([string]$Message)
  [Console]::Out.WriteLine("preflight: $Message")
}

# --- 0. Expected files sit next to this script. ---
# Default $EnvFile resolves here (not in param()) so `-File` invocation
# works: $PSScriptRoot is populated by the time this line runs.
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $EnvFile = Join-Path $PSScriptRoot 'prod.env'
}
$ComposeFile = Join-Path $PSScriptRoot 'docker-compose.prod.yml'
$CaddyFile = Join-Path $PSScriptRoot 'Caddyfile'
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
  Write-Fail -Code 1 -Message 'deploy/docker-compose.prod.yml not found next to this script.'
}
if (-not (Test-Path -LiteralPath $CaddyFile -PathType Leaf)) {
  Write-Fail -Code 1 -Message 'deploy/Caddyfile not found next to this script.'
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

# Required names mirror deploy/docker-compose.prod.yml ${VAR:?...} references.
$Required = @('DOMAIN', 'ACME_EMAIL', 'PUBLIC_API_ORIGIN', 'POSTGRES_PASSWORD', 'APP_DATABASE_URL', 'APP_CORS_ORIGINS')
foreach ($name in $Required) {
  if (-not $values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($values[$name])) {
    Write-Fail -Code 2 -Message "$name is missing or blank in prod.env (see deploy/prod.env.example)."
  }
}
if ($script:ExitCode -ne 0) { exit $script:ExitCode }

# --- 2. Template placeholders must be gone (example.com / CHANGE_ME). ---
$SiteVars = @('DOMAIN', 'ACME_EMAIL', 'PUBLIC_API_ORIGIN', 'APP_CORS_ORIGINS')
foreach ($name in $SiteVars) {
  if ($values[$name] -match 'example\.com|CHANGE_ME') {
    Write-Fail -Code 2 -Message "$name still holds a template placeholder (example.com / CHANGE_ME)."
  }
}
if ($values['POSTGRES_PASSWORD'] -match 'CHANGE_ME') {
  Write-Fail -Code 2 -Message 'POSTGRES_PASSWORD still holds the template placeholder. Generate a fresh random password.'
}
if ($values['APP_DATABASE_URL'] -match 'CHANGE_ME|example\.com') {
  Write-Fail -Code 2 -Message 'APP_DATABASE_URL still holds a template placeholder. Point it at the production database.'
}
if ($values['POSTGRES_PASSWORD'].Length -lt 16) {
  Write-Fail -Code 2 -Message 'POSTGRES_PASSWORD is shorter than 16 characters. Use a long random password.'
}

# --- 3. Public origin shape (mirrors build.mjs + lib/runtime-config.ts). ---
$originUri = $null
if (-not [System.Uri]::TryCreate($values['PUBLIC_API_ORIGIN'], [System.UriKind]::Absolute, [ref]$originUri)) {
  Write-Fail -Code 2 -Message 'PUBLIC_API_ORIGIN must be an absolute URL (for example https://app.example.org with your real domain).'
} else {
  if ($originUri.Scheme -ne 'https') {
    Write-Fail -Code 2 -Message 'PUBLIC_API_ORIGIN must use https in production.'
  }
  if ($originUri.UserInfo -ne '' -or $originUri.Query -ne '' -or $originUri.Fragment -ne '') {
    Write-Fail -Code 2 -Message 'PUBLIC_API_ORIGIN must be an origin only (no credentials, query, or fragment).'
  }
  if ($originUri.AbsolutePath -ne '/') {
    Write-Fail -Code 2 -Message 'PUBLIC_API_ORIGIN must be an origin only (no path; the client appends /api/v1/...).'
  }
  $originHost = $originUri.Host.ToLowerInvariant()
  if ($originHost -in @('localhost', '127.0.0.1', '::1') -or $originHost.EndsWith('.localhost')) {
    Write-Fail -Code 2 -Message 'PUBLIC_API_ORIGIN must not point at loopback in production.'
  }
  $domain = $values['DOMAIN'].Trim().ToLowerInvariant()
  if ($domain -ne $originHost) {
    Write-Fail -Code 2 -Message 'DOMAIN must equal the host of PUBLIC_API_ORIGIN (single public origin through Caddy).'
  }
  if ($domain -match '://|/') {
    Write-Fail -Code 2 -Message 'DOMAIN must be a bare hostname (no scheme, path, or port suffix).'
  }
}

# --- 4. CORS allowlist (mirrors backend validate_production_config). ---
try {
  $cors = ConvertFrom-Json $values['APP_CORS_ORIGINS']
} catch {
  Write-Fail -Code 2 -Message 'APP_CORS_ORIGINS must be a JSON array of exact origins.'
  $cors = $null
}
if ($null -ne $cors) {
  if ($cors -isnot [System.Array] -or $cors.Count -eq 0) {
    Write-Fail -Code 2 -Message 'APP_CORS_ORIGINS must list at least one exact https origin.'
  } else {
    $normalizedOrigin = $values['PUBLIC_API_ORIGIN'].TrimEnd('/').ToLowerInvariant()
    $found = $false
    foreach ($entry in $cors) {
      $u = $null
      if (-not [System.Uri]::TryCreate([string]$entry, [System.UriKind]::Absolute, [ref]$u)) {
        Write-Fail -Code 2 -Message 'APP_CORS_ORIGINS holds a value that is not an absolute URL.'
        continue
      }
      if ($u.Scheme -ne 'https') {
        Write-Fail -Code 2 -Message 'APP_CORS_ORIGINS entries must use https in production.'
      }
      $h = $u.Host.ToLowerInvariant()
      if ($h -in @('localhost', '127.0.0.1', '::1') -or $h.EndsWith('.localhost')) {
        Write-Fail -Code 2 -Message 'APP_CORS_ORIGINS must not contain loopback hosts in production.'
      }
      if (([string]$entry).TrimEnd('/').ToLowerInvariant() -eq $normalizedOrigin) { $found = $true }
    }
    if (-not $found) {
      Write-Fail -Code 2 -Message 'APP_CORS_ORIGINS must include PUBLIC_API_ORIGIN (the single Caddy origin).'
    }
  }
}

# --- 5. Database URL shape + password consistency (names only, never values). ---
if (-not $values['APP_DATABASE_URL'].StartsWith('postgresql+asyncpg://')) {
  Write-Fail -Code 2 -Message 'APP_DATABASE_URL must use the postgresql+asyncpg:// scheme.'
} elseif ($values['APP_DATABASE_URL'] -match '://([^:/@]+):([^@]+)@') {
  if ($Matches[2] -ne $values['POSTGRES_PASSWORD']) {
    Write-Fail -Code 2 -Message 'APP_DATABASE_URL password does not match POSTGRES_PASSWORD.'
  }
}

# --- 6. mock LLM = real data, offline AI (never a silent demo fallback). ---
$provider = ''
if ($values.ContainsKey('APP_LLM_PROVIDER')) { $provider = $values['APP_LLM_PROVIDER'].Trim().ToLowerInvariant() }
if ($provider -eq '' -or $provider -eq 'mock') {
  Write-Info 'APP_LLM_PROVIDER is mock: the stack serves real database data while AI execution stays offline.'
} elseif (-not $values.ContainsKey('APP_LLM_API_KEY') -or [string]::IsNullOrWhiteSpace($values['APP_LLM_API_KEY'])) {
  Write-Fail -Code 2 -Message 'APP_LLM_PROVIDER requests real execution but APP_LLM_API_KEY is blank.'
}

if ($script:ExitCode -ne 0) { exit $script:ExitCode }

# --- 7. Ports 80/443 must be free for Caddy (read-only TCP probe only). ---
if (-not $SkipPortCheck) {
  foreach ($port in @(80, 443)) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
      $iar = $client.BeginConnect('127.0.0.1', $port, $null, $null)
      $connected = $iar.AsyncWaitHandle.WaitOne(2000) -and $client.Connected
    } catch {
      $connected = $false
    } finally {
      $client.Close()
    }
    if ($connected) {
      Write-Fail -Code 3 -Message "Port $port is already in use on this machine. Stop the conflicting service before deploying - this script changes nothing."
    } else {
      Write-Info "Port $port appears free."
    }
  }
} else {
  Write-Info 'Port check skipped (-SkipPortCheck).'
}

# --- 8. Docker daemon must answer (read-only `docker info`, output suppressed). ---
if (-not $SkipDockerCheck) {
  & docker info --format '{{.ServerVersion}}' > $null 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Fail -Code 4 -Message 'Docker daemon is unreachable. Start Docker Desktop, then re-run preflight.'
  } else {
    Write-Info 'Docker daemon answers.'
  }
} else {
  Write-Info 'Docker check skipped (-SkipDockerCheck).'
}

if ($script:ExitCode -eq 0) {
  Write-Info 'Preflight passed. Next: config --quiet, build, backup, migrate, smoke (docs/deployment.md section 14).'
}
exit $script:ExitCode
