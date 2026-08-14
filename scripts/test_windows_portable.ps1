[CmdletBinding()]
param(
    [string]$Artifact = (Join-Path $PSScriptRoot "..\release\Industrial-XML-Fingerprint-v0.1.0-windows-x64.zip")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Artifact = [IO.Path]::GetFullPath($Artifact)
if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
    throw "Portable artifact not found: $Artifact"
}

$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$SmokeRoot = Join-Path $TempRoot ("Industrial XML & (portable smoke) " + [guid]::NewGuid().ToString("N"))
$SmokeRoot = [IO.Path]::GetFullPath($SmokeRoot)
$TempPrefix = $TempRoot + [IO.Path]::DirectorySeparatorChar
if (-not $SmokeRoot.StartsWith($TempPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not [IO.Path]::GetFileName($SmokeRoot).StartsWith("Industrial XML & (portable smoke) ")) {
    throw "Unsafe smoke directory: $SmokeRoot"
}

function Invoke-Batch([string]$BatchPath, [string[]]$Arguments, [string]$WorkingDirectory) {
    $Quoted = @('"' + $BatchPath + '"')
    $Quoted += @($Arguments | ForEach-Object { '"' + $_.Replace('"', '""') + '"' })
    $CommandLine = '"' + ($Quoted -join ' ') + '"'

    $StartInfo = New-Object Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $env:ComSpec
    $StartInfo.Arguments = "/d /s /c $CommandLine"
    $StartInfo.WorkingDirectory = $WorkingDirectory
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.CreateNoWindow = $true

    $Process = New-Object Diagnostics.Process
    $Process.StartInfo = $StartInfo
    [void]$Process.Start()
    $Process.StandardInput.WriteLine("")
    $Process.StandardInput.Close()
    if (-not $Process.WaitForExit(30000)) {
        $Process.Kill()
        throw "Portable batch smoke timed out: $BatchPath"
    }
    [pscustomobject]@{
        ExitCode = $Process.ExitCode
        Stdout = $Process.StandardOutput.ReadToEnd()
        Stderr = $Process.StandardError.ReadToEnd()
    }
}

New-Item -ItemType Directory -Path $SmokeRoot | Out-Null
try {
    Expand-Archive -LiteralPath $Artifact -DestinationPath $SmokeRoot
    $PackageRoot = Join-Path $SmokeRoot "Industrial-XML-Fingerprint-v0.1.0-windows-x64"
    $InputName = "example & mkdir CMD_INJECTION_MARKER & (demo).xml"
    $InputPath = Join-Path $PackageRoot $InputName
    Copy-Item -LiteralPath (Join-Path $PackageRoot "examples\synthetic-plcopen-demo.xml") -Destination $InputPath

    $Marker = Join-Path $PackageRoot "CMD_INJECTION_MARKER"
    $Launcher = Invoke-Batch (Join-Path $PackageRoot "scan-xml.cmd") @($InputPath) $PackageRoot
    $ReportRoot = Join-Path $PackageRoot ("reports\" + [IO.Path]::GetFileNameWithoutExtension($InputName))
    $Report = Join-Path $ReportRoot "fingerprint_report.json"

    $GenericOut = Join-Path $PackageRoot "generic & (report)"
    $Generic = Invoke-Batch (Join-Path $PackageRoot "xml-fingerprint.cmd") @("scan", $InputPath, "--profiles", "all", "--out", $GenericOut) $PackageRoot
    $GenericReport = Join-Path $GenericOut "fingerprint_report.json"
    $Caches = @(Get-ChildItem -LiteralPath $PackageRoot -Recurse -Force | Where-Object {
        $_.Name -in @("__pycache__", ".pytest_cache") -or $_.Extension -in @(".pyc", ".pyo")
    })

    [pscustomobject]@{
        LauncherExit = $Launcher.ExitCode
        LauncherReport = Test-Path -LiteralPath $Report -PathType Leaf
        GenericExit = $Generic.ExitCode
        GenericReport = Test-Path -LiteralPath $GenericReport -PathType Leaf
        InjectionMarker = Test-Path -LiteralPath $Marker
        CacheEntries = $Caches.Count
    } | Format-List

    if ($Launcher.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $Report -PathType Leaf)) {
        throw "scan-xml.cmd metacharacter regression failed: $($Launcher.Stderr)"
    }
    if ($Generic.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $GenericReport -PathType Leaf)) {
        throw "xml-fingerprint.cmd metacharacter regression failed: $($Generic.Stderr)"
    }
    if (Test-Path -LiteralPath $Marker) {
        throw "CMD injection marker was created"
    }
    if ($Caches.Count -ne 0) {
        throw "Portable execution created bytecode/test caches"
    }
} finally {
    if (Test-Path -LiteralPath $SmokeRoot) {
        Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
    }
}
