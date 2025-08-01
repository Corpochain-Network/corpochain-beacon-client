# $env:path should contain a path to editbin.exe

$ErrorActionPreference = "Stop"

git status

Write-Output "   ---"
Write-Output "Setup npm packager"
Write-Output "   ---"
Set-Location -Path ".\npm_windows" -PassThru
npm ci
$Env:Path = $(npm bin) + ";" + $Env:Path

Set-Location -Path "..\..\" -PassThru
git submodule update --init corpochain-gui

Set-Location -Path ".\corpochain-gui" -PassThru

Write-Output "   ---"
Write-Output "Build GUI npm modules"
Write-Output "   ---"
$Env:NODE_OPTIONS = "--max-old-space-size=3000"

Write-Output "lerna clean -y"
lerna clean -y
Write-Output "npm ci"
npm ci
# Audit fix does not currently work with Lerna. See https://github.com/lerna/lerna/issues/1663
# npm audit fix

git status

Write-Output "npm run build"
npm run build
If ($LastExitCode -gt 0){
    Throw "npm run build failed!"
}

# Remove unused packages
Remove-Item node_modules -Recurse -Force

# Other than `corpochain-gui/package/gui`, all other packages are no longer necessary after build.
# Since these unused packages make cache unnecessarily fat, unused packages should be removed.
Write-Output "Remove unused @Corpochain-Network packages to make cache slim"
Remove-Item packages\api -Recurse -Force
Remove-Item packages\api-react -Recurse -Force
Remove-Item packages\core -Recurse -Force
Remove-Item packages\icons -Recurse -Force

# Remove unused fat npm modules from the gui package
Set-Location -Path ".\packages\gui\node_modules" -PassThru
Write-Output "Remove unused node_modules in the gui package to make cache slim more"
Remove-Item electron\dist -Recurse -Force # ~186MB
Remove-Item "@mui" -Recurse -Force # ~71MB
Remove-Item typescript -Recurse -Force # ~63MB

# Remove `packages/gui/node_modules/@Corpochain-Network` because it causes an error on later `electron-packager` command
Remove-Item "@Corpochain-Network" -Recurse -Force
