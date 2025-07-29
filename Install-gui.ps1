$ErrorActionPreference = "Stop"
$SUBMODULE_BRANCH = $args[0]

if ($null -eq (Get-ChildItem env:VIRTUAL_ENV -ErrorAction SilentlyContinue))
{
    Write-Output "This script requires that the Corpochain Python virtual environment is activated."
    Write-Output "Execute '.\venv\Scripts\Activate.ps1' before running."
    Exit 1
}

if ($null -eq (Get-Command node -ErrorAction SilentlyContinue))
{
    Write-Output "Unable to find Node.js"
    Exit 1
}

Write-Output "Running 'git submodule update --init --recursive'."
Write-Output ""
git submodule update --init --recursive
if ( $SUBMODULE_BRANCH ) {
  git fetch --all
  git reset --hard $SUBMODULE_BRANCH
  Write-Output ""
  Write-Output "Building the GUI with branch $SUBMODULE_BRANCH"
  Write-Output ""
}


Push-Location
try {
    Set-Location corpochain-gui

    $ErrorActionPreference = "SilentlyContinue"
    npm ci --loglevel=error
    npm audit fix
    npm run build
    py ..\installhelper.py

    Write-Output ""
    Write-Output "Corpochain Beacon Client Install-gui.ps1 completed."
    Write-Output ""
    Write-Output "Type 'cd corpochain-gui' and then 'npm run electron' to start the GUI."
} finally {
    Pop-Location
}
