# Read-only diagnostics for the isolated Crawl4AI Docker-in-Docker test environment.
# Usage (from the repository root):
#   pwsh -NoProfile -File .\scripts\crawl4ai_docker_diagnostics.ps1
#   pwsh -NoProfile -File .\scripts\crawl4ai_docker_diagnostics.ps1 -OutputPath .\crawl4ai-report.txt
# The script does not change containers, firewall rules, application config or production.
# It deliberately omits environment variables, tokens and full container logs.

param(
    [string]$OutputPath = (Join-Path $env:TEMP ("crawl4ai-diagnostics-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss')))
)

$ErrorActionPreference = 'Stop'
$script:Report = [System.Collections.Generic.List[string]]::new()
$script:FailedSteps = 0

function Add-ReportLine {
    param([string]$Text = '')
    $script:Report.Add($Text)
}

function Invoke-DockerRead {
    param(
        [string]$Name,
        [string[]]$DockerArgs,
        [switch]$SourceOnly
    )

    Add-ReportLine "=== $Name ==="
    try {
        $lines = @(& docker @DockerArgs 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
        $output = $lines -join "`n"
    }
    catch {
        $exitCode = 1
        $output = $_.Exception.Message
    }

    Add-ReportLine "exit_code=$exitCode"
    if ($exitCode -ne 0) {
        $script:FailedSteps++
    }
    if (-not $SourceOnly) {
        if ($output) { Add-ReportLine $output.TrimEnd() }
        Add-ReportLine
    }

    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
}

function Add-ServerSourceExcerpts {
    param([string]$SourceText)

    # Only selected lines of image-shipped source code are included in the report.
    # Never export process environment or arbitrary server logs.
    $sourceLines = @($SourceText -split "`r?`n")
    $expression = '(?i)proxy_config|Provenance\.UNTRUSTED|BrowserConfig|CrawlerRunConfig|handle_crawl_request|\bdef crawl\b|http_proxy|ProxyConfig'
    $hitIndexes = [System.Collections.Generic.List[int]]::new()
    for ($i = 0; $i -lt $sourceLines.Count; $i++) {
        if ($sourceLines[$i] -match $expression) {
            $hitIndexes.Add($i)
        }
    }

    Add-ReportLine "source_total_lines=$($sourceLines.Count)"
    Add-ReportLine "matching_lines=$($hitIndexes.Count)"
    if ($hitIndexes.Count -eq 0) {
        Add-ReportLine 'No matching REST config/proxy code found in /app/server.py.'
        Add-ReportLine
        return
    }

    $indexes = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($hit in ($hitIndexes | Select-Object -First 45)) {
        for ($line = [Math]::Max(0, $hit - 4); $line -le [Math]::Min($sourceLines.Count - 1, $hit + 5); $line++) {
            [void]$indexes.Add($line)
        }
    }

    $lastIndex = -2
    foreach ($index in ($indexes | Sort-Object | Select-Object -First 240)) {
        if ($index -gt $lastIndex + 1) { Add-ReportLine '...' }
        Add-ReportLine ('{0,5}: {1}' -f ($index + 1), $sourceLines[$index])
        $lastIndex = $index
    }
    if ($hitIndexes.Count -gt 45 -or $indexes.Count -gt 240) {
        Add-ReportLine '[Source excerpts truncated; 45 matches / 240 source lines max.]'
    }
    Add-ReportLine
}

function Add-RelatedSourceExcerpts {
    param([string]$Content, [string]$Pattern)

    $lines = @($Content -split "`r?`n")
    $hitIndexes = [System.Collections.Generic.List[int]]::new()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $Pattern) { $hitIndexes.Add($i) }
    }
    Add-ReportLine "source_total_lines=$($lines.Count)"
    Add-ReportLine "matching_lines=$($hitIndexes.Count)"
    $selected = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($hit in ($hitIndexes | Select-Object -First 60)) {
        for ($i = [Math]::Max(0, $hit - 10); $i -le [Math]::Min($lines.Count - 1, $hit + 10); $i++) {
            [void]$selected.Add($i)
        }
    }
    $previous = -2
    foreach ($index in ($selected | Sort-Object | Select-Object -First 440)) {
        if ($index -gt $previous + 1) { Add-ReportLine '...' }
        Add-ReportLine ('{0,5}: {1}' -f ($index + 1), $lines[$index])
        $previous = $index
    }
    if ($hitIndexes.Count -gt 60 -or $selected.Count -gt 440) {
        Add-ReportLine '[Source excerpts truncated to first 60 matches / 440 lines]'
    }
    Add-ReportLine
}

function Add-SourceRange {
    param(
        [string]$Name,
        [string]$Content,
        [int]$Start,
        [int]$End
    )

    Add-ReportLine "=== $Name ==="
    $lines = @($Content -split "`r?`n")
    for ($i = [Math]::Max(1, $Start); $i -le [Math]::Min($End, $lines.Count); $i++) {
        Add-ReportLine ('{0,5}: {1}' -f $i, $lines[$i - 1])
    }
    Add-ReportLine
}

Add-ReportLine 'Crawl4AI 0.9.3 - Docker Desktop isolated diagnostic report'
Add-ReportLine "collected_at=$((Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'))"
Add-ReportLine 'scope=read-only; no host firewall changes; no external SSRF probes'
Add-ReportLine 'secrets=environment variables, API tokens and container logs excluded'
Add-ReportLine

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Add-ReportLine 'ERROR: docker CLI is not available on PATH.'
    $script:FailedSteps++
}
else {
    Invoke-DockerRead 'Docker Desktop Linux engine' @('info', '--format', '{{.OSType}} {{.ServerVersion}}') | Out-Null
    $dind = Invoke-DockerRead 'Isolated DinD container status' @(
        'ps', '--filter', 'name=^/fcai-security-dind$', '--format', '{{.Names}} {{.Status}}'
    )
    if ($dind.ExitCode -eq 0 -and $dind.Output -match 'fcai-security-dind') {
        Invoke-DockerRead 'Inner Docker firewall backend' @(
            'exec', 'fcai-security-dind', 'docker', 'info', '--format', '{{json .FirewallBackend}}'
        ) | Out-Null
        Invoke-DockerRead 'Inner test container status' @(
            'exec', 'fcai-security-dind', 'docker', 'ps', '--format', '{{.Names}} {{.Status}}'
        ) | Out-Null
        Invoke-DockerRead 'Real Crawl4AI container image ID' @(
            'exec', 'fcai-security-dind', 'docker', 'inspect', '--format', '{{.Image}}', 'fcai-crawl4ai'
        ) | Out-Null
        Invoke-DockerRead 'Firewall check (no changes)' @(
            'exec', 'fcai-security-dind', 'bash', '/workspace/scripts/crawl4ai_firewall.sh', 'check'
        ) | Out-Null

        $source = Invoke-DockerRead 'Crawl4AI 0.9.3 /app/server.py REST proxy handling' @(
            'exec', 'fcai-security-dind', 'docker', 'exec', 'fcai-crawl4ai', 'cat', '/app/server.py'
        ) -SourceOnly
        if ($source.ExitCode -eq 0) {
            Add-ServerSourceExcerpts -SourceText $source.Output
            Add-SourceRange -Name 'Server startup / permanent browser (lines 208-270)' `
                -Content $source.Output -Start 208 -End 270
        }
        else {
            Add-ReportLine $source.Output
            Add-ReportLine
        }
        # Inspect only source code shipped inside the actual pinned image.
        # The untrusted REST schema forbids client-supplied proxy_config;
        # these server-side modules determine whether trusted egress works.
        $modules = @(
            [pscustomobject]@{
                Path = '/app/egress_broker.py'
                Name = 'Trusted egress broker and proxy restrictions'
                Pattern = '(?i)def |class |proxy|egress|dns|address|route|socket|browser|block|deny|allow|resolve|redirect|private|loopback'
            },
            [pscustomobject]@{
                Path = '/app/egress_proxy.py'
                Name = 'Local DNS-pinning proxy and upstream forwarding'
                Pattern = '(?i)def |class |connect|proxy|squid|upstream|resolve|pin|socket|redirect|handle|listen|port|forbid|block'
            },
            [pscustomobject]@{
                Path = '/app/api.py'
                Name = 'REST crawl handler and config trust boundary'
                Pattern = '(?i)handle_crawl_request|BrowserConfig|CrawlerRunConfig|Provenance\.UNTRUSTED|get_crawler|proxy_config|enforce_egress|crawler_config|browser_config|async def'
            },
            [pscustomobject]@{
                Path = '/app/crawler_pool.py'
                Name = 'Browser pool creation and reuse'
                Pattern = '(?i)get_crawler|init_permanent|BrowserConfig|proxy|browser_config|crawler_config|async def|cache|pool'
            }
        )
        foreach ($module in $modules) {
            $result = Invoke-DockerRead $module.Name @(
                'exec', 'fcai-security-dind', 'docker', 'exec', 'fcai-crawl4ai', 'cat', $module.Path
            ) -SourceOnly
            Add-ReportLine "source_file=$($module.Path)"
            if ($result.ExitCode -eq 0) {
                Add-RelatedSourceExcerpts -Content $result.Output -Pattern $module.Pattern
                if ($module.Path -eq '/app/egress_broker.py') {
                    Add-SourceRange -Name 'Complete server-side egress enforcement (lines 155-209)' `
                        -Content $result.Output -Start 155 -End 209
                }
                if ($module.Path -eq '/app/egress_proxy.py') {
                    Add-SourceRange -Name 'Pinning proxy connection handling (lines 144-300)' `
                        -Content $result.Output -Start 144 -End 300
                }
                if ($module.Path -eq '/app/api.py') {
                    Add-SourceRange -Name 'REST crawl egress enforcement (lines 677-734)' `
                        -Content $result.Output -Start 677 -End 734
                }
            }
            else {
                Add-ReportLine $result.Output
                Add-ReportLine
            }
        }
    }
    else {
        Add-ReportLine 'Isolated DinD test container is unavailable; nested checks skipped.'
        $script:FailedSteps++
    }
}

Add-ReportLine "failed_steps=$($script:FailedSteps)"
Add-ReportLine 'NOTE: This diagnostic does not prove DNS rebinding or full SSRF isolation.'

$destination = [System.IO.Path]::GetFullPath($OutputPath)
$directory = [System.IO.Path]::GetDirectoryName($destination)
if (-not [System.IO.Directory]::Exists($directory)) {
    throw "Output directory does not exist: $directory"
}
[System.IO.File]::WriteAllLines(
    $destination, [string[]]$script:Report, [System.Text.UTF8Encoding]::new($false)
)
Write-Host "Report saved: $destination"
Write-Host "Failed checks: $($script:FailedSteps)"
Write-Host 'Review the report before sharing. Do not enable Crawl4AI on production based on this report alone.'
if ($script:FailedSteps -gt 0) { exit 1 }
