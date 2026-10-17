<#
.SYNOPSIS
  Inscrit, ou retire avec -Remove, la tâche planifiée « Aubier vkd » : le démon vkd lancé à
  l'ouverture de session, dans la session interactive, relancé chaque minute en cas d'échec,
  trois fois au plus.

.DESCRIPTION
  L'option du mode quotidien (spec P7, condition (b)). Pour le pilote, la façon documentée
  reste le terminal dédié du runbook (docs/technical/runbooks/recette-maskai.md, §3.2). La tâche lance
  la même ligne, à l'identique :
    vkd --auto-enroll-node --harness-timeout-secs 2700

  - Déclencheur : l'ouverture de session du compte qui exécute ce script.
  - Jeton interactif (LogonType Interactive, RunLevel Limited) : la tâche ne tourne que session
    ouverte, sur le bureau, sans élévation. Chrome doit s'ouvrir sur le bureau, ce qu'un service
    en session 0 ne peut pas faire (P7).
  - Relance : si la tâche échoue, nouvel essai chaque minute, trois fois au plus. Windows
    documente cette relance pour une tâche qui ne démarre pas ; qu'il relance aussi un vkd
    qui s'arrête en erreur reste à vérifier sur le nœud.
  - Pas de limite de durée (le défaut de Windows arrêterait le démon au bout de 72 heures), et
    la tâche démarre et continue sur batterie.
  - Idempotent : une tâche du même nom est arrêtée (son vkd avec elle), puis désinscrite,
    avant l'inscription : il ne reste jamais un vkd de l'ancienne tâche à côté du nouveau.

  À lancer par le fondateur, depuis son propre compte, dans un PowerShell élevé (« Exécuter en
  tant qu'administrateur »). vkd.exe et vk-mcp.exe doivent être côte à côte (runbook §2).

.PARAMETER Vkd
  Le chemin de vkd.exe. Par défaut, la sortie du build du runbook §2.

.PARAMETER Remove
  Arrête la tâche si elle tourne, la désinscrit, et rien d'autre.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\vkd-logon-task.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\vkd-logon-task.ps1 -Remove
#>
param(
  [string]$Vkd = (Join-Path $env:USERPROFILE ".cargo-target\verticalai\release\vkd.exe"),
  [switch]$Remove
)
$ErrorActionPreference = "Stop"
$TaskName = "Aubier vkd"
$Arguments = "--auto-enroll-node --harness-timeout-secs 2700"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Remove) {
  if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Output "tâche « $TaskName » désinscrite"
  } else {
    Write-Output "tâche « $TaskName » absente : rien à retirer"
  }
  return
}

# 1. Les binaires, avant de toucher à la tâche en place : vkd refuse de lancer un harnais sans
#    vk-mcp à côté de lui (runbook §2).
if (-not (Test-Path -LiteralPath $Vkd -PathType Leaf)) {
  throw "vkd introuvable : $Vkd. Construisez-le (runbook §2) ou donnez son chemin avec -Vkd."
}
$Vkd = (Resolve-Path -LiteralPath $Vkd -ErrorAction Stop).Path
$mcp = Join-Path (Split-Path -Parent $Vkd) "vk-mcp.exe"
if (-not (Test-Path -LiteralPath $mcp -PathType Leaf)) {
  throw "vk-mcp.exe absent à côté de vkd ($mcp) : le démon refuserait de lancer un harnais (runbook §2)."
}

# 2. La tâche : le compte qui exécute ce script, à son ouverture de session, jeton interactif.
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $Vkd -Argument $Arguments -WorkingDirectory $repo -ErrorAction Stop
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user -ErrorAction Stop
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited -ErrorAction Stop
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew -ErrorAction Stop

# 3. Idempotent : l'ancienne tâche s'arrête (son vkd avec elle) et s'en va, la nouvelle prend sa
#    place.
if ($existing) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
  -Settings $settings -ErrorAction Stop `
  -Description "Aubier : le démon vkd dans la session interactive (spec P7, condition (b)). scripts\vkd-logon-task.ps1 -Remove la retire." |
  Out-Null
Write-Output "tâche « $TaskName » inscrite pour $user"
Write-Output "  à l'ouverture de session : `"$Vkd`" $Arguments"
Write-Output "  dossier de travail : $repo"
Write-Output "  relance si elle échoue : chaque minute, 3 fois au plus ; pas de limite de durée"
