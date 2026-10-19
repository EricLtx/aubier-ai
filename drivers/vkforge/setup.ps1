<#
.SYNOPSIS
  Prépare vkforge sur ce nœud : le virtualenv avec lequel le démon apprend et qualifie les drivers.

.DESCRIPTION
  vkd lance chaque étape de vkforge ainsi : "<python> -I drivers\vkforge\__main__.py <étape> ...",
  depuis le dossier drivers\ (ruling L-P2). <python> est VK_FORGE_PYTHON s'il est défini, sinon
  l'interpréteur du virtualenv que ce script prépare, par son chemin (ruling L-P12) :
  %LOCALAPPDATA%\vk\venvs\vkforge\Scripts\python.exe. Chaque driver généré par vkforge reçoit sa
  propre jonction ".venv" vers ce même virtualenv (le générateur la crée) ; ce script n'en crée
  aucune sous drivers\vkforge.

  Le Python de base compte (ruling R27). Le Python du Microsoft Store est une application
  empaquetée : Windows démarre ses enfants non empaquetés hors du Job Object du noyau, donc hors
  du plafond mémoire et de l'arrêt avec le démon. Ce script refuse une base sous WindowsApps,
  pour le virtualenv à créer comme pour un virtualenv existant, et il préfère le CPython
  autonome géré par uv quand -Python n'est pas donné.

  Étapes : choisir le Python de base, réutiliser ou créer le virtualenv, installer
  requirements.txt (lxml, comtypes), vérifier l'import par l'interpréteur que le démon lancera,
  imprimer le chemin à donner à VK_FORGE_PYTHON.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File drivers\vkforge\setup.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File drivers\vkforge\setup.ps1 -Python C:\Python313\python.exe
#>
param(
  [string]$Python = "",
  [string]$VenvHome = (Join-Path $env:LOCALAPPDATA "vk\venvs\vkforge")
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

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

$venvPython = Join-Path $VenvHome "Scripts\python.exe"

# 1. Le virtualenv : réutilisé s'il existe, sinon créé sous -VenvHome à partir du Python de base
#    (-Python, sinon le CPython géré par uv, sinon python sur le PATH).
if (Test-Path $venvPython) {
  Write-Host "virtualenv existant : $VenvHome"
} else {
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
  New-Item -ItemType Directory -Force (Split-Path -Parent $VenvHome) | Out-Null
  Invoke-Checked "python -m venv" { & $basePython -m venv $VenvHome }
  Write-Host "virtualenv créé : $VenvHome"
}

# 1b. Un virtualenv bâti sur le Python du Store est refusé, qu'il soit neuf ou existant :
#     pyvenv.cfg dit sur quel interpréteur il a été construit, et c'est lui que le démon lancera.
$cfg = Join-Path $VenvHome "pyvenv.cfg"
$baseOfVenv = ""
if (Test-Path $cfg) {
  $line = Get-Content $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
  if ($line) { $baseOfVenv = ($line -split '=', 2)[1].Trim() }
}
if (Test-PackagedPython $baseOfVenv) {
  throw "virtualenv refusé : $VenvHome a été construit sur le Python du Microsoft Store " +
    "($baseOfVenv) ; renommez ou supprimez ce dossier, puis relancez ; $storeRemedy"
}

# 2. Les paquets de vkforge.
Invoke-Checked "pip install" {
  & $venvPython -I -m pip install --disable-pip-version-check -r (Join-Path $here "requirements.txt")
}

# 3. L'interpréteur tel que le démon le lance voit lxml et comtypes.
Invoke-Checked "import lxml, comtypes" { & $venvPython -I -c "import lxml, comtypes" }

Write-Host "VK_FORGE_PYTHON par défaut : $venvPython"
