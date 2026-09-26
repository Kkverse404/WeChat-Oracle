param(
    [switch]$PreserveLocalConfig
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$specPath = Join-Path $repoRoot "packaging\wechat-oracle.spec"
$outputDir = Join-Path $repoRoot "dist\WeChatOracle"
$exePath = Join-Path $outputDir "WeChatOracle.exe"
$localConfigPath = Join-Path $outputDir ".env"
$configBackupPath = $null

if ($PreserveLocalConfig -and (Test-Path -LiteralPath $localConfigPath -PathType Leaf)) {
    $configBackupPath = Join-Path ([System.IO.Path]::GetTempPath()) (
        "wechat-oracle-config-" + [guid]::NewGuid().ToString("N") + ".env"
    )
    Copy-Item -LiteralPath $localConfigPath -Destination $configBackupPath
}

Push-Location $repoRoot
try {
    uv sync --group dev
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

    uv run pyinstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        throw "Expected executable was not created: $exePath"
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $outputDir "data\personas") | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination $outputDir -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "README.zh-CN.md") -Destination $outputDir -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination $outputDir -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination $outputDir -Force

    $sitePackages = Join-Path $repoRoot ".venv\Lib\site-packages"
    $wxLicense = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "wx4py-*.dist-info" |
        ForEach-Object { Join-Path $_.FullName "licenses\LICENSE" } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $wxLicense) {
        throw "wx4py license file was not found under $sitePackages"
    }
    $licenseDir = Join-Path $outputDir "licenses"
    New-Item -ItemType Directory -Force -Path $licenseDir | Out-Null
    Copy-Item -LiteralPath $wxLicense -Destination (Join-Path $licenseDir "wx4py-LICENSE.txt") -Force

    $cryptographyLicenses = Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "cryptography-*.dist-info" |
        ForEach-Object { Join-Path $_.FullName "licenses" } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
        ForEach-Object { Get-ChildItem -LiteralPath $_ -File }
    if (-not $cryptographyLicenses) {
        throw "cryptography license files were not found under $sitePackages"
    }
    foreach ($license in $cryptographyLicenses) {
        Copy-Item -LiteralPath $license.FullName -Destination (
            Join-Path $licenseDir ("cryptography-" + $license.Name)
        ) -Force
    }

    $forbidden = Get-ChildItem -LiteralPath $outputDir -Recurse -Force -File | Where-Object {
        $_.Name -eq ".env" -or
        $_.Extension -in ".db", ".sqlite", ".wal" -or
        $_.Name -like "*.db-wal" -or
        $_.Name -like "wx4py.log"
    }
    if ($forbidden) {
        throw "Sensitive runtime files entered the portable output: $($forbidden.FullName -join ', ')"
    }

    Write-Host "Portable build ready: $exePath"
    Write-Host "Distribute the entire dist\WeChatOracle directory, not only the EXE."
}
finally {
    if ($configBackupPath -and (Test-Path -LiteralPath $configBackupPath -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
        Copy-Item -LiteralPath $configBackupPath -Destination $localConfigPath -Force
        Remove-Item -LiteralPath $configBackupPath -Force
        Write-Host "Restored local runtime configuration after the clean build."
    }
    Pop-Location
}
