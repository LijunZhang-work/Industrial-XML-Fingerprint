[CmdletBinding()]
param(
    [string]$PythonEmbedZip
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProductVersion = "0.1.0"
$PythonVersion = "3.12.10"
$PythonArchiveName = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonArchiveName"
$PythonSha256 = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"
$PackageName = "Industrial-XML-Fingerprint-v$ProductVersion-windows-x64"

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot "build"))
$DownloadRoot = Join-Path $BuildRoot "downloads"
$StageRoot = [IO.Path]::GetFullPath((Join-Path $BuildRoot $PackageName))
$ReleaseRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot "release"))
$ArtifactPath = Join-Path $ReleaseRoot "$PackageName.zip"
$HashPath = "$ArtifactPath.sha256"

function Assert-ProjectChild([string]$Path) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    $Prefix = $RepoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $FullPath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the project: $FullPath"
    }
}

foreach ($Path in @($BuildRoot, $StageRoot, $ReleaseRoot, $ArtifactPath, $HashPath)) {
    Assert-ProjectChild $Path
}

New-Item -ItemType Directory -Force -Path $DownloadRoot, $ReleaseRoot | Out-Null

if ($PythonEmbedZip) {
    $RuntimeArchive = [IO.Path]::GetFullPath($PythonEmbedZip)
    if (-not (Test-Path -LiteralPath $RuntimeArchive -PathType Leaf)) {
        throw "Python embeddable archive not found: $RuntimeArchive"
    }
} else {
    $RuntimeArchive = Join-Path $DownloadRoot $PythonArchiveName
    if (-not (Test-Path -LiteralPath $RuntimeArchive -PathType Leaf)) {
        Write-Host "Downloading pinned Python runtime from $PythonUrl"
        Invoke-WebRequest -Uri $PythonUrl -OutFile $RuntimeArchive -UseBasicParsing
    }
}

# The runtime is never extracted until its pinned digest has been verified.
$ActualRuntimeHash = (Get-FileHash -LiteralPath $RuntimeArchive -Algorithm SHA256).Hash.ToUpperInvariant()
if ($ActualRuntimeHash -ne $PythonSha256) {
    throw "Python runtime SHA-256 mismatch. Expected $PythonSha256, got $ActualRuntimeHash"
}

if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
$RuntimeRoot = Join-Path $StageRoot "runtime"
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
Expand-Archive -LiteralPath $RuntimeArchive -DestinationPath $RuntimeRoot

$PthPath = Join-Path $RuntimeRoot "python312._pth"
if (-not (Test-Path -LiteralPath $PthPath -PathType Leaf)) {
    throw "Pinned runtime did not contain python312._pth"
}
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($PthPath, "python312.zip`r`n.`r`n..\app`r`n", $Utf8NoBom)

$AppRoot = Join-Path $StageRoot "app"
New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null

function Copy-SourcePackage([string]$Source, [string]$Destination) {
    $SourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd([IO.Path]::DirectorySeparatorChar)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $SourceRoot -Recurse -File | Where-Object {
        $_.FullName -notmatch "[\\/]__pycache__[\\/]" -and $_.Extension -notin @(".pyc", ".pyo")
    } | ForEach-Object {
        $RelativePath = $_.FullName.Substring($SourceRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        $Target = Join-Path $Destination $RelativePath
        New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($Target)) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $Target
    }
}

Copy-SourcePackage (Join-Path $RepoRoot "xml_fingerprint") (Join-Path $AppRoot "xml_fingerprint")
Copy-SourcePackage (Join-Path $RepoRoot "standards") (Join-Path $AppRoot "standards")

$PackagingRoot = Join-Path $RepoRoot "packaging\windows"
Copy-Item -LiteralPath (Join-Path $PackagingRoot "portable_launcher.py") -Destination $AppRoot
foreach ($Name in @("xml-fingerprint.cmd", "scan-xml.cmd", "scan-example.cmd", "快速开始.txt", "THIRD_PARTY_NOTICES.txt")) {
    Copy-Item -LiteralPath (Join-Path $PackagingRoot $Name) -Destination $StageRoot
}
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
foreach ($Name in @("xml-fingerprint.cmd", "scan-xml.cmd", "scan-example.cmd")) {
    $BatchPath = Join-Path $StageRoot $Name
    $BatchText = [IO.File]::ReadAllText($BatchPath).Replace("`r`n", "`n").Replace("`r", "`n").Replace("`n", "`r`n")
    [IO.File]::WriteAllText($BatchPath, $BatchText, $Utf8NoBom)
}
Copy-Item -LiteralPath (Join-Path $PackagingRoot "examples") -Destination $StageRoot -Recurse
Copy-Item -LiteralPath (Join-Path $RepoRoot "README.md") -Destination $StageRoot

$VersionText = @"
Industrial XML Fingerprint $ProductVersion
Bundled Python runtime $PythonVersion (Windows x64 embeddable package)
Runtime source: $PythonUrl
Runtime SHA-256: $PythonSha256
"@
[IO.File]::WriteAllText((Join-Path $StageRoot "VERSION.txt"), $VersionText.TrimStart() + "`r`n", $Utf8NoBom)

# Development caches and bytecode must never enter the portable package.
Get-ChildItem -LiteralPath $StageRoot -Recurse -Force | Where-Object {
    $_.Name -eq "__pycache__" -or $_.Name -eq ".pytest_cache" -or $_.Extension -in @(".pyc", ".pyo")
} | ForEach-Object {
    throw "Unexpected cache artifact in release staging: $($_.FullName)"
}

foreach ($Path in @($ArtifactPath, $HashPath)) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
}
Compress-Archive -LiteralPath $StageRoot -DestinationPath $ArtifactPath -CompressionLevel Optimal
$ArtifactHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($HashPath, "$ArtifactHash  $([IO.Path]::GetFileName($ArtifactPath))`n", $Utf8NoBom)

$Artifact = Get-Item -LiteralPath $ArtifactPath
Write-Host "Built $($Artifact.FullName)"
Write-Host "Size: $($Artifact.Length) bytes"
Write-Host "SHA-256: $ArtifactHash"
