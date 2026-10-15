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

  Le Python de base compte (ruling R27). Le Python du Microsoft Store est une application
  empaquetée : Windows démarre ses enfants non empaquetés (le node de Playwright, Chrome)
  hors du Job Object du noyau, donc hors du plafond mémoire, de l'arrêt avec le démon et
  de hosts_seen. Ce script refuse une base sous WindowsApps, pour le virtualenv à créer
  comme pour un virtualenv existant, et il préfère le CPython autonome géré par uv quand
  -Python n'est pas donné.

  Étapes : choisir le Python de base, créer le virtualenv, installer requirements.txt,
  installer Chromium pour Playwright, vérifier l'import par le chemin du manifeste,
  imprimer l'empreinte (hash_dir.py) et la comparer à files_sha256 des deux manifestes.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File drivers\maskai-recette\setup.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File drivers\maskai-recette\setup.ps1 -Python C:\Python313\python.exe
#>
param(
  [string]$Python = "",
  [string]$VenvHome = (Join-Path $env:LOCALAPPDATA "vk\venvs\maskai-recette")
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $here ".venv"

function Invoke-Checked([string]$what, [scriptblock]$cmd) {
  & $cmd
  if ($LASTEXITCODE -ne 0) { throw "$what a échoué (code $LASTEXITCODE)" }
}

$storeRemedy = "installez un CPython non empaqueté (uv python install 3.13, ou python.org) " +
  "et relancez avec -Python <chemin de son python.exe> ; sans -Python, le script prend " +
  "celui de uv (uv python find --managed-python 3.13)."

function Test-PackagedPython([string]$path) {
  # Le Python du Store vit sous ...\WindowsApps\... (alias par utilisateur ou dossier système).
  return $path -match '\\WindowsApps\\'
}

# 0. Le Python de base : -Python, sinon le CPython géré par uv, sinon python sur le PATH.
if ($Python -eq "") {
  $uv = Get-Command uv -ErrorAction SilentlyContinue
  if ($null -ne $uv) {
    $found = (& $uv.Source python find --managed-python 3.13 2>$null)
    if ($LASTEXITCODE -eq 0 -and $found) { $Python = ([string]$found).Trim() }
  }
  if ($Python -eq "") { $Python = "python" }
}
$pyCmd = Get-Command $Python -ErrorAction SilentlyContinue
if ($null -eq $pyCmd) { throw "Python de base introuvable : $Python" }
$basePython = $pyCmd.Source
if (Test-PackagedPython $basePython) {
  throw "Python de base refusé : $basePython est le Python du Microsoft Store (application " +
    "empaquetée), ses enfants échapperaient au Job Object du noyau (ruling R27) ; $storeRemedy"
}
Write-Host "python de base : $basePython"

# 1. Le virtualenv : réutilisé s'il existe, sinon créé sous -VenvHome et relié par jonction.
$item = Get-Item -LiteralPath $venv -Force -ErrorAction SilentlyContinue
if ($null -eq $item) {
  if (-not (Test-Path (Join-Path $VenvHome "Scripts\python.exe"))) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $VenvHome) | Out-Null
    Invoke-Checked "python -m venv" { & $basePython -m venv $VenvHome }
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

# 1b. Un virtualenv existant bâti sur le Python du Store est refusé lui aussi : pyvenv.cfg
#     dit sur quel interpréteur il a été construit, et c'est lui que le démon lancera.
$cfg = Join-Path $real "pyvenv.cfg"
$baseOfVenv = ""
if (Test-Path $cfg) {
  $line = Get-Content $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
  if ($line) { $baseOfVenv = ($line -split '=', 2)[1].Trim() }
}
if (Test-PackagedPython $baseOfVenv) {
  throw "virtualenv refusé : $real a été construit sur le Python du Microsoft Store ($baseOfVenv) ; " +
    "renommez ou supprimez ce dossier et la jonction $venv, puis relancez ; $storeRemedy"
}

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
