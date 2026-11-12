# Reviewed Scoop entry point. Never accept command strings or arbitrary actions.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ScoopScript,
    [Parameter(Mandatory = $true)]
    [ValidateSet('version', 'search', 'search-json', 'install', 'uninstall', 'upgrade', 'list', 'show')]
    [string] $Action,
    [string] $Package,
    [string] $Query
)

$ErrorActionPreference = 'Stop'
try {
    # Process-local encoding only; preserve metadata when the host is redirected.
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    if (-not [System.IO.Path]::IsPathRooted($ScoopScript)) {
        throw 'Scoop script path must be absolute.'
    }
    $scriptFile = Get-Item -LiteralPath $ScoopScript -ErrorAction Stop
    if ($scriptFile.PSIsContainer -or $scriptFile.Name -ine 'scoop.ps1') {
        throw 'Only the explicitly selected installed Scoop entry point is allowed.'
    }
    $arguments = @()
    $nativeAction = $Action
    if ($Action -in @('search', 'search-json')) {
        if ($Query -notmatch '^[A-Za-z0-9][A-Za-z0-9 ._+@-]{0,99}$' -or $Package) {
            throw 'Invalid search request.'
        }
        $arguments = @($Query)
        if ($Action -eq 'search-json') {
            $global:LASTEXITCODE = 0
            $items = @(& $scriptFile.FullName 'search' @arguments 6>$null)
            $searchSucceeded = $?
            $searchExitCode = [int] $LASTEXITCODE
            if ($searchExitCode -ne 0) { exit $searchExitCode }
            if (-not $searchSucceeded) { exit 1 }
            $packages = @($items | Select-Object -First 200 | ForEach-Object {
                if (-not $_.Name -or -not $_.Source) { throw 'Unsupported native search record.' }
                [PSCustomObject]@{ Name = [string] $_.Name; Source = [string] $_.Source; Version = [string] $_.Version }
            })
            [PSCustomObject]@{ packages = $packages; limited = ($items.Count -gt 200) } | ConvertTo-Json -Depth 4 -Compress
            exit 0
        }
    } elseif ($Action -in @('install', 'uninstall', 'upgrade', 'show')) {
        if ($Package -notmatch '^(main|extras)/[a-z0-9][a-z0-9._-]*$' -or $Query) {
            throw 'A reviewed bucket and exact package token are required.'
        }
        $selectedBucket, $selectedApp = $Package -split '/', 2
        if ($selectedApp -eq 'scoop') { throw 'Manager self-management is not supported.' }
        # Scoop can ignore bucket qualification for installed-app mutations.
        # Inspect its JSON export before dispatching the selected package action.
        $global:LASTEXITCODE = 0
        $exportLines = & $scriptFile.FullName 'export'
        if (-not $? -or $LASTEXITCODE -ne 0) { throw 'Cannot inspect Scoop inventory.' }
        $inventory = ($exportLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
        $bucketRecord = @($inventory.buckets | Where-Object { $_.Name -eq $selectedBucket })
        if ($bucketRecord.Count -ne 1) { throw 'Selected bucket is not configured.' }
        $expectedRemote = if ($selectedBucket -eq 'main') {
            'https://github.com/ScoopInstaller/Main'
        } else {
            'https://github.com/ScoopInstaller/Extras'
        }
        $bucketRemote = ([string] $bucketRecord[0].Source).TrimEnd('/') -replace '\.git$', ''
        if ($bucketRemote -ine $expectedRemote) { throw 'Selected bucket remote does not match its reviewed source.' }
        if ($Action -ne 'show') {
            $installedApp = @($inventory.apps | Where-Object { $_.Name -eq $selectedApp })
            if (($Action -in @('uninstall', 'upgrade')) -and $installedApp.Count -ne 1) {
                throw 'An unambiguous installed package is required.'
            }
            if ($installedApp.Count -gt 1 -or ($installedApp.Count -eq 1 -and
                ($installedApp[0].Source -ne $selectedBucket -or $installedApp[0].Info -match 'Global install'))) {
                throw 'Installed package origin or scope does not match the selected source.'
            }
        }
        if ($Action -eq 'upgrade') { $nativeAction = 'update' }
        if ($Action -eq 'show') { $nativeAction = 'info' }
        $arguments = @($Package)
    } else {
        if ($Package -or $Query) { throw 'Unexpected action arguments.' }
        if ($Action -eq 'version') { $nativeAction = '--version' }
    }
    $global:LASTEXITCODE = 0
    & $scriptFile.FullName $nativeAction @arguments
    $scoopSucceeded = $?
    $nativeExitCode = [int] $LASTEXITCODE
    if ($nativeExitCode -ne 0) { exit $nativeExitCode }
    if (-not $scoopSucceeded) { exit 1 }
    exit 0
} catch {
    # Do not echo input values, credentials, environment values or raw exceptions.
    [Console]::Error.WriteLine('The reviewed Scoop action failed or was refused.')
    exit 1
}
