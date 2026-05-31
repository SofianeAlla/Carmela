# Launch the Electron desktop app (which spawns the Python sidecar itself).
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location (Join-Path $root "desktop")
npm start
Pop-Location
