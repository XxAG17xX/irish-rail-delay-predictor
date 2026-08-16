# build_lambda.ps1 — stage the Lambda deployment package into build/lambda.
#
# Modules are COPIED from src/ at build time rather than the repo keeping a second copy.
# One source of truth; the package is generated. Duplicating source into a lambda/
# directory is how the feature lists diverged in D35 — the trained artifact carried a
# feature the comparison excluded, nothing detected it, and it took a human reading both
# files to notice.
#
# Only the modules the handler actually imports are staged. train_quantile.py and friends
# would drag in lightgbm and pyarrow, which are not in requirements-lambda.txt and would
# blow the 250 MB unzipped limit for code that never runs.
#
#   powershell scripts\build_lambda.ps1
#   sam deploy --guided --region eu-west-1 --template-file infra/poller.yaml

$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root "build\lambda"
$python = Join-Path $root ".venv\Scripts\python.exe"

# --- clean
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path $build | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $build "config") | Out-Null

# --- modules the handler transitively imports
#     lambda_poll -> poll_live -> {sinks, hostlock, backfill}
#                 -> backfill  -> hostlock
$modules = @(
    "lambda_poll.py",
    "poll_live.py",
    "sinks.py",
    "backfill.py",
    "hostlock.py"
)
foreach ($m in $modules) {
    $src = Join-Path $root "src\$m"
    if (-not (Test-Path $src)) { throw "missing module: $src" }
    Copy-Item $src $build
}
Write-Host "  staged $($modules.Count) modules"

# --- data the handler reads at runtime. Baked in rather than fetched from S3: the
#     station list changes on a scale of years, and shipping it removes both a
#     getAllStationsXML call per cold start and an S3 read permission.
Copy-Item (Join-Path $root "config\poll_stations.toml") (Join-Path $build "config")
$stations = Join-Path $root "data\live\stations.json"
if (-not (Test-Path $stations)) {
    throw "missing $stations - run: python src\poll_live.py --once --max-stations 1"
}
Copy-Item $stations $build
Write-Host "  staged config and station list"

# --- dependencies
# No --platform flag: every dependency in requirements-lambda.txt is pure Python, so a
# Windows build produces a working Linux/arm64 package. Add
#   --platform manylinux2014_aarch64 --only-binary :all:
# the day a compiled dependency appears, or build in a Linux container.
& $python -m pip install `
    -r (Join-Path $root "requirements-lambda.txt") `
    --target $build --quiet --no-compile --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- verify the package actually imports, before it is ever uploaded
# Written to a file rather than passed with -c: PowerShell strips the inner quotes out of
# a here-string handed to python -c, which turns every module name into a bare NameError.
$check = @'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
for m in ("requests", "tzdata", "sinks", "backfill", "hostlock", "poll_live"):
    __import__(m)

root = pathlib.Path(sys.argv[1])
for rel in ("config/poll_stations.toml", "stations.json"):
    assert (root / rel).exists(), f"{rel} missing from package"
print("  import check passed")
'@
$checkFile = Join-Path $env:TEMP "rail_delay_pkgcheck.py"
Set-Content -Path $checkFile -Value $check -Encoding UTF8
try {
    # -B so the check does not leave __pycache__ behind in the package it just verified.
    & $python -B $checkFile $build
    if ($LASTEXITCODE -ne 0) { throw "package import check failed" }
} finally {
    Remove-Item $checkFile -ErrorAction SilentlyContinue
}

# --- strip dead weight, AFTER the import check so nothing it creates survives
#   *.dist-info   packaging metadata, unused at runtime
#   __pycache__   rebuilt on the target anyway, and the .pyc are the wrong magic number
#   bin/          pip installs console-script wrappers here; on Windows they are .exe
#                 files, which are doubly useless in a Linux arm64 Lambda
foreach ($pattern in @("*.dist-info", "__pycache__")) {
    Get-ChildItem $build -Directory -Recurse -Filter $pattern |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
$bin = Join-Path $build "bin"
if (Test-Path $bin) { Remove-Item -Recurse -Force $bin }

$size = [math]::Round(((Get-ChildItem $build -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 2)
$count = (Get-ChildItem $build -Recurse -File).Count
Write-Host ""
Write-Host "build/lambda ready: $count files, $size MB unzipped (limit 250 MB)"
Write-Host "next: sam deploy --guided --region eu-west-1 --template-file infra/poller.yaml"
