# $env:path should contain a path to editbin.exe

$ErrorActionPreference = "Stop"

mkdir build_scripts\win_build

git status
git submodule

if (-not (Test-Path env:CORPOCHAIN_INSTALLER_VERSION)) {
  $env:CORPOCHAIN_INSTALLER_VERSION = '0.0.0'
  Write-Output "WARNING: No environment variable CORPOCHAIN_INSTALLER_VERSION set. Using 0.0.0"
}
Write-Output "Corpochain Version is: $env:CORPOCHAIN_INSTALLER_VERSION"
Write-Output "   ---"

Write-Output "   ---"
Write-Output "Use pyinstaller to create corpochain .exe's"
Write-Output "   ---"
$SPEC_FILE = (python -c 'import corpochain; print(corpochain.PYINSTALLER_SPEC_PATH)') -join "`n"
pyinstaller --log-level INFO $SPEC_FILE

Write-Output "   ---"
Write-Output "Copy corpochain executables to corpochain-gui\"
Write-Output "   ---"
Copy-Item "dist\daemon" -Destination "..\corpochain-gui\packages\gui\" -Recurse

Write-Output "   ---"
Write-Output "Setup npm packager"
Write-Output "   ---"
Set-Location -Path ".\npm_windows" -PassThru
npm ci
$Env:Path = $(npm bin) + ";" + $Env:Path

Set-Location -Path "..\..\" -PassThru

Write-Output "   ---"
Write-Output "Prepare Electron packager"
Write-Output "   ---"
$Env:NODE_OPTIONS = "--max-old-space-size=3000"

# Change to the GUI directory
Set-Location -Path "corpochain-gui\packages\gui" -PassThru

Write-Output "   ---"
Write-Output "Increase the stack for corpochain command for (corpochain plots create) chiapos limitations"
# editbin.exe needs to be in the path
$editbinPath = Get-ChildItem -Path "C:\Program Files\Microsoft Visual Studio\2022" -Filter editbin.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $editbinPath) {
    Write-Error "editbin.exe not found"
    exit 1
}

& $editbinPath /STACK:8000000 daemon\corpochain.exe
Write-Output "   ---"

$packageVersion = "$env:CORPOCHAIN_INSTALLER_VERSION"
$packageName = "corpochain-gui-$packageVersion"

Write-Output "packageName is $packageName"

Write-Output "   ---"
Write-Output "fix version in package.json"
choco install jq
cp package.json package.json.orig
jq --arg VER "$env:CORPOCHAIN_INSTALLER_VERSION" '.version=$VER' package.json > temp.json
rm package.json
mv temp.json package.json
Write-Output "   ---"

Write-Output "   ---"
Write-Output "electron-builder"
electron-builder build --win --x64 --config.productName="Corpochain Beacon Client"
Get-ChildItem dist\win-unpacked\resources
Write-Output "   ---"

Write-Output "   ---"
Write-Output "Moving final installers to expected location"
Write-Output "   ---"
Copy-Item ".\dist\win-unpacked" -Destination "$env:GITHUB_WORKSPACE\corpochain-gui\corpochain-gui-win32-x64" -Recurse
mkdir "$env:GITHUB_WORKSPACE\corpochain-gui\release-builds\windows-installer" -ea 0
Copy-Item ".\dist\corpochain-beacon-client-$packageVersion.exe" -Destination "$env:GITHUB_WORKSPACE\corpochain-gui\release-builds\windows-installer\corpochain-beacon-client_${packageVersion}_amd64.exe"

Write-Output "   ---"
Write-Output "Windows Installer complete"
Write-Output "   ---"
