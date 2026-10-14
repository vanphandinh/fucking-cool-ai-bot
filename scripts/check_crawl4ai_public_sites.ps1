# Optional smoke test in the disposable Docker Desktop Docker-in-Docker engine.
# Never invoke against a production Docker host or with production credentials.
# Requires fcai-security-dind containing /tmp/fcai-20-real-sites.txt (20 distinct
# public sites), /tmp/fcai-smoke.env and the locally built fcai-smoke-bot:local.
# The bot configuration stays disabled; this probes only the private crawler API.
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_crawl4ai_public_sites.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$report = Join-Path ([System.IO.Path]::GetTempPath()) "fcai-public-smoke-$stamp.txt"
$failed = $false

function Report([string]$line) {
    Add-Content -LiteralPath $report -Value $line -Encoding UTF8
    Write-Host $line
}

function Run-Test([string]$label, [string[]]$Arguments) {
    Report "=== $label ==="
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker @Arguments 2>&1 | ForEach-Object { Report $_.ToString() }
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 1 }
        Report "exit_code=$code"
        return $code
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

Report 'FCAI disposable Docker-in-Docker 20 public websites smoke'
Report "timestamp=$((Get-Date).ToString('o'))"
Report 'No production activation, no application env changes.'

try {
    $image = (& docker inspect fcai-security-dind --format '{{.Config.Image}}' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $image -notmatch 'dind') {
        throw 'fcai-security-dind must be a running Docker-in-Docker test container'
    }
    $check = Run-Test 'Linux firewall' @(
        'exec', 'fcai-security-dind', 'bash',
        '/workspace/scripts/crawl4ai_firewall.sh', 'check'
    )
    if ($check -ne 0) { throw 'Firewall check failed; crawler smoke not started' }

    $check = Run-Test 'Required isolated test files and image' @(
        'exec', 'fcai-security-dind', 'sh', '-c',
        'test -s /tmp/fcai-20-real-sites.txt && test -s /tmp/fcai-smoke.env && test -s /workspace/scripts/smoke_crawl4ai.py && docker image inspect fcai-smoke-bot:local >/dev/null && docker inspect fcai-bot >/dev/null'
    )
    if ($check -ne 0) { throw 'Missing disposable smoke fixture, image, or network namespace' }

    # Sharing only the fixture bot's network namespace preserves the firewall
    # source-IP allowlist without replacing or restarting the fixture container.
    $check = Run-Test '20 representative public URLs through isolated Crawl4AI' @(
        'exec', 'fcai-security-dind', 'docker', 'run', '--rm',
        '--network', 'container:fcai-bot',
        '--env-file', '/tmp/fcai-smoke.env',
        '-e', 'PYTHONPATH=/app',
        '-e', 'CRAWL4AI_ENABLED=0',
        '-e', 'CRAWL4AI_URL=http://crawl4ai:11235',
        '-e', 'CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128',
        '-v', '/tmp/fcai-20-real-sites.txt:/tmp/fcai-20-real-sites.txt:ro',
        '-v', '/workspace/scripts/smoke_crawl4ai.py:/tmp/smoke_crawl4ai.py:ro',
        'fcai-smoke-bot:local', 'python', '-u',
        '/tmp/smoke_crawl4ai.py', '--urls-file', '/tmp/fcai-20-real-sites.txt'
    )
    if ($check -ne 0) { throw 'Public smoke did not pass all 20 sites' }

    $check = Run-Test 'Linux firewall after smoke' @(
        'exec', 'fcai-security-dind', 'bash',
        '/workspace/scripts/crawl4ai_firewall.sh', 'check'
    )
    if ($check -ne 0) { throw 'Firewall check failed after smoke' }
}
catch {
    $failed = $true
    Report "FAILED: $($_.Exception.Message)"
}

if ($failed) { Report 'RESULT: FAIL' }
else { Report 'RESULT: PASS' }
Report "Report file: $report"
if ($failed) { exit 1 }
