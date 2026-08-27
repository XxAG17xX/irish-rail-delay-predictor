# build_api.ps1 — stage the prediction API package into build/api.
#
#   powershell scripts\build_api.ps1 -Version 20260813T221035Z-0c444e3
#
# The version is REQUIRED and is not defaulted to LATEST. The serving version is pinned as
# a CloudFormation parameter, so the artifact baked into the package must be the same one
# the stack names. Defaulting to LATEST here is precisely how the two would drift: a
# --save between builds would silently ship a model the stack does not think is serving.
# api.py refuses to start on a mismatch, but the build should not be able to create one.
#
# Pass the same string to sam deploy as ServingModelVersion.

param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Architecture = "aarch64"
)

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$build  = Join-Path $root "build\api"
$python = Join-Path $root ".venv\Scripts\python.exe"
$source = Join-Path $root "data\models\$Version"

if (-not (Test-Path $source)) {
    throw "no artifact at $source. Available: $((Get-ChildItem (Join-Path $root 'data\models') -Directory).Name -join ', ')"
}

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path $build | Out-Null

# --- modules api.py transitively imports
$modules = @(
    "api.py", "generate.py", "features.py", "feedtime.py", "prediction_log.py",
    "poll_live.py", "backfill.py", "hostlock.py", "sinks.py"
)
foreach ($m in $modules) {
    $src = Join-Path $root "src\$m"
    if (-not (Test-Path $src)) { throw "missing module: $src" }
    Copy-Item $src $build
}
Write-Host "  staged $($modules.Count) modules"

# --- station config, staged BESIDE the modules
# api.py stamps station_group onto every logged row so the accuracy page can report the
# documented weak-coverage lines separately instead of blended into the aggregate, and
# state() refuses to start without it. Same path trap lambda_poll.py documents: under
# Lambda the code lives at /var/task, so a repo-root-relative lookup resolves to /var.
New-Item -ItemType Directory -Force -Path (Join-Path $build "config") | Out-Null
Copy-Item (Join-Path $root "config\poll_stations.toml") (Join-Path $build "config")
$stations = Join-Path $root "data\live\stations.json"
if (-not (Test-Path $stations)) {
    throw "no station list at $stations. Run poll_live.py once locally to fetch it."
}
Copy-Item $stations $build
Write-Host "  staged config and station list"

# --- the pinned artifact, baked rather than fetched. See D33 amendment and api.py.
Copy-Item -Recurse $source (Join-Path $build "model")
Write-Host "  baked model $Version"

# --- dependencies
# Four flags, each load-bearing, all four found the hard way:
#
#   --platform          numpy, scipy and pydantic-core ship compiled extensions, so pip
#                       must fetch Linux wheels rather than reuse this machine's Windows
#                       ones. Given TWICE because the dependencies disagree about tags:
#                       numpy past 2.2.6 publishes only manylinux_2_28, while lightgbm
#                       4.7.0 publishes only manylinux2014. Either tag alone fails to
#                       resolve. Lambda's Amazon Linux 2023 satisfies both.
#   --python-version    this machine runs 3.14, the Lambda runs 3.13. Without it pip
#                       looks for cp314 wheels and reports the package as nonexistent
#                       rather than as a version mismatch.
#   --only-binary       fail loudly instead of silently building from source against the
#                       wrong platform.
& $python -m pip install `
    -r (Join-Path $root "requirements-api.txt") `
    --target $build `
    --platform "manylinux2014_$Architecture" `
    --platform "manylinux_2_28_$Architecture" `
    --python-version 3.13 `
    --only-binary=:all: `
    --quiet --no-compile --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed. Check whether a dependency changed its manylinux tag; if a wheel genuinely does not exist for $Architecture, rebuild with -Architecture x86_64 and set the function's Architectures to x86_64 to match."
}

# --- OpenMP runtime, which lightgbm needs and Lambda does not ship
# lib_lightgbm.so links against libgomp.so.1. The Lambda Python runtime image has no
# OpenMP, and lightgbm's own wheel does not bundle it, so the import dies at
# ctypes.LoadLibrary with "libgomp.so.1: cannot open shared object file".
#
# scikit-learn's manylinux wheel does bundle a matching aarch64 build, so it is borrowed
# from there rather than adding scikit-learn itself (~40MB) as a dependency. Lambda's
# default LD_LIBRARY_PATH already contains /var/task/lib, so dropping it there is enough
# and no environment variable is needed.
$gompScript = @'
import pathlib
import shutil
import sys
import zipfile

wheel, out = sys.argv[1], pathlib.Path(sys.argv[2])
z = zipfile.ZipFile(wheel)
names = [n for n in z.namelist() if "gomp" in n.lower()]
if not names:
    raise SystemExit(f"no libgomp inside {wheel}; find another wheel that bundles it")
out.mkdir(parents=True, exist_ok=True)
with z.open(names[0]) as src, open(out / "libgomp.so.1", "wb") as dst:
    shutil.copyfileobj(src, dst)
print(f"  vendored libgomp.so.1 from {names[0].split('/')[0]}")
'@
$tmp = Join-Path $env:TEMP "rail_delay_gomp"
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
& $python -m pip download scikit-learn --dest $tmp --no-deps `
    --platform "manylinux_2_28_$Architecture" --only-binary=:all: --python-version 3.13 `
    --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "could not download a wheel to source libgomp from" }
$gompFile = Join-Path $env:TEMP "rail_delay_gomp.py"
Set-Content -Path $gompFile -Value $gompScript -Encoding UTF8
try {
    & $python -B $gompFile (Get-ChildItem $tmp -Filter *.whl | Select-Object -First 1).FullName (Join-Path $build "lib")
    if ($LASTEXITCODE -ne 0) { throw "libgomp extraction failed" }
} finally {
    Remove-Item $gompFile -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

# --- verify the baked artifact is the one that was asked for, before anything ships
$check = @'
import json
import pathlib
import sys

build, want = pathlib.Path(sys.argv[1]), sys.argv[2]
manifest = json.loads((build / "model" / "manifest.json").read_text(encoding="utf-8"))
assert manifest["version"] == want, (
    f"baked {manifest['version']!r} but was asked for {want!r}")
for rel in ("api.py", "generate.py", "config/poll_stations.toml", "stations.json",
            "lib/libgomp.so.1", "model/q10.txt", "model/q50.txt", "model/q90.txt"):
    assert (build / rel).exists(), f"{rel} missing from package"
print(f"  baked artifact verified: {manifest['version']}, {len(manifest['features'])} features")
'@
$checkFile = Join-Path $env:TEMP "rail_delay_apicheck.py"
Set-Content -Path $checkFile -Value $check -Encoding UTF8
try {
    & $python -B $checkFile $build $Version
    if ($LASTEXITCODE -ne 0) { throw "package verification failed" }
} finally {
    Remove-Item $checkFile -ErrorAction SilentlyContinue
}

# --- strip dead weight. Windows .exe console scripts in bin/ are doubly useless on Linux.
foreach ($pattern in @("*.dist-info", "__pycache__", "tests")) {
    Get-ChildItem $build -Directory -Recurse -Filter $pattern |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
$bin = Join-Path $build "bin"
if (Test-Path $bin) { Remove-Item -Recurse -Force $bin }

$size = [math]::Round(((Get-ChildItem $build -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "build/api ready: $size MB unzipped (limit 250 MB)"
Write-Host "next: sam deploy --guided --region eu-west-1 --template-file infra/api.yaml \"
Write-Host "        --parameter-overrides ServingModelVersion=$Version"
