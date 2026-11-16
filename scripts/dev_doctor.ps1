<#
.SYNOPSIS
  CoursePilot dev doctor: read-only health check of the local dev stack.

.DESCRIPTION
  Checks Docker, the compose containers, backend health, migration status,
  the frontend/backend ports (with the owning process), stale Next.js dev
  servers, and prints a plain-English fix for every problem it finds.

  This script is READ-ONLY. It never stops processes, runs migrations,
  deletes files, or reads secrets. Every fix is printed for you to run.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev_doctor.ps1
#>
[CmdletBinding()]
param(
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8000
)

$ErrorActionPreference = 'Continue'
$script:Problems = 0
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Ok([string]$msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Write-Info([string]$msg) { Write-Host "  [..]   $msg" -ForegroundColor Gray }
function Write-Bad([string]$msg, [string[]]$fix) {
    $script:Problems++
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
    foreach ($line in $fix) { Write-Host "         fix: $line" -ForegroundColor Yellow }
}
function Write-Section([string]$title) { Write-Host ""; Write-Host "== $title ==" -ForegroundColor Cyan }

# Returns one object per PID that owns a socket on the port (listening or
# established). netstat is used because Get-NetTCPConnection can miss
# IPv6/loopback sockets held by a wedged process.
function Get-PortOwners([int]$port) {
    $rows = netstat -ano -p TCP 2>$null
    $rows += netstat -ano -p TCPv6 2>$null
    $owners = @{}
    foreach ($r in $rows) {
        $parts = ($r -split '\s+') | Where-Object { $_ -ne '' }
        if ($parts.Count -lt 5 -or $parts[0] -ne 'TCP') { continue }
        $local = $parts[1]; $state = $parts[3]; $procId = [int]$parts[4]
        if ($local -notmatch ":$port$") { continue }
        if ($procId -eq 0) { continue }
        if (-not $owners.ContainsKey($procId)) { $owners[$procId] = New-Object System.Collections.Generic.List[string] }
        if (-not $owners[$procId].Contains($state)) { $owners[$procId].Add($state) }
    }
    foreach ($procId in $owners.Keys) {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        [pscustomobject]@{
            Pid       = $procId
            Name      = if ($p) { $p.ProcessName } else { '?' }
            StartTime = if ($p -and $p.StartTime) { $p.StartTime } else { $null }
            States    = ($owners[$procId] -join ',')
        }
    }
}

function Format-Owner($o) {
    $started = if ($o.StartTime) { $o.StartTime.ToString('yyyy-MM-dd HH:mm') } else { 'unknown' }
    "PID $($o.Pid) ($($o.Name), started $started, $($o.States))"
}

function Test-Http([string]$url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 8 -MaximumRedirection 0 -ErrorAction Stop
        return [pscustomobject]@{ Ok = $true; Status = [int]$r.StatusCode; Body = $r.Content; Error = $null }
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $code = [int]$resp.StatusCode
            # Redirects (e.g. to /signin) mean the server is up.
            return [pscustomobject]@{ Ok = ($code -ge 300 -and $code -lt 400); Status = $code; Body = $null; Error = $_.Exception.Message }
        }
        return [pscustomobject]@{ Ok = $false; Status = 0; Body = $null; Error = $_.Exception.Message }
    }
}

Write-Host "CoursePilot dev doctor (read-only) - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "Repo: $RepoRoot"

# ---------------------------------------------------------------- Git
Write-Section "Git"
$branch = git -C $RepoRoot branch --show-current 2>$null
if ($branch) {
    Write-Info "Branch: $branch"
    $dirty = git -C $RepoRoot status --porcelain 2>$null
    if ($dirty) { Write-Info "Uncommitted changes: $(@($dirty).Count) file(s)" } else { Write-Ok "Working tree clean" }
} else {
    Write-Info "Not a git checkout (skipped)"
}

# ---------------------------------------------------------------- Docker
Write-Section "Docker"
$dockerOk = $false
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Bad "docker CLI not found on PATH." @("Install Docker Desktop and reopen this terminal.")
} else {
    docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "Docker engine is not running." @("Start Docker Desktop and wait until it says 'Engine running', then re-run this script.")
    } else {
        $dockerOk = $true
        Write-Ok "Docker engine is running"
    }
}

# ---------------------------------------------------------------- Containers
Write-Section "Containers"
$backendUp = $false
if ($dockerOk) {
    foreach ($name in 'coursepilot-db', 'coursepilot-backend', 'coursepilot-live-worker') {
        $state  = docker inspect -f '{{.State.Status}}' $name 2>$null
        $health = docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $name 2>$null
        if (-not $state) {
            Write-Bad "$name does not exist." @("From the repo root: docker compose up -d")
        } elseif ($state -ne 'running') {
            Write-Bad "$name is '$state'." @("docker compose up -d $($name -replace '^coursepilot-','')", "If it keeps stopping: docker logs --tail 50 $name")
        } elseif ($health -eq 'unhealthy') {
            Write-Bad "$name is running but UNHEALTHY." @("Check its log: docker logs --tail 50 $name", "Then: docker compose restart $($name -replace '^coursepilot-','')")
        } else {
            $h = if ($health -eq 'none') { 'no healthcheck' } else { $health }
            Write-Ok "$name running ($h)"
            if ($name -eq 'coursepilot-backend') { $backendUp = $true }
        }
    }
} else {
    Write-Info "Skipped (Docker not available)"
}

# ---------------------------------------------------------------- Backend health
Write-Section "Backend health"
$h = Test-Http "http://localhost:$BackendPort/api/v1/health"
if ($h.Ok -and $h.Status -eq 200) {
    $status = try { ($h.Body | ConvertFrom-Json).status } catch { '?' }
    Write-Ok "GET /api/v1/health -> 200 (status=$status)"
} else {
    Write-Bad "Backend health check failed ($($h.Error))." @(
        "If the container is running, read its log: docker logs --tail 80 coursepilot-backend",
        "Restart it: docker compose restart backend")
}

# ---------------------------------------------------------------- Migrations
Write-Section "Database migrations (dev DB, read-only)"
if ($backendUp) {
    $cur   = (docker exec coursepilot-backend alembic current 2>&1) | Where-Object { $_ -notmatch '^INFO' }
    $heads = (docker exec coursepilot-backend alembic heads 2>&1)   | Where-Object { $_ -notmatch '^INFO' }
    $curRev   = @($cur   | ForEach-Object { ($_ -split '\s+')[0] } | Where-Object { $_ }) | Sort-Object
    $headRevs = @($heads | ForEach-Object { ($_ -split '\s+')[0] } | Where-Object { $_ }) | Sort-Object
    if ($headRevs.Count -gt 1) {
        Write-Bad "Multiple alembic heads: $($headRevs -join ', ')." @("This is a code issue (branches need a merge migration). Ask before changing anything.")
    }
    if (-not $curRev) {
        Write-Bad "Could not read the current revision (empty DB or alembic error)." @("Inspect: docker exec coursepilot-backend alembic current")
    } elseif (($curRev -join ',') -eq ($headRevs -join ',')) {
        Write-Ok "At head: $($curRev -join ', ')"
    } else {
        Write-Bad "DB is at '$($curRev -join ', ')' but code head is '$($headRevs -join ', ')'." @(
            "1. Back up first (dump inside the container, then copy out; PowerShell '>' would corrupt a binary dump):",
            "   New-Item -ItemType Directory -Force `"$env:USERPROFILE\coursepilot-backups`"",
            "   docker exec coursepilot-db pg_dump -U coursepilot -Fc -f /tmp/cp.dump coursepilot",
            "   docker cp coursepilot-db:/tmp/cp.dump `"$env:USERPROFILE\coursepilot-backups\coursepilot-$(Get-Date -Format yyyyMMdd-HHmm).dump`"",
            "2. Then upgrade:   docker exec coursepilot-backend alembic upgrade head")
    }
} else {
    Write-Info "Skipped (backend container not running)"
}

# ---------------------------------------------------------------- Ports
Write-Section "Ports"
$feOwners = @(Get-PortOwners $FrontendPort)
$beOwners = @(Get-PortOwners $BackendPort)

if ($beOwners.Count -eq 0) {
    Write-Bad "Nothing is on port $BackendPort." @("Start the backend: docker compose up -d backend")
} else {
    foreach ($o in $beOwners) { Write-Info "Port ${BackendPort}: $(Format-Owner $o)" }
    $bad = $beOwners | Where-Object { $_.Name -notmatch '^(wslrelay|com\.docker\.backend|vpnkit|docker|python|uvicorn)' }
    if ($bad) { Write-Bad "Port $BackendPort is held by an unexpected process." @("Stop it (Task Manager > Details > PID, or Stop-Process -Id <PID>) and then: docker compose up -d backend") }
    else { Write-Ok "Port $BackendPort owned by Docker/WSL relay or the API" }
}

if ($feOwners.Count -eq 0) {
    Write-Bad "Nothing is on port $FrontendPort (frontend not running)." @("cd frontend; npm run dev -- -p $FrontendPort")
} else {
    foreach ($o in $feOwners) { Write-Info "Port ${FrontendPort}: $(Format-Owner $o)" }
}

# ---------------------------------------------------------------- Frontend
Write-Section "Frontend"
$nextProcs = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'next[\\/]dist[\\/]server[\\/]lib[\\/]start-server' })
$serverPids = @($feOwners | Where-Object { $_.Name -eq 'node' } | ForEach-Object { $_.Pid })

if ($feOwners.Count -gt 0) {
    $fe = Test-Http "http://localhost:$FrontendPort/signin"
    if ($fe.Ok -and $fe.Status -lt 400) {
        Write-Ok "GET /signin -> $($fe.Status)"
    } else {
        $pids = ($feOwners | ForEach-Object { $_.Pid }) -join ','
        Write-Bad "Port $FrontendPort is taken but /signin does not respond properly ($($fe.Status) $($fe.Error))." @(
            "A stale or wedged Next dev server is holding the port: PID $pids.",
            "Stop it: Task Manager > Details > end PID $pids, or  Stop-Process -Id $pids",
            "Then: Remove-Item -Recurse -Force frontend\.next ; cd frontend ; npm run dev -- -p $FrontendPort")
    }
}

foreach ($np in $nextProcs) {
    $p = Get-Process -Id $np.ProcessId -ErrorAction SilentlyContinue
    $started = if ($p -and $p.StartTime) { $p.StartTime.ToString('yyyy-MM-dd HH:mm') } else { 'unknown' }
    if ($serverPids -contains [int]$np.ProcessId) {
        Write-Info "Next server PID $($np.ProcessId) (started $started) serves port $FrontendPort"
    } else {
        Write-Bad "Orphan Next server PID $($np.ProcessId) (started $started) is not serving port $FrontendPort." @(
            "It can lock frontend\.next files (EBUSY). Stop it: Stop-Process -Id $($np.ProcessId)  (or end it in Task Manager)")
    }
}

$nextDir = Join-Path $RepoRoot 'frontend\.next'
if (Test-Path $nextDir) { Write-Info "frontend\.next exists (if you see EBUSY or module errors, stop the dev server and delete it)" }
else { Write-Info "frontend\.next absent (next dev will rebuild it)" }
if (-not (Test-Path (Join-Path $RepoRoot 'frontend\node_modules'))) {
    Write-Bad "frontend\node_modules is missing." @("cd frontend; npm install")
}

# ---------------------------------------------------------------- Summary
Write-Host ""
if ($script:Problems -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
} else {
    Write-Host "$script:Problems problem(s) found. Apply the 'fix:' lines above in order, then re-run." -ForegroundColor Yellow
}
exit $script:Problems
