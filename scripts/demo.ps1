<#
.SYNOPSIS
    Demo housekeeping for PragMattie Sync, run from the repository root.

.DESCRIPTION
    reset            Print what a reset would do (a dry run); nothing changes.
    reset -Apply     Reseed the CRM demo data, undo what demos opened on GitHub (only items
                     marked as demo) and regenerate the simulated history for today.
    baseline         Record the GitHub baseline the reset restores to.
    baseline -Force  Replace an existing baseline.
    check            The pre-demo check: containers and web apps from the host, then the
                     checklist inside the orchestrator. Exit code 1 if anything failed.
    check -Smoke     The same, plus one real, paid call per agent (about 5 cents).

.EXAMPLE
    ./scripts/demo.ps1 reset
    ./scripts/demo.ps1 reset -Apply
    ./scripts/demo.ps1 check
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("reset", "baseline", "check")]
    [string]$Command,
    [switch]$Apply,
    [switch]$Force,
    [switch]$Smoke
)

$ErrorActionPreference = "Stop"

function Test-Running([string]$Service) {
    $running = docker compose --profile collect ps --status running --services 2>$null
    return ($running -split "`r?`n") -contains $Service
}

function Assert-Running([string[]]$Services) {
    foreach ($service in $Services) {
        if (-not (Test-Running $service)) {
            throw "The $service container isn't running. Start the stack first: docker compose up -d"
        }
    }
}

function Invoke-Checked([string]$What, [string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE)."
    }
}

$Containers = @("db", "api", "orchestrator", "crm-web", "insights-web")

function Get-Setting([string]$Name, [string]$Default) {
    # The environment wins, then .env, then the compose file's default.
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) {
        return $value
    }
    if (Test-Path ".env") {
        $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -Last 1
        if ($line) {
            $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
            if ($value) {
                return $value
            }
        }
    }
    return $Default
}

function Test-WebApp([string]$Name, [int]$Port) {
    # The Vite dev servers refuse requests from inside Docker, so these run on the host.
    $url = "http://localhost:$Port/"
    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Host ("  PASS  {0,-24} answering at {1}" -f "Web: $Name", $url)
        return $true
    }
    catch {
        Write-Host ("  FAIL  {0,-24} not answering at {1}: docker compose up -d" -f "Web: $Name", $url)
        return $false
    }
}

function Invoke-Check {
    $failed = 0
    foreach ($service in $Containers) {
        if (Test-Running $service) {
            Write-Host ("  PASS  {0,-24} running" -f "Container: $service")
        }
        else {
            Write-Host ("  FAIL  {0,-24} not running: docker compose up -d" -f "Container: $service")
            $failed++
        }
    }
    if (-not (Test-WebApp "crm-web" ([int](Get-Setting "CRM_WEB_PORT" "5173")))) { $failed++ }
    if (-not (Test-WebApp "insights-web" ([int](Get-Setting "INSIGHTS_WEB_PORT" "5174")))) { $failed++ }

    $checklist = 0
    if (Test-Running "orchestrator") {
        $arguments = @("compose", "exec", "-T", "orchestrator", "python", "-m", "sdlc.demo_check")
        if (Test-Running "api") {
            # The orchestrator can't see the CRM's migrations; the api container can.
            $heads = docker compose exec -T api alembic heads 2>$null
            $head = (($heads | Select-Object -First 1) -split "\s+")[0]
            if ($LASTEXITCODE -eq 0 -and $head) {
                $arguments += @("--crm-head", $head)
            }
        }
        if ($Smoke) {
            $arguments += @("--smoke", "--yes")
        }
        & docker @arguments
        $checklist = $LASTEXITCODE
    }
    if ($failed -gt 0) {
        throw "Not ready: $failed container or web check(s) failed (above)."
    }
    if ($checklist -ne 0) {
        exit 1
    }
}

function Invoke-Reset {
    Assert-Running @("db", "api", "orchestrator")
    if (-not $Apply) {
        Write-Host "CRM: would reseed the demo data (python -m app.seed --reset)."
        Invoke-Checked "The dry run" @("compose", "exec", "-T", "orchestrator", "python", "-m", "sdlc.demo_reset")
        return
    }

    $started = Get-Date
    $collector = Test-Running "collector"
    $restartFailed = $false
    try {
        if ($collector) {
            Write-Host "Pausing the collector..."
            Invoke-Checked "Pausing the collector" @("compose", "--profile", "collect", "stop", "collector")
        }
        Write-Host "Reseeding the CRM demo data..."
        Invoke-Checked "Reseeding the CRM" @("compose", "exec", "-T", "api", "python", "-m", "app.seed", "--reset")
        Write-Host "Resetting GitHub and the engineering history..."
        Invoke-Checked "The reset" @("compose", "exec", "-T", "orchestrator", "python", "-m", "sdlc.demo_reset", "--apply")
    }
    finally {
        if ($collector) {
            Write-Host "Restarting the collector..."
            docker compose --profile collect start collector
            if ($LASTEXITCODE -ne 0) {
                $restartFailed = $true
                Write-Host ("Restarting the collector failed (exit code $LASTEXITCODE). Start it " +
                    "yourself: docker compose --profile collect start collector") -ForegroundColor Red
            }
        }
    }
    if ($restartFailed) {
        throw "The reset finished, but the collector isn't running again (above)."
    }
    $seconds = [int]((Get-Date) - $started).TotalSeconds
    Write-Host "Reset done in $seconds seconds."
    Invoke-Check
}

function Invoke-Baseline {
    Assert-Running @("db", "orchestrator")
    $arguments = @("compose", "exec", "-T", "orchestrator", "python", "-m", "sdlc.demo_reset", "baseline")
    if ($Force) {
        $arguments += "--force"
    }
    Invoke-Checked "Recording the baseline" $arguments
}

try {
    switch ($Command) {
        "reset" { Invoke-Reset }
        "baseline" { Invoke-Baseline }
        "check" { Invoke-Check }
    }
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
