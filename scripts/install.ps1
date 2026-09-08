# Decision Board bootstrap for Windows.
#
# Run from any PowerShell:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Asks where to put the repository (folder dialog), clones it there or pulls
# if it is already there, checks out the branch, then runs the setup wizard
# (scripts/setup.py), which checks OpenCode, writes the config, lets you pick
# the knowledge source and makes one test call to the model.

param(
    [string]$Repo = "https://github.com/AlexanderHultsch/Decision-Board.git",
    [string]$Branch = "main",
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"

foreach ($tool in @("git", "python")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "[XX] $tool is not on PATH. Install it and open a new terminal." -ForegroundColor Red
        exit 1
    }
}

if (-not $Target) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "Choose the folder that will contain the Decision-Board repository"
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host "No folder chosen." -ForegroundColor Yellow
        exit 1
    }
    $Target = $dialog.SelectedPath
}

$repoDir = Join-Path $Target "Decision-Board"
if (Test-Path (Join-Path $repoDir ".git")) {
    Write-Host "[ok] repository already at $repoDir - pulling $Branch"
    git -C $repoDir fetch origin $Branch
    git -C $repoDir checkout $Branch
    git -C $repoDir pull origin $Branch
} else {
    Write-Host "[ok] cloning into $repoDir"
    git clone --branch $Branch $Repo $repoDir
}

python (Join-Path $repoDir "scripts\setup.py")
