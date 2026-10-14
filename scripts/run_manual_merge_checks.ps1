# Run locally in PowerShell 5.1 or 7 when automated checks are unavailable.
# No git staging/commit/push, Docker lifecycle changes, firewall edits, or production deployment.
# Usage (repository root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_manual_merge_checks.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_manual_merge_checks.ps1 -IncludeDockerIsolation
# A fresh report is written to the user's TEMP directory; send the file for review.

[CmdletBinding()]
param(
    [string]$OutputPath = '',
    [switch]$IncludeDockerIsolation
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        'fcai-manual-merge-checks-{0}.txt' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
    )
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite an existing report: $OutputPath"
}
$parent = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "Report directory does not exist: $parent"
}

$script:results = [System.Collections.Generic.List[object]]::new()

function Add-Report {
    param([string]$Line = '')
    Add-Content -LiteralPath $OutputPath -Value $Line -Encoding UTF8
}

function Invoke-Check {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments
    )
    Write-Host "Checking: $Name"
    Add-Report ("=== {0} ===" -f $Name)
    Add-Report ("command: {0} {1}" -f $Executable, ($Arguments -join ' '))
    $started = Get-Date
    $code = 1
    $output = ''
    try {
        # Exit codes belong to each native command; preserve output even on failure.
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            # PowerShell 5.1 otherwise expands Git's harmless stderr warnings
            # into verbose NativeCommandError records with script line numbers.
            $output = (& $Executable @Arguments 2>&1 | ForEach-Object { $_.ToString() }) -join "`n"
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = 1 }
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    catch {
        $output = $_.Exception.Message
        $code = 1
    }
    $elapsed = [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    Add-Report "exit_code=$code duration_seconds=$elapsed"
    if ($output) { Add-Report $output.TrimEnd() }
    Add-Report
    $script:results.Add([pscustomobject]@{ Name = $Name; ExitCode = $code })
    if ($code -eq 0) { Write-Host "  PASS (${elapsed}s)" -ForegroundColor Green }
    else { Write-Host "  FAIL (exit $code, ${elapsed}s)" -ForegroundColor Yellow }
}

Add-Report 'FCAI manual merge verification'
Add-Report ("timestamp_local={0:o}" -f (Get-Date))
Add-Report ("repository={0}" -f $repo)
Add-Report 'No environment variables, Docker container environments, or credentials are collected.'
Add-Report 'These checks do not verify the actual production VPS firewall.'
Add-Report

Push-Location -LiteralPath $repo
try {
    Invoke-Check 'Git working tree (includes uncommitted changes)' 'git' @('status', '--short', '--branch')
    Invoke-Check 'Git whitespace checks' 'git' @('diff', '--check')
    Invoke-Check 'Ruff' 'python' @('-m', 'ruff', 'check', '.')
    Invoke-Check 'Pytest regression suite' 'python' @('-m', 'pytest', '-q')
    Invoke-Check 'Windows Python dependency consistency' 'python' @('-m', 'pip', 'check')
    Invoke-Check 'Python compilation' 'python' @('-m', 'compileall', '-q', 'app', 'tests', 'scripts')
    Invoke-Check 'Canonical environment variable vocabulary' 'python' @('scripts/check_canonical_env_vocabulary.py')
    Invoke-Check 'Markdown links' 'python' @('scripts/check_markdown_links.py')
    Invoke-Check 'Docker Compose Crawl4AI profile' 'docker' @('compose', '--profile', 'crawl4ai', 'config', '--no-env-resolution', '--quiet')
    if (Test-Path -LiteralPath (Join-Path $repo '.gitnexus/run.cjs')) {
        Invoke-Check 'GitNexus local change impact' 'node' @('.gitnexus/run.cjs', 'detect-changes', '--scope', 'all', '--repo', '.')
        Invoke-Check 'GitNexus branch impact vs main' 'node' @('.gitnexus/run.cjs', 'detect-changes', '--scope', 'compare', '--base-ref', 'main', '--repo', '.')
    }
    else {
        Add-Report 'GitNexus runner not found; GitNexus checks skipped.'
        Add-Report
    }
    if ($IncludeDockerIsolation) {
        # Uses only the disposable fcai-security-dind test environment.
        # The helper makes a local control GET to verify sink logging.
        Invoke-Check 'Isolated production bot Python dependencies' 'docker' @('exec', 'fcai-security-dind', 'docker', 'exec', 'fcai-bot', 'python', '-m', 'pip', 'check')
        Invoke-Check 'Docker-in-Docker Chromium bypass and private-sink check' 'python' @('scripts/check_crawl4ai_dind_chromium.py')
        Invoke-Check 'Docker-in-Docker firewall check (read-only)' 'docker' @('exec', 'fcai-security-dind', 'bash', '/workspace/scripts/crawl4ai_firewall.sh', 'check')
    }
}
finally {
    Pop-Location
}

$failed = @($script:results | Where-Object { $_.ExitCode -ne 0 })
Add-Report '=== SUMMARY ==='
Add-Report ("checks={0} passed={1} failed={2}" -f $script:results.Count, ($script:results.Count - $failed.Count), $failed.Count)
foreach ($entry in $failed) { Add-Report ("FAILED: {0} exit_code={1}" -f $entry.Name, $entry.ExitCode) }
Add-Report 'Inspect the report for sensitive test output before sharing.'
Write-Host "`nReport: $OutputPath"
Write-Host ("Checks: {0} passed; {1} failed" -f ($script:results.Count - $failed.Count), $failed.Count)
Write-Host 'Send back this .txt report. A local PASS is not permission to enable Crawl4AI on production.'
if ($failed.Count -gt 0) { exit 1 }
