$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot
$distDir = Join-Path $repoRoot "dist"
$buildDir = Join-Path $repoRoot "build"
$hostPython = (Get-Command python -ErrorAction Stop).Source

foreach ($generatedPath in @($buildDir, $distDir)) {
    if (Test-Path -LiteralPath $generatedPath) {
        Remove-Item -LiteralPath $generatedPath -Recurse -Force
    }
}

# Build verification order: clean build, twine check, package tests, install.
& $hostPython -m build --outdir $distDir
if ($LASTEXITCODE -ne 0) {
    throw "The clean package build failed."
}

$expectedWheelName = "hydraclaim-0.2.0-py3-none-any.whl"
$expectedSdistName = "hydraclaim-0.2.0.tar.gz"
$expectedArtifactNames = @($expectedWheelName, $expectedSdistName) | Sort-Object
$actualArtifactNames = @(
    Get-ChildItem -LiteralPath $distDir -File |
        Select-Object -ExpandProperty Name |
        Sort-Object
)
if (($actualArtifactNames -join "`n") -cne ($expectedArtifactNames -join "`n")) {
    throw (
        "Expected exactly these package files: {0}; found: {1}." -f
        ($expectedArtifactNames -join ", "),
        ($actualArtifactNames -join ", ")
    )
}

$matchingWheels = @(
    Get-ChildItem -LiteralPath $distDir -File |
        Where-Object Name -eq $expectedWheelName
)
if ($matchingWheels.Count -ne 1) {
    throw "Expected exactly one $expectedWheelName in $distDir; found $($matchingWheels.Count)."
}

$wheelPath = $matchingWheels[0].FullName
$sdistPath = Join-Path $distDir $expectedSdistName
& $hostPython -m twine check $wheelPath $sdistPath
if ($LASTEXITCODE -ne 0) {
    throw "Twine rejected the clean package artifacts."
}

& $hostPython -m pytest tests/test_package_metadata.py -q
if ($LASTEXITCODE -ne 0) {
    throw "Package metadata and archive tests failed after the clean build."
}

$venv = Join-Path $repoRoot ".venv-package-test"
if (Test-Path -LiteralPath $venv) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}

& $hostPython -m venv $venv
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the clean virtual environment at $venv."
}

$venvBin = if ($env:OS -eq "Windows_NT") { "Scripts" } else { "bin" }
$pythonName = if ($env:OS -eq "Windows_NT") { "python.exe" } else { "python" }
$cliName = if ($env:OS -eq "Windows_NT") { "hydraclaim.exe" } else { "hydraclaim" }
$python = Join-Path (Join-Path $venv $venvBin) $pythonName
$cli = Join-Path (Join-Path $venv $venvBin) $cliName
if (-not (Test-Path -LiteralPath $python)) {
    throw "The virtual environment does not contain $python."
}

$requirementsPath = Join-Path $repoRoot "requirements.txt"
$runtimeRequirements = @(
    Get-Content -LiteralPath $requirementsPath |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
)
if ($runtimeRequirements.Count -eq 0) {
    throw "No runtime requirements found in $requirementsPath."
}
& $python -m pip install --disable-pip-version-check $runtimeRequirements
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the HydraClaim runtime dependencies."
}

& $python -m pip install --disable-pip-version-check --no-index --no-deps $wheelPath
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

$fixtureDir = Join-Path $venv "offline-fixture"
Write-Output "Checking offline fixture generation"
& $cli generate --out $fixtureDir
if ($LASTEXITCODE -ne 0) {
    throw "Offline fixture generation failed."
}
$fixtureFiles = @(Get-ChildItem -LiteralPath $fixtureDir -Filter "*.json" -File)
if ($fixtureFiles.Count -lt 1) {
    throw "Offline fixture generation wrote no JSON files."
}
$fixturePath = $fixtureFiles[0].FullName

function Invoke-ExpectedConfigFailure {
    param(
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [Parameter(Mandatory = $true)] [string] $ExpectedSetting
    )

    $outputLines = & $cli @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $output = $outputLines | Out-String
    if ($exitCode -eq 0) {
        throw "Expected configuration failure for: hydraclaim $($Arguments -join ' ')"
    }
    if ($output -notmatch [regex]::Escape($ExpectedSetting)) {
        throw (
            "Expected $ExpectedSetting in configuration failure for " +
            "hydraclaim $($Arguments -join ' '); got: $output"
        )
    }
    Write-Output "Configuration check failed as expected: hydraclaim $($Arguments -join ' ')"
}

$oldHydraUrl = [Environment]::GetEnvironmentVariable("HYDRADB_URL", "Process")
$oldHydraToken = [Environment]::GetEnvironmentVariable("HYDRADB_TOKEN", "Process")
$oldLlmKey = [Environment]::GetEnvironmentVariable("LLM_API_KEY", "Process")
try {
    # These commands must fail before HydraDB access when its settings are empty.
    $env:HYDRADB_URL = ""
    $env:HYDRADB_TOKEN = ""
    foreach ($arguments in @(
        @("ask", "Who owns launch?"),
        @("ingest", $fixturePath),
        @("schema", "--verify"),
        @("benchmark", $fixturePath),
        @("serve", "--port", "0")
    )) {
        Invoke-ExpectedConfigFailure -Arguments $arguments -ExpectedSetting "HYDRADB_URL"
    }

    # These commands must fail before an LLM request when its key is empty.
    $env:HYDRADB_URL = "http://127.0.0.1:8443"
    $env:HYDRADB_TOKEN = "local-development-token-32-bytes"
    $env:LLM_API_KEY = ""
    foreach ($arguments in @(
        @("ask", "Who owns launch?", "--llm"),
        @("extract", $fixturePath),
        @("pipeline", $fixturePath)
    )) {
        Invoke-ExpectedConfigFailure -Arguments $arguments -ExpectedSetting "LLM_API_KEY"
    }
}
finally {
    if ($null -eq $oldHydraUrl) {
        Remove-Item Env:HYDRADB_URL -ErrorAction SilentlyContinue
    }
    else {
        $env:HYDRADB_URL = $oldHydraUrl
    }
    if ($null -eq $oldHydraToken) {
        Remove-Item Env:HYDRADB_TOKEN -ErrorAction SilentlyContinue
    }
    else {
        $env:HYDRADB_TOKEN = $oldHydraToken
    }
    if ($null -eq $oldLlmKey) {
        Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
    }
    else {
        $env:LLM_API_KEY = $oldLlmKey
    }
}

Write-Output "Clean HydraClaim 0.2.0 installation passed all CLI, fixture, and configuration checks."
