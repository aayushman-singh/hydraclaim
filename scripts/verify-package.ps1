$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distDir = Join-Path $repoRoot "dist"
$expectedWheelName = "hydraclaim-0.2.0-py3-none-any.whl"
$matchingWheels = @(
    Get-ChildItem -LiteralPath $distDir -File |
        Where-Object Name -eq $expectedWheelName
)
if ($matchingWheels.Count -ne 1) {
    throw "Expected exactly one $expectedWheelName in $distDir; found $($matchingWheels.Count)."
}

$wheelPath = $matchingWheels[0].FullName
$venv = Join-Path $repoRoot ".venv-package-test"
if (Test-Path -LiteralPath $venv) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}

$hostPython = (Get-Command python -ErrorAction Stop).Source
& $hostPython -m venv $venv
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the clean virtual environment at $venv."
}

$python = Join-Path $venv "Scripts/python.exe"
$cli = Join-Path $venv "Scripts/hydraclaim.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "The virtual environment does not contain $python."
}

& $python -m pip install --disable-pip-version-check $wheelPath
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the exact release wheel $wheelPath."
}
if (-not (Test-Path -LiteralPath $cli)) {
    throw "The clean installation does not contain $cli."
}

$version = (& $cli --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne "hydraclaim 0.2.0") {
    throw "Expected 'hydraclaim 0.2.0' from the clean installation; got '$version'."
}
Write-Output $version

$commands = @(
    "ask",
    "serve",
    "schema",
    "generate",
    "ingest",
    "extract",
    "evaluate",
    "pipeline",
    "benchmark",
    "longmemeval"
)
foreach ($command in $commands) {
    Write-Output "Checking hydraclaim $command --help"
    & $cli $command --help
    if ($LASTEXITCODE -ne 0) {
        throw "Help command failed: hydraclaim $command --help"
    }
}

Write-Output "Clean HydraClaim 0.2.0 installation passed all ten CLI help checks."
