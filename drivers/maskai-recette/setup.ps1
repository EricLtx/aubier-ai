<#
.SYNOPSIS
  Prépare le driver maskai-recette sur ce nœud : virtualenv, Playwright, Chromium, empreinte.

.DESCRIPTION
  Le manifeste lance ".venv\Scripts\python.exe -I driver.py" depuis le dossier du driver
  (Ruling R15) : `python -I` ignore les paquets installés avec --user, le driver a donc son
  propre virtualenv. Les chemins internes de Playwright dépassent la limite MAX_PATH de
  Windows (260 caractères) quand le dépôt est sous OneDrive : le virtualenv est créé sous un
  chemin court (-VenvHome) et ".venv" est une jonction vers lui. ".venv" est exclu de
  l'empreinte du driver (Ruling R5), qui ne change donc pas.

  Étapes : créer le virtualenv, installer requirements.txt, installer Chromium pour
  Playwright, vérifier l'import par le chemin du manifeste, imprimer l'empreinte
  (hash_dir.py) et la comparer à files_sha256 des deux manifestes.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File drivers\maskai-recette\setup.ps1
#>
param(
  [string]$Python = "python",
  [string]$VenvHome = (Join-Path $env:LOCALAPPDATA "vk\venvs\maskai-recette")
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $here ".venv"

function Invoke-Checked([string]$what, [scriptblock]$cmd) {
  & $cmd
  if ($LASTEXITCODE -ne 0) { throw "$what a échoué (code $LASTEXITCODE)" }
}

# 1. Le virtualenv : réutilisé s'il existe, sinon créé sous -VenvHome et relié par jonction.
$item = Get-Item -LiteralPath $venv -Force -ErrorAction SilentlyContinue
if ($null -eq $item) {
  if (-not (Test-Path (Join-Path $VenvHome "Scripts\python.exe"))) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $VenvHome) | Out-Null
    Invoke-Checked "python -m venv" { & $Python -m venv $VenvHome }
  }
  New-Item -ItemType Junction -Path $venv -Target $VenvHome | Out-Null
  $real = $VenvHome
} elseif ($item.LinkType -eq "Junction") {
  $real = [string]$item.Target
} else {
  $real = $venv
}
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
  throw "$venv n'a pas de Scripts\python.exe : supprimez-le et relancez"
}
Write-Host "virtualenv : $venv -> $real"

# 2. Paquets et navigateur, par le chemin réel (court) du virtualenv.
$realPy = Join-Path $real "Scripts\python.exe"
Invoke-Checked "pip install" {
  & $realPy -I -m pip install --disable-pip-version-check -r (Join-Path $here "requirements.txt")
}
Invoke-Checked "playwright install chromium" { & $realPy -I -m playwright install chromium }

# 3. L'interpréteur tel que le démon le lance voit Playwright.
$py = Join-Path $venv "Scripts\python.exe"
Invoke-Checked "import playwright" { & $py -I -c "import playwright.sync_api" }

# 4. L'empreinte du dossier, comparée aux manifestes.
$hash = (& $py -I (Join-Path $here "..\vkdriver\hash_dir.py") $here).Trim()
if ($LASTEXITCODE -ne 0) { throw "hash_dir.py a échoué" }
Write-Host "files_sha256 : $hash"
foreach ($name in "manifest.json", "manifest-fixture.json") {
  $path = Join-Path $here $name
  if (-not (Test-Path $path)) { continue }
  $declared = (Get-Content -Raw -Encoding UTF8 $path | ConvertFrom-Json).program.files_sha256
  if ($declared -eq $hash) {
    Write-Host "$name : à jour"
  } else {
    Write-Warning "$name déclare $declared : remplacez files_sha256 par $hash avant de monter le driver"
  }
}
