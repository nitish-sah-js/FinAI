# Wrapper for deploy/cluster.py bundle (see deploy/README.md). Extra args pass through, e.g. --allow-loopback
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
& $Py (Join-Path $PSScriptRoot "cluster.py") bundle @args
exit $LASTEXITCODE
