# build_scorer.ps1 — stage the nightly scorer into build/scorer.
#
#   powershell scripts\build_scorer.ps1
#   sam deploy --region eu-west-1 --template-file infra/scorer.yaml
#
# Its own package rather than a third handler on the API's, because the scorer must NOT
# carry the model. D39 gives it the mirror image of the API's permissions — read
# predictions, write scores, no write to the prediction prefix — and the same logic applies
# to the code: with no model in the package, "never regenerate historical predictions"
# stops being a rule someone has to remember and becomes something the deployment cannot
# do. That also keeps it at a few MB instead of 165.
#
# Pure Python throughout, same as the poller, so a Windows machine builds a working
# Linux/arm64 package with no --platform flags.

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$build  = Join-Path $root "build\scorer"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path $build | Out-Null

# score -> poll_live -> {sinks, hostlock, backfill}; score -> feedtime
$modules = @("score.py", "poll_live.py", "sinks.py", "backfill.py", "hostlock.py",
             "feedtime.py")
foreach ($m in $modules) {
    $src = Join-Path $root "src\$m"
    if (-not (Test-Path $src)) { throw "missing module: $src" }
    Copy-Item $src $build
}
Write-Host "  staged $($modules.Count) modules"

& $python -m pip install `
    -r (Join-Path $root "requirements-lambda.txt") `
    --target $build --quiet --no-compile --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- verify it imports, and that no model came along by accident
$check = @'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
for m in ("requests", "tzdata", "backfill", "poll_live", "feedtime", "score"):
    __import__(m)

# The absence of the model is a property worth asserting, not just intending: a scorer
# that could load a booster could regenerate a historical prediction, which CLAUDE.md
# classifies as a bug rather than an optimisation.
root = pathlib.Path(sys.argv[1])
for forbidden in ("model", "lightgbm", "numpy"):
    assert not (root / forbidden).exists(), f"{forbidden} must not be in the scorer package"

import score
score._self_check()
print("  import check and self-check passed")
'@
$checkFile = Join-Path $env:TEMP "rail_delay_scorercheck.py"
Set-Content -Path $checkFile -Value $check -Encoding UTF8
try {
    & $python -B $checkFile $build
    if ($LASTEXITCODE -ne 0) { throw "package check failed" }
} finally {
    Remove-Item $checkFile -ErrorAction SilentlyContinue
}

foreach ($pattern in @("*.dist-info", "__pycache__")) {
    Get-ChildItem $build -Directory -Recurse -Filter $pattern |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
$bin = Join-Path $build "bin"
if (Test-Path $bin) { Remove-Item -Recurse -Force $bin }

$size = [math]::Round(((Get-ChildItem $build -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 2)
$count = (Get-ChildItem $build -Recurse -File).Count
Write-Host ""
Write-Host "build/scorer ready: $count files, $size MB unzipped (limit 250 MB)"
Write-Host "next: sam deploy --region eu-west-1 --template-file infra/scorer.yaml"
