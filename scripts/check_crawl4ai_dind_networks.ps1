# Opt-in security regression test for the disposable fcai-security-dind engine.
# Temporarily attaches one extra INTERNAL bridge to the crawler, then removes
# it in finally. Never run against production or a non-disposable engine.
# Usage from the repository root:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_crawl4ai_dind_networks.ps1
# Send the generated report from %TEMP% for review.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$report = Join-Path ([System.IO.Path]::GetTempPath()) "fcai-topology-drift-$stamp.txt"
$testNetwork = "fcai-topology-guard-$stamp".ToLowerInvariant()
$attached = $false
$created = $false
$failure = ''

function Report([string]$Message) {
    Add-Content -LiteralPath $report -Value $Message -Encoding UTF8
    Write-Host $Message
}

function Invoke-Isolated([string[]]$InnerArgs) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& docker exec fcai-security-dind @InnerArgs 2>&1 | ForEach-Object { $_.ToString() })
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 1 }
        return [pscustomobject]@{ Code = $code; Text = ($lines -join "`n") }
    }
    catch {
        return [pscustomobject]@{ Code = 1; Text = $_.Exception.Message }
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

Report 'FCAI disposable Docker-in-Docker topology drift test'
Report "timestamp=$((Get-Date).ToString('o'))"
Report 'No production changes or secrets collected.'

try {
    $image = (& docker inspect fcai-security-dind --format '{{.Config.Image}}' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $image -notmatch 'dind') {
        throw 'fcai-security-dind must be a running, disposable Docker-in-Docker image'
    }

    $syntax = Invoke-Isolated @('bash', '-n', '/workspace/scripts/crawl4ai_firewall.sh')
    if ($syntax.Code -ne 0) { throw "Firewall shell syntax failed: $($syntax.Text)" }
    Report 'PASS: firewall shell syntax'

    $baseline = Invoke-Isolated @('bash', '/workspace/scripts/crawl4ai_firewall.sh', 'check')
    if ($baseline.Code -ne 0) { throw "Baseline firewall check failed: $($baseline.Text)" }
    Report 'PASS: firewall baseline with expected networks'

    $networks = Invoke-Isolated @('docker', 'network', 'ls', '--format', '{{.Name}}')
    if ($networks.Code -ne 0) { throw 'Cannot inspect nested Docker networks' }
    if (@($networks.Text -split "`n") -contains $testNetwork) {
        throw 'Temporary network name already exists; refusing to reuse it'
    }

    $create = Invoke-Isolated @('docker', 'network', 'create', '--driver', 'bridge', '--internal', $testNetwork)
    if ($create.Code -ne 0) { throw "Could not create isolated network: $($create.Text)" }
    $created = $true

    $connect = Invoke-Isolated @('docker', 'network', 'connect', $testNetwork, 'fcai-crawl4ai')
    if ($connect.Code -ne 0) { throw "Could not attach extra network: $($connect.Text)" }
    $attached = $true

    $negative = Invoke-Isolated @('bash', '/workspace/scripts/crawl4ai_firewall.sh', 'check')
    if ($negative.Code -eq 0 -or $negative.Text -notmatch 'unexpected Docker network attachment') {
        throw "SECURITY FAIL: extra network was not rejected (exit=$($negative.Code)): $($negative.Text)"
    }
    Report 'PASS: firewall rejected an extra network attached to the crawler'
}
catch {
    $failure = $_.Exception.Message
    Report "FAIL: $failure"
}
finally {
    if ($attached) {
        $disconnect = Invoke-Isolated @('docker', 'network', 'disconnect', $testNetwork, 'fcai-crawl4ai')
        if ($disconnect.Code -ne 0) {
            $failure += " | network disconnect failed: $($disconnect.Text)"
        }
    }
    if ($created) {
        $remove = Invoke-Isolated @('docker', 'network', 'rm', $testNetwork)
        if ($remove.Code -ne 0) { $failure += " | network removal failed: $($remove.Text)" }
    }

    $restored = Invoke-Isolated @('bash', '/workspace/scripts/crawl4ai_firewall.sh', 'check')
    if ($restored.Code -ne 0) {
        $failure += " | restored firewall check failed: $($restored.Text)"
    }
    else {
        Report 'PASS: expected network topology and firewall restored'
    }
}

if ($failure) { Report "RESULT: FAIL$failure" }
else { Report 'RESULT: PASS' }
Report "Report file: $report"
if ($failure) { exit 1 }
