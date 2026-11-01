#requires -Version 5.1
<##
.SYNOPSIS
Updates autoXAK with a database backup and readiness check.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy/update.ps1 -SshHost root@185.149.145.234 -RemotePath /opt/autoxak-production
.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy/update.ps1 -Mode Copy -ComposeFile docker-compose.yml -EnvFile .env -HealthUrl https://localhost:8000/ready -HealthCaFile /app/cert.pem
##>
[CmdletBinding()]
param(
    [ValidateSet('Rebuild','Copy')][string]$Mode = 'Rebuild',
    [string]$ComposeFile = 'compose.production.yml',
    [string]$EnvFile = '.env.production',
    [string]$SshHost,
    [string]$RemotePath = '/opt/autoxak-production',
    [string]$HealthUrl = 'http://127.0.0.1:8000/ready',
    [string]$HealthCaFile = '',
    [ValidateRange(5,600)][int]$HealthTimeout = 180
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $result = & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE). Update aborted." }
    return $result
}

if ($SshHost) {
    if ($SshHost -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$' -or $RemotePath -notmatch '^/[a-zA-Z0-9_./-]+$') {
        throw 'Use user@host and an absolute remote path containing letters, digits, /, _, . or -.'
    }
    if ($Mode -ne 'Rebuild') { throw 'SSH deployments use Rebuild. Copy is for local writable Docker containers.' }
    # The production helper backs up PostgreSQL, pulls with --ff-only and rebuilds
    # the API image. It preserves .env.production and all data volumes.
    Invoke-Checked 'ssh' @('-T','-o','ConnectTimeout=15','--',$SshHost,"cd -- '$RemotePath' && bash deploy/production.sh update && bash deploy/production.sh check") | Out-Host
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
$oldTag = [Environment]::GetEnvironmentVariable('APP_IMAGE_TAG','Process')
$backup = $null
$changed = $false
$container = $null
$rollbackFile = $null
try {
    $composePath = (Resolve-Path -LiteralPath $ComposeFile).Path
    $envPath = (Resolve-Path -LiteralPath $EnvFile).Path
    $composeArgs = @('compose','--env-file',$envPath,'-f',$composePath)
    Invoke-Checked 'docker' ($composeArgs + @('config','--quiet')) | Out-Host
    $container = (Invoke-Checked 'docker' ($composeArgs + @('ps','-q','api')) | Out-String).Trim()
    if (-not $container -or $container -match '\s') { throw 'Expected exactly one running API container. Start the stack first.' }
    $readOnly = (Invoke-Checked 'docker' @('inspect','--format','{{.HostConfig.ReadonlyRootfs}}',$container) | Out-String).Trim()
    if ($Mode -eq 'Copy' -and $readOnly -eq 'true') {
        throw 'API filesystem is read-only. Use -Mode Rebuild for production.'
    }
    if ($Mode -eq 'Copy') {
        $containerUser = (Invoke-Checked 'docker' @('inspect','--format','{{.Config.User}}',$container) | Out-String).Trim()
        if ($containerUser -and $containerUser -notin @('root','0','0:0')) {
            throw 'Copy requires a root development container to preserve readable files. Use -Mode Rebuild for a non-root API.'
        }
    }
    $oldImage = (Invoke-Checked 'docker' @('inspect','--format','{{.Image}}',$container) | Out-String).Trim()
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
    $backup = Join-Path $repo ('backups/update-' + $stamp)
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $backup 'previous-image.txt') -Value $oldImage -Encoding Ascii
    # Write the binary dump inside PostgreSQL, then docker cp it. PowerShell 5.1
    # text redirection corrupts binary pg_dump output, so it is never used here.
    $dbContainer = (Invoke-Checked 'docker' ($composeArgs + @('ps','-q','postgres')) | Out-String).Trim()
    if (-not $dbContainer) { throw 'PostgreSQL container is not running.' }
    $dumpPath = '/tmp/autoxak-update-' + $stamp + '.dump'
    $dumpCommand = 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file=' + $dumpPath + ' && pg_restore --list ' + $dumpPath + ' >/dev/null'
    try {
        Invoke-Checked 'docker' @('exec',$dbContainer,'sh','-ec',$dumpCommand) | Out-Host
        Invoke-Checked 'docker' @('cp',"${dbContainer}:$dumpPath",(Join-Path $backup 'database.dump')) | Out-Host
        if ((Get-Item -LiteralPath (Join-Path $backup 'database.dump')).Length -eq 0) { throw 'Database backup is empty.' }
    } finally {
        & docker exec $dbContainer rm -f $dumpPath | Out-Null
    }
    if ($Mode -eq 'Rebuild') {
        $env:APP_IMAGE_TAG = 'update-' + $stamp.ToLowerInvariant()
        Invoke-Checked 'docker' ($composeArgs + @('build','api')) | Out-Host
        $changed = $true
        Invoke-Checked 'docker' ($composeArgs + @('up','-d','--no-deps','--wait','--wait-timeout',"$HealthTimeout",'api')) | Out-Host
        $container = (Invoke-Checked 'docker' ($composeArgs + @('ps','-q','api')) | Out-String).Trim()
    } else {
        # Copy complete directories, including new modules. .env, certificates,
        # database volumes and logs are deliberately outside the copied paths.
        $paths = @('app','static','deploy/entrypoint.py','seed_fleet.py','seed_fuel_prices.py')
        foreach ($path in $paths) {
            $destination = Join-Path $backup $path
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Invoke-Checked 'docker' @('cp',"${container}:/app/$path",$destination) | Out-Host
        }
        Invoke-Checked 'docker' ($composeArgs + @('stop','--timeout','100','api')) | Out-Host
        $changed = $true
        foreach ($path in $paths) {
            $source = Join-Path $repo $path
            # A directory's /. suffix copies its contents, avoiding app/app.
            $target = '/app/' + $path
            if (Test-Path -LiteralPath $source -PathType Container) { $source += '/.' }
            Invoke-Checked 'docker' @('cp',$source,"${container}:$target") | Out-Host
        }
        Invoke-Checked 'docker' ($composeArgs + @('start','api')) | Out-Host
    }
    $probe = 'import sys, ssl, urllib.request; ctx=ssl.create_default_context(cafile=sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else None; r=urllib.request.urlopen(sys.argv[1],context=ctx,timeout=4); assert r.status==200; assert b"ready" in r.read()'
    $deadline = [DateTime]::UtcNow.AddSeconds($HealthTimeout)
    $ready = $false
    do {
        & docker exec $container python -c $probe $HealthUrl $HealthCaFile 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $ready) { throw 'API readiness check failed.' }
    Write-Host "Update complete. Backup: $backup"
} catch {
    $failure = $_
    if ($changed -and $backup) {
        Write-Warning 'Update failed. Restoring the previous application; the database backup is retained.'
        try {
            if ($Mode -eq 'Rebuild') {
                $rollbackFile = Join-Path $backup 'rollback.json'
                @{services=@{api=@{image=$oldImage}}} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $rollbackFile -Encoding Ascii
                Invoke-Checked 'docker' ($composeArgs + @('-f',$rollbackFile,'up','-d','--no-deps','--no-build','--wait','--wait-timeout',"$HealthTimeout",'api')) | Out-Host
            } else {
                Invoke-Checked 'docker' ($composeArgs + @('stop','--timeout','100','api')) | Out-Host
                foreach ($path in $paths) {
                    $source = Join-Path $backup $path
                    if (Test-Path -LiteralPath $source -PathType Container) { $source += '/.' }
                    Invoke-Checked 'docker' @('cp',$source,"${container}:/app/$path") | Out-Host
                }
                Invoke-Checked 'docker' ($composeArgs + @('start','api')) | Out-Host
            }
        } catch { Write-Warning "Application rollback failed. Backup is at $backup. $($_.Exception.Message)" }
    }
    throw $failure
} finally {
    [Environment]::SetEnvironmentVariable('APP_IMAGE_TAG',$oldTag,'Process')
    Pop-Location
}
