[CmdletBinding()]
param(
    [string]$DotnetPath,
    [string]$OutputDirectory,
    [string]$InstallDirectory,
    [switch]$PrepareOnly,
    [switch]$NoRestore,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$installRoot = $projectRoot
if ($InstallDirectory) {
    if (-not $Install) { throw '-InstallDirectory requires -Install.' }
    $installRoot = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'interface.json') -PathType Leaf)) {
        throw 'The installation directory must contain an existing client interface.json.'
    }
}
$manifestPath = Join-Path $projectRoot 'frontend/upstream.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$utf8 = New-Object Text.UTF8Encoding($false)

function Get-ContainedPath {
    param([string]$Root, [string]$RelativePath)
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $resolved = [IO.Path]::GetFullPath((Join-Path $resolvedRoot $RelativePath))
    if (-not $resolved.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must stay inside ${resolvedRoot}: $RelativePath"
    }
    return $resolved
}

function Write-Utf8 {
    param([string]$Path, [string]$Value)
    [IO.File]::WriteAllText($Path, $Value, $utf8)
}

function Assert-FrontendStopped {
    # Keep the script ASCII-compatible with Windows PowerShell 5.1's source encoding.
    $renamedFrontendName = 'MAA' + [char]0x9020 + [char]0x68A6 + [char]0x897F + [char]0x6E38 + '4'
    foreach ($process in @(Get-Process -Name 'MFAAvalonia', $renamedFrontendName -ErrorAction SilentlyContinue)) {
        try { $processPath = $process.Path } catch { $processPath = $null }
        if (-not $processPath) {
            throw "Cannot verify the location of running frontend PID $($process.Id). Close it before using -Install."
        }
        if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($processPath)).Equals($installRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The project frontend is running (PID $($process.Id)). Close it before using -Install; no process was stopped."
        }
    }
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'dotnet.exe'" -ErrorAction SilentlyContinue)) {
        if ($process.CommandLine -and $process.CommandLine.IndexOf((Join-Path $installRoot 'MFAAvalonia.dll'), [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw "The project frontend is running through dotnet (PID $($process.ProcessId)). Close it before using -Install."
        }
    }
}

if ($PrepareOnly -and $Install) { throw '-PrepareOnly cannot be combined with -Install.' }
if ($Install) { Assert-FrontendStopped }

$cacheRoot = Get-ContainedPath $projectRoot '.runtime-cache'
$sourceRoot = Get-ContainedPath $projectRoot $manifest.cache_directory
$archivePath = Get-ContainedPath $cacheRoot 'mfa-source-1080d3e.zip'
New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null

if (-not (Test-Path -LiteralPath $archivePath)) {
    Write-Host "Downloading MFAAvalonia $($manifest.commit)..."
    $partialArchive = $archivePath + '.download'
    Invoke-WebRequest -Uri $manifest.archive_url -OutFile $partialArchive
    $actualHash = (Get-FileHash -LiteralPath $partialArchive -Algorithm SHA256).Hash
    if ($actualHash -ne $manifest.archive_sha256) { throw "Downloaded source archive checksum mismatch: $actualHash" }
    Move-Item -LiteralPath $partialArchive -Destination $archivePath
}
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
if ($archiveHash -ne $manifest.archive_sha256) {
    throw "Source archive checksum mismatch. Expected $($manifest.archive_sha256), found $archiveHash."
}

if (-not (Test-Path -LiteralPath $sourceRoot)) {
    $extractRoot = Get-ContainedPath $cacheRoot ('mfa-extract-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $extractRoot | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $unpackedRoot = Get-ContainedPath $extractRoot ('MFAAvalonia-' + $manifest.commit)
    if (-not (Test-Path -LiteralPath (Join-Path $unpackedRoot $manifest.project))) {
        throw "The source archive does not contain the expected project: $($manifest.project)"
    }
    # Both move targets are checked against the intended workspace cache above.
    Move-Item -LiteralPath $unpackedRoot -Destination $sourceRoot
    Remove-Item -LiteralPath $extractRoot
}

$sourceMarker = Join-Path $sourceRoot '.script-studio-upstream'
if ((Test-Path -LiteralPath $sourceMarker) -and (Get-Content -LiteralPath $sourceMarker -Raw).Trim() -ne $manifest.commit) {
    throw "Source cache belongs to a different upstream commit: $sourceRoot"
}
Write-Utf8 $sourceMarker ($manifest.commit + "`n")

$patchPaths = @($manifest.patches)
$patchDefinitions = @(
    foreach ($relativePath in $patchPaths) {
        $path = Get-ContainedPath $projectRoot $relativePath
        Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
)
for ($patchIndex = 0; $patchIndex -lt $patchPaths.Count; $patchIndex++) {
    $patchRelativePath = $patchPaths[$patchIndex]
    $patch = $patchDefinitions[$patchIndex]
    $targetPath = Get-ContainedPath $sourceRoot $patch.path
    $text = [IO.File]::ReadAllText($targetPath).Replace("`r`n", "`n")
    $find = $patch.find.Replace("`r`n", "`n")
    $replacement = $patch.replace.Replace("`r`n", "`n")
    if ($text.Contains($replacement)) {
        Write-Host "Patch already applied: $($patch.description)"
        continue
    }
    # Later exact patches can replace or extend an earlier fragment.
    # If their composed result is present, reapplying the earlier fragment would
    # either fail or undo part of the later patch on a second build.
    $superseded = $false
    for ($laterIndex = $patchIndex + 1; $laterIndex -lt $patchPaths.Count; $laterIndex++) {
        $later = $patchDefinitions[$laterIndex]
        if ($later.path -ne $patch.path) { continue }
        $laterFind = $later.find.Replace("`r`n", "`n")
        $laterReplacement = $later.replace.Replace("`r`n", "`n")
        $laterCoversEarlier = $laterFind.Contains($replacement) -and $text.Contains($laterReplacement)
        $laterInsideEarlier = [regex]::Matches($replacement, [regex]::Escape($laterFind)).Count -eq 1 `
            -and $text.Contains($replacement.Replace($laterFind, $laterReplacement))
        if ($laterCoversEarlier -or $laterInsideEarlier) {
            Write-Host "Patch superseded by an already-applied later patch: $($patch.description)"
            $superseded = $true
            break
        }
    }
    if ($superseded) { continue }
    if ([regex]::Matches($text, [regex]::Escape($find)).Count -ne 1) {
        $previousProperty = $patch.PSObject.Properties['previous_replacements']
        $previous = @()
        if ($previousProperty) {
            $previous = @($previousProperty.Value | Where-Object { [regex]::Matches($text, [regex]::Escape($_.Replace("`r`n", "`n"))).Count -eq 1 })
        }
        if ($previous.Count -ne 1) { throw "Patch anchor is absent or ambiguous: $patchRelativePath ($($patch.path)). The pinned source must match exactly." }
        $find = $previous[0].Replace("`r`n", "`n")
    }
    Write-Utf8 $targetPath ($text.Replace($find, $replacement))
    Write-Host "Applied: $($patch.description)"
}

$overlayRoot = Get-ContainedPath $projectRoot $manifest.overlay_directory
$coreRoot = Get-ContainedPath $sourceRoot 'MFAAvalonia'
$retiredProperty = $manifest.PSObject.Properties['retired_overlay_files']
if ($retiredProperty) {
    foreach ($relative in $retiredProperty.Value) {
        $retired = Get-ContainedPath $coreRoot $relative
        if (Test-Path -LiteralPath $retired -PathType Leaf) { Remove-Item -LiteralPath $retired }
    }
}
if (Test-Path -LiteralPath $overlayRoot) {
    foreach ($file in Get-ChildItem -LiteralPath $overlayRoot -Recurse -File) {
        $relative = $file.FullName.Substring($overlayRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        $destination = Get-ContainedPath $coreRoot $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
} elseif (-not $PrepareOnly) {
    throw "Script studio source overlay is missing: $overlayRoot"
}

$applicationIcon = Get-ContainedPath $projectRoot $manifest.application_icon
if (-not (Test-Path -LiteralPath $applicationIcon -PathType Leaf)) {
    throw "Application icon is missing: $applicationIcon"
}
# Both the Windows executable and Avalonia's embedded window/tray icon use this asset.
Copy-Item -LiteralPath $applicationIcon -Destination (Join-Path $coreRoot 'Assets/logo.ico') -Force
# Copy-Item preserves the source timestamp. Refresh the cached copy so Avalonia's
# incremental resource build cannot keep the old icon when the supplied ICO is older.
[IO.File]::SetLastWriteTimeUtc((Join-Path $coreRoot 'Assets/logo.ico'), [DateTime]::UtcNow)

Write-Host "Prepared source: $sourceRoot"
if ($PrepareOnly) { return }

foreach ($relative in @('Views/Pages/ScriptStudioView.axaml.cs', 'ViewModels/Pages/ScriptStudioViewModel.cs', 'Services/ScriptStudioClient.cs')) {
    if (-not (Test-Path -LiteralPath (Get-ContainedPath $coreRoot $relative))) {
        throw "The source overlay is incomplete: $relative"
    }
}

if (-not $DotnetPath) {
    $bundledSdk = Get-ContainedPath $cacheRoot 'dotnet-sdk/dotnet.exe'
    if (Test-Path -LiteralPath $bundledSdk) { $DotnetPath = $bundledSdk }
    else {
        $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
        if ($dotnetCommand) { $DotnetPath = $dotnetCommand.Source }
    }
}
if (-not $DotnetPath -or -not (Test-Path -LiteralPath $DotnetPath)) {
    throw 'A .NET 10 SDK is required. Pass -DotnetPath or install it in .runtime-cache/dotnet-sdk.'
}
$sdkVersion = & $DotnetPath --version
if ($LASTEXITCODE -ne 0 -or $sdkVersion -notmatch '^10\.') {
    throw "A .NET 10 SDK is required; SDK lookup returned: $sdkVersion"
}

if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'dist/script-studio' }
elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path $projectRoot $OutputDirectory }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
if ($InstallDirectory -and -not $installRoot.Equals($projectRoot, [StringComparison]::OrdinalIgnoreCase) -and
    ($outputRoot.Equals($installRoot, [StringComparison]::OrdinalIgnoreCase) -or
     $outputRoot.StartsWith($installRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
    throw 'Build output must be outside the separate installation directory.'
}
if ($outputRoot.Equals($projectRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $outputRoot.Equals($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $outputRoot.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must be separate from the project root and the source cache.'
}
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$publishArguments = @('publish', (Join-Path $sourceRoot $manifest.project), '-c', 'Release', '-r', $manifest.runtime_identifier,
    '--self-contained', 'false', '--output', $outputRoot, '--nologo')
if ($NoRestore) { $publishArguments += '--no-restore' }
Write-Host "Building with SDK $sdkVersion..."
& $DotnetPath @publishArguments
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE. The running frontend was not changed." }
if (-not (Test-Path -LiteralPath (Join-Path $outputRoot 'MFAAvalonia.exe'))) {
    throw "Publish finished without the expected frontend executable: $outputRoot"
}

Copy-Item -LiteralPath (Join-Path $sourceRoot 'LICENSE') -Destination (Join-Path $outputRoot 'LICENSE-MFAAvalonia.txt') -Force
$sourceHashes = [ordered]@{}
$sourceFiles = @('frontend/upstream.json') + @($manifest.patches)
$sourceFiles += @(Get-ChildItem -LiteralPath $overlayRoot -Recurse -File | ForEach-Object { $_.FullName.Substring($projectRoot.Length + 1).Replace('\', '/') })
foreach ($relative in $sourceFiles) {
    $sourceHashes[$relative] = (Get-FileHash -LiteralPath (Get-ContainedPath $projectRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$binaryHashes = [ordered]@{}
foreach ($relative in @('MFAAvalonia.dll', 'libs/MFAAvalonia.Core.dll')) {
    $binaryHashes[$relative] = (Get-FileHash -LiteralPath (Get-ContainedPath $outputRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$buildInfo = [ordered]@{
    repository = $manifest.repository
    commit = $manifest.commit
    archive_sha256 = $manifest.archive_sha256
    core_version = $manifest.core_version
    sdk_version = [string]$sdkVersion
    runtime_identifier = $manifest.runtime_identifier
    application_icon = $manifest.application_icon
    application_icon_sha256 = (Get-FileHash -LiteralPath $applicationIcon -Algorithm SHA256).Hash.ToLowerInvariant()
    source_url = "https://github.com/$($manifest.repository)/tree/$($manifest.commit)"
    overlay_directory = $manifest.overlay_directory
    patches = $manifest.patches
    source_sha256 = $sourceHashes
    binary_sha256 = $binaryHashes
}
Write-Utf8 (Join-Path $outputRoot 'script-studio-build.json') (($buildInfo | ConvertTo-Json -Depth 5) + "`n")

if ($Install) {
    Assert-FrontendStopped
    $backupRelative = 'backup/frontend-script-studio-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $backupRoot = Get-ContainedPath $installRoot $backupRelative
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $installedFiles = @()
    foreach ($file in Get-ChildItem -LiteralPath $outputRoot -Recurse -File) {
        $relative = $file.FullName.Substring($outputRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        $normalized = $relative.Replace([IO.Path]::DirectorySeparatorChar, [char]'/')
        # Install frontend binaries only; project resources, Python and user configuration stay outside this list.
        if ($normalized -notmatch '^(MFAAvalonia\.(exe|dll|deps\.json|runtimeconfig\.json)|libloader\.dll|script-studio-build\.json|LICENSE-MFAAvalonia\.txt)$' -and
            $normalized -notmatch '^(libs/|runtimes/win-x64/native/)') { continue }
        $destination = Get-ContainedPath $installRoot $relative
        $existed = Test-Path -LiteralPath $destination
        if ($existed) {
            $backupPath = Get-ContainedPath $backupRoot $relative
            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($backupPath)) -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $backupPath -Force
        }
        $installedFiles += [ordered]@{ path = $normalized; existed = $existed }
    }
    Write-Utf8 (Join-Path $backupRoot 'installation.json') (($installedFiles | ConvertTo-Json -Depth 4) + "`n")
    foreach ($entry in $installedFiles) {
        $destination = Get-ContainedPath $installRoot $entry.path
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        Copy-Item -LiteralPath (Get-ContainedPath $outputRoot $entry.path) -Destination $destination -Force
    }
    Write-Host "Installed frontend. Previous binaries: $backupRoot"
}
Write-Host "Frontend build output: $outputRoot"
