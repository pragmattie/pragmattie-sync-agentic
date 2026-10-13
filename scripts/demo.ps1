<#
.SYNOPSIS
    Demo housekeeping for PragMattie Sync, run from the repository root.

.DESCRIPTION
    reset            Print what a reset would do (a dry run); nothing changes.
    reset -Apply     Reseed the CRM demo data, undo what demos opened on GitHub (only items
                     marked as demo) and regenerate the simulated history for today.
    baseline         Record the GitHub baseline the reset restores to.
    baseline -Force  Replace an existing baseline.
    check [-Smoke]   The pre-demo check (arrives with rebuild item 6.9).

.EXAMPLE
    ./scripts/demo.ps1 reset
    ./scripts/demo.ps1 reset -Apply
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

function Invoke-Check {
    # Rebuild item 6.9 fills this in.
    Write-Host "The pre-demo check arrives in 6.9; nothing was checked."
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
        }
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
