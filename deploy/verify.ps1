#Requires -Version 5.1
<#
.SYNOPSIS
  Read-only post-deployment verification for the Fluxus stack (Stage 17).

.DESCRIPTION
  Anonymous HTTP checks against a configurable HTTPS base URL. Reads only:
  no login, no cookies stored, no mutations, no AI runs, no destructive
  calls. TLS validation is never disabled; timeouts bound every request.

  Checks (each mirrors a real contract in backend/):
  1. GET /api/v1/health -> 200 {status:ok, version, environment, contract}
     + X-Trace-Id (app/main.py trace_id_middleware, HealthResponse).
  2. GET /api/v1/readiness -> 200 {status:ready, database:ok} + X-Trace-Id,
     else 503 {status:degraded, database:unconfigured|unreachable} treated
     as FAIL (gate traffic on this, not on /health).
  3. GET /api/v1/no-such-route-verify -> 404 {error:{code:not_found,
     message, trace_id}} + matching X-Trace-Id header
     (errors.py contract envelope + catch-all in main.py).
  4. OPTIONS /api/v1/auth/logout with Origin=<base> -> 204/200 with
     Access-Control-Allow-Origin echoing exactly <base> and
     Access-Control-Allow-Credentials: true (CORSMiddleware, allowlist from
     APP_CORS_ORIGINS). A foreign Origin must NOT be echoed.
  5. GET / (frontend root through Caddy) -> 200 text/html from the Next.js
     standalone server (proves the proxy routes non-/api traffic).

  Exit codes: 0 = all checks passed; 1 = usage/base-URL problem;
  2 = any check failed; 3 = network/TLS/timeout before checks completed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy/verify.ps1 -BaseUrl https://app.example.org
#>
[CmdletBinding()]
param(
  [string]$BaseUrl = '',
  [int]$TimeoutSec = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$script:Failed = @()

function Write-Pass {
  param([string]$Name)
  [Console]::Out.WriteLine("verify [ok] $Name")
}

function Write-Fail {
  param([string]$Name, [string]$Detail)
  [Console]::Error.WriteLine("verify [FAIL] $Name -- $Detail")
  $script:Failed += $Name
}

# --- 0. Base URL must be an explicit https origin (no default, no fallback). ---
$BaseUrl = $BaseUrl.Trim().TrimEnd('/')
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  [Console]::Error.WriteLine('verify [FAIL] usage -- pass -BaseUrl https://your-domain (no default; nothing is probed without it).')
  exit 1
}
$baseUri = $null
if (-not [System.Uri]::TryCreate($BaseUrl, [System.UriKind]::Absolute, [ref]$baseUri)) {
  [Console]::Error.WriteLine('verify [FAIL] usage -- -BaseUrl must be an absolute URL.')
  exit 1
}
if ($baseUri.Scheme -ne 'https') {
  [Console]::Error.WriteLine('verify [FAIL] usage -- -BaseUrl must use https (this script never disables TLS validation).')
  exit 1
}
if ($baseUri.UserInfo -ne '' -or $baseUri.Query -ne '' -or $baseUri.Fragment -ne '') {
  [Console]::Error.WriteLine('verify [FAIL] usage -- -BaseUrl must be an origin only (no credentials, query, or fragment).')
  exit 1
}
if ($baseUri.AbsolutePath -ne '/') {
  [Console]::Error.WriteLine('verify [FAIL] usage -- -BaseUrl must be an origin only (no path).')
  exit 1
}
$originHost = $baseUri.Host.ToLowerInvariant()
if ($originHost -in @('localhost', '127.0.0.1', '::1') -or $originHost.EndsWith('.localhost')) {
  [Console]::Error.WriteLine('verify [FAIL] usage -- -BaseUrl must not be loopback: verify the public origin through Caddy.')
  exit 1
}
$Origin = $baseUri.GetLeftPart([System.UriPartial]::Authority)
if ($TimeoutSec -lt 2 -or $TimeoutSec -gt 120) {
  [Console]::Error.WriteLine('verify [FAIL] usage -- -TimeoutSec must be between 2 and 120 seconds.')
  exit 1
}

# --- Request helper: TLS validated, bounded timeout, no cookies kept. ---
function Invoke-VerifyRequest {
  param([string]$Method, [string]$Path, [hashtable]$Headers)
  $req = [System.Net.HttpWebRequest]::Create($Origin + $Path)
  $req.Method = $Method
  $req.Timeout = $TimeoutSec * 1000
  $req.ReadWriteTimeout = $TimeoutSec * 1000
  $req.AllowAutoRedirect = $false
  $req.CookieContainer = New-Object System.Net.CookieContainer
  foreach ($key in $Headers.Keys) {
    if ($key -eq 'Accept') { $req.Accept = $Headers[$key]; continue }
    $req.Headers.Set($key, $Headers[$key])
  }
  try {
    $resp = $req.GetResponse()
  } catch [System.Net.WebException] {
    $resp = $_.Exception.Response
    if ($null -eq $resp) { throw }
  }
  $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
  try {
    $bodyText = $reader.ReadToEnd()
  } finally {
    $reader.Close()
    $resp.Close()
  }
  $status = [int]$resp.StatusCode
  $heads = @{}
  foreach ($key in $resp.Headers.AllKeys) { $heads[$key.ToLowerInvariant()] = $resp.Headers[$key] }
  $body = $null
  if (-not [string]::IsNullOrWhiteSpace($bodyText)) {
    try { $body = $bodyText | ConvertFrom-Json } catch { $body = $null }
  }
  return @{ Status = $status; Headers = $heads; Body = $body; Raw = $bodyText }
}

# --- 1. Liveness: dependency-free, always 200 when the process answers. ---
try {
  $r = Invoke-VerifyRequest -Method 'GET' -Path '/api/v1/health' -Headers @{}
} catch {
  [Console]::Error.WriteLine("verify [FAIL] health -- unreachable: $($_.Exception.Message)")
  exit 3
}
if ($r.Status -ne 200) {
  Write-Fail 'health' "expected 200, got $($r.Status)."
} elseif ($null -eq $r.Body -or $r.Body.status -ne 'ok') {
  Write-Fail 'health' 'body is not {status:ok, ...}.'
} elseif ([string]::IsNullOrWhiteSpace($r.Body.version)) {
  Write-Fail 'health' 'body misses version/environment/contract fields.'
} elseif ([string]::IsNullOrWhiteSpace($r.Headers['x-trace-id'])) {
  Write-Fail 'health' 'missing X-Trace-Id response header.'
} else {
  Write-Pass ("health (contract $($r.Body.contract), env $($r.Body.environment))")
}

# --- 2. Readiness: 200 ready/ok ONLY with a live database, else FAIL. ---
try {
  $r = Invoke-VerifyRequest -Method 'GET' -Path '/api/v1/readiness' -Headers @{}
} catch {
  Write-Fail 'readiness' "unreachable: $($_.Exception.Message)"
  $r = $null
}
if ($null -ne $r) {
  if ($r.Status -eq 200 -and $null -ne $r.Body) {
    if ($r.Body.status -eq 'ready' -and $r.Body.database -eq 'ok') {
      if (-not [string]::IsNullOrWhiteSpace($r.Headers['x-trace-id'])) {
        Write-Pass 'readiness (ready, database ok)'
      } else {
        Write-Fail 'readiness' 'ready but missing X-Trace-Id header.'
      }
    } else {
      Write-Fail 'readiness' 'unexpected ready body.'
    }
  } elseif ($r.Status -eq 503 -and $null -ne $r.Body -and $r.Body.status -eq 'degraded') {
    Write-Fail 'readiness' "degraded (database: $($r.Body.database)) -- fix persistence; send no traffic here."
  } else {
    Write-Fail 'readiness' "unexpected answer: status=$($r.Status)."
  }
}

# --- 3. Unknown route: contract envelope + matching trace id. ---
try {
  $r = Invoke-VerifyRequest -Method 'GET' -Path '/api/v1/no-such-route-verify' -Headers @{}
} catch {
  Write-Fail 'unknown-route' "unreachable: $($_.Exception.Message)"
  $r = $null
}
if ($null -ne $r) {
  $err = $null
  if ($null -ne $r.Body) { $err = $r.Body.error }
  $headerTrace = $r.Headers['x-trace-id']
  $bodyTrace = $null
  if ($null -ne $err) { $bodyTrace = $err.trace_id }
  if ($r.Status -eq 404 -and $null -ne $err -and $err.code -eq 'not_found') {
    if ([string]::IsNullOrWhiteSpace($err.message) -or [string]::IsNullOrWhiteSpace($bodyTrace)) {
      Write-Fail 'unknown-route' 'envelope misses message or trace_id.'
    } elseif ([string]::IsNullOrWhiteSpace($headerTrace) -or $bodyTrace -ne $headerTrace) {
      Write-Fail 'unknown-route' 'body trace_id does not match the X-Trace-Id header.'
    } else {
      Write-Pass 'unknown-route (envelope not_found, trace ids match)'
    }
  } else {
    Write-Fail 'unknown-route' "expected 404 envelope {error:{code:not_found, ...}}, got status=$($r.Status)."
  }
}

# --- 4. CORS preflight on a real endpoint (logout allows OPTIONS in CORS). ---
$preHeaders = @{
  'Origin' = $Origin
  'Access-Control-Request-Method' = 'POST'
  'Access-Control-Request-Headers' = 'content-type,x-csrf-token'
}
try {
  $r = Invoke-VerifyRequest -Method 'OPTIONS' -Path '/api/v1/auth/logout' -Headers $preHeaders
} catch {
  Write-Fail 'cors-preflight' "unreachable: $($_.Exception.Message)"
  $r = $null
}
if ($null -ne $r) {
  $echo = $r.Headers['access-control-allow-origin']
  $creds = $r.Headers['access-control-allow-credentials']
  if (($r.Status -eq 200 -or $r.Status -eq 204) -and $echo -ceq $Origin -and $creds -eq 'true') {
    Write-Pass 'cors-preflight (origin echoed, credentials allowed)'
  } else {
    Write-Fail 'cors-preflight' "expected echo of exactly $Origin with credentials:true, got status=$($r.Status)."
  }
}
$foreignHeaders = @{
  'Origin' = 'https://foreign.example.net'
  'Access-Control-Request-Method' = 'POST'
  'Access-Control-Request-Headers' = 'content-type,x-csrf-token'
}
try {
  $rf = Invoke-VerifyRequest -Method 'OPTIONS' -Path '/api/v1/auth/logout' -Headers $foreignHeaders
} catch {
  Write-Fail 'cors-foreign' "unreachable: $($_.Exception.Message)"
  $rf = $null
}
if ($null -ne $rf) {
  if ($rf.Headers['access-control-allow-origin'] -ceq 'https://foreign.example.net') {
    Write-Fail 'cors-foreign' 'foreign origin was echoed: the CORS allowlist is misconfigured.'
  } else {
    Write-Pass 'cors-foreign (foreign origin not echoed)'
  }
}

# --- 5. Frontend root through Caddy: storefront HTML, not an API answer. ---
try {
  $r = Invoke-VerifyRequest -Method 'GET' -Path '/' -Headers @{ 'Accept' = 'text/html' }
} catch {
  Write-Fail 'frontend-root' "unreachable: $($_.Exception.Message)"
  $r = $null
}
if ($null -ne $r) {
  $ctype = $r.Headers['content-type']
  if ($r.Status -eq 200 -and $null -ne $ctype -and $ctype.Contains('text/html') -and $r.Raw -match '<html') {
    Write-Pass 'frontend-root (Next.js HTML through Caddy)'
  } else {
    Write-Fail 'frontend-root' "expected 200 text/html from the storefront, got status=$($r.Status)."
  }
}

# --- Verdict (no secrets in output; paths/bodies stay local). ---
if ($script:Failed.Count -eq 0) {
  [Console]::Out.WriteLine('verify: all checks passed.')
  exit 0
}
[Console]::Error.WriteLine(('verify: FAILED checks: ' + ($script:Failed -join ', ')))
exit 2
