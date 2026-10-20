# Runbook — recette MaskAI gouvernée (pilote Aubier, jalon A)

Pour le fondateur, sur son nœud Windows, dans sa session interactive. Version du 2026-10-17 : driver v2 (debrief `docs/technical/debriefs/2026-10-17-maskai-driver.md`).

- Recette exécutée : `recipes/recette-maskai.json`, seize pas.
- Procédure suivie : `docs/functional/use-cases/recettage-chat-ia/PROCEDURE.md`.
- Répétition à blanc : `recipes/recette-fixture.json`, huit pas, sur le banc d'essai local.
- Seconde recette, « MaskAI en profondeur » : `recipes/recette-maskai-2.json`, huit niveaux M0–M7 (`docs/functional/use-cases/recettage-chat-ia/PROCEDURE-2.md`). Elle se déroule comme celle-ci, de §6 à §12, avec ses propres niveaux.

Chaque étape donne la commande exacte, puis la ligne attendue (« Attendu »). Toutes les commandes se tapent dans PowerShell, à la racine du dépôt. Si la sortie ne ressemble pas à la ligne attendue, on s'arrête et on lit le §11 avant d'aller plus loin.

---

## 0. La carte en une minute

Le pilote se lit comme un système embarqué.

| Ici | En firmware |
|---|---|
| `vkd` | Le noyau du RTOS. Il ordonnance les pas et tient le registre de la tâche. Il écrit le ledger : une boîte noire append-only, chaînée par hash. |
| Une arch (`claude-code/…`) | Un coprocesseur que le noyau appelle pour le plan, le jugement et la rédaction. |
| Le driver `maskai-1` | Un pilote de périphérique. Le périphérique est Chrome, le bus est Playwright, et les quinze actions du manifeste sont l'API du pilote. |
| `hosts` du manifeste | La table MPU du pilote. Une navigation hors de la liste est défaite, et l'action rend `host`. |
| Le harnais (Claude Code confiné) | Une tâche utilisateur sans privilège. Son seul appel système utile est `vk_driver_act`. |
| `max_actions`, `timeout_s` d'un pas | Le budget d'appels de la tâche, et son chien de garde. |
| `vkd --harness-timeout-secs` | Le chien de garde global. Le plus court des deux chiens de garde gagne. |
| `vk approve` | Le point de validation, signé par la clé de l'appareil : la preuve de présence humaine. |
| `vk artefact cat` | La lecture de ce qu'on va signer, par un humain, signée elle aussi : on lit la mémoire avant de valider. La lecture est inscrite à la boîte noire comme une publication (`artefact.released`, destination `terminal:<appareil>`). |
| `vk stop` | L'arrêt d'urgence. Il tue l'arbre du harnais et l'action en vol. |
| `driver_id`, `files_sha256` | L'empreinte du firmware du pilote. Un octet changé dans `drivers/maskai-recette/` donne un autre pilote. |
| `inputs_sha256` | L'empreinte des tables de calibration du verdict (§13). |

Une séance complète se déroule ainsi :
1. On démarre `vkd`, on monte deux arches et le driver, puis on se connecte à MaskAI à la main.
2. On soumet la recette, puis on lance un pas de plan et dix exécutions du harnais (N0 à N9).
3. On lance le juge, puis on signe. On lance la rédaction, puis on signe. On publie.
4. On copie le résultat dans le dépôt, après l'avoir expurgé.
5. Après le tir, on lance le debrief (§16) : il lit les journaux et la console, il propose, il n'applique rien.

---

## 1. Prérequis, une fois par nœud et par montage (P8)

Docker n'est pas requis.

**1.1 Un CPython 3.13 non empaqueté.** Le Python du Microsoft Store ne convient pas (voir plus bas). Le plus simple est celui que `uv` gère :
```powershell
uv python install 3.13
uv python find --managed-python 3.13
```
Attendu : un chemin sous `%APPDATA%\uv\python\cpython-3.13-...\python.exe`. Un Python de python.org convient aussi ; on le passera alors à `setup.ps1` avec `-Python <chemin>\python.exe`.

**1.2 Le virtualenv du driver** : Playwright 1.64.0, Chromium, et le contrôle de l'empreinte.
```powershell
powershell -ExecutionPolicy Bypass -File drivers\maskai-recette\setup.ps1
```
Attendu, en première ligne, `python de base : ` suivi du chemin du 1.1, puis en dernières lignes :
```
files_sha256 : sha256:855a4e17e5b3c563fcddc4dc2498cac8c2092f3b42bd31eba23a2ced9a2cfadf
manifest.json : à jour
manifest-fixture.json : à jour
```
Installé par le chantier de paquetage, le driver vit sans dépôt autour de lui : `drivers/maskai-recette/pack.json` nomme ce que le paquet emporte sous `vendor/` (vkdriver, `evaluer.py`, `journal.py`), et le driver cherche là d'abord, dans le dépôt ensuite. Ici, en développement, `vendor/` n'existe pas et rien ne change ; `inputs_sha256` nomme ces fichiers par leur chemin dans le dépôt dans les deux cas.

Pourquoi un virtualenv à part ? Les chemins internes de Playwright dépassent la limite MAX_PATH de 260 caractères sous OneDrive. Le virtualenv vit donc sous `%LOCALAPPDATA%\vk\venvs\maskai-recette`, et `drivers\maskai-recette\.venv` est une jonction vers lui. `.venv` est exclu de l'empreinte.

**Le Python de base compte (ruling R27).** Le virtualenv est construit sur le Python de base, et c'est lui que le démon lancera. Le Python du Microsoft Store est une application empaquetée : Windows démarre ses enfants non empaquetés, donc le nœud Playwright et Chrome, **hors du Job Object**. Chrome n'a alors ni le plafond mémoire, ni l'arrêt avec le démon, et `hosts_seen` ne voit pas ses connexions. L'arrêt par l'arbre des processus (échéance, STOP, démontage) l'atteint toujours. `setup.ps1` refuse donc une base sous `WindowsApps`, et refuse aussi un virtualenv déjà construit dessus : dans ce cas, renommez `%LOCALAPPDATA%\vk\venvs\maskai-recette`, supprimez la jonction `drivers\maskai-recette\.venv` si elle existe, puis relancez le 1.2. Sans `-Python`, le script prend le CPython de `uv` ; à défaut, `python` du PATH.

**1.3 Google Chrome**, parce que le manifeste dit `browser_channel: "chrome"`.
```powershell
Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe"
```
Attendu : `True`

Pour utiliser Edge à la place, mettez `"msedge"` dans `manifest.json`. Le `driver_id` change, `files_sha256` ne change pas.

**1.4 Claude Code**, nécessaire pour les arches `claude-code` et pour le harnais.
```powershell
claude --version
```
Attendu : `2.1.3xx (Claude Code)`, sur un compte connecté à l'abonnement (dans `claude`, `/login` si besoin).

Une arch `anthropic` demanderait en plus `vk secret set anthropic`. Elle n'est pas utilisée ici : le fondateur a choisi les arches `claude-code`.

**1.5 Alimentation stabilisée.** Une mise en veille tue le démon, c'est le coût connu de P7. Branchez le secteur et coupez la veille pour la séance.
```powershell
powercfg /change standby-timeout-ac 0
```
Attendu : aucune sortie. Pour rétablir la veille après la séance : `powercfg /change standby-timeout-ac 30`.

---

## 2. Construire les trois binaires

`vk`, `vkd` et `vk-mcp` doivent être côte à côte : le démon refuse de lancer un harnais sans `vk-mcp` à côté de lui.

```powershell
$env:CARGO_TARGET_DIR = "$env:USERPROFILE\.cargo-target\verticalai"
$env:OPENSSL_SRC_PERL = "$env:USERPROFILE\.cargo-target\tools\strawberry-perl\perl\bin\perl.exe"
cargo build --release -p vk-cli -p vkd -p vk-mcp
$env:PATH = "$env:USERPROFILE\.cargo-target\verticalai\release;$env:PATH"
```
Attendu : `Finished `release` profile [optimized] target(s) in …`

La ligne `$env:PATH` est à refaire, telle quelle, dans chaque terminal ouvert pour la séance. Elle ne dépend d'aucune autre variable.

---

## 3. Démarrer le noyau

Le démon tourne en mode interactif, dans son propre terminal (P7) : Chrome doit s'ouvrir sur votre bureau, et un service en session 0 ne le peut pas.

**3.1 Un seul démon à la fois.** Si `vk status` répond, un `vkd` tourne déjà, par exemple lancé par `vk boot`. Arrêtez-le : Ctrl+C dans son terminal, ou bien :
```powershell
Get-Process vkd -ErrorAction SilentlyContinue | Stop-Process
```
Si la tâche planifiée du §3.3 est inscrite, arrêtez plutôt la tâche. Qu'un `Stop-Process` soit lu comme un échec, et le démon relancé une minute plus tard, reste à vérifier sur le nœud ; `Stop-ScheduledTask` ne laisse pas ce doute.
```powershell
Stop-ScheduledTask -TaskName "Aubier vkd"
```

**3.2 Démarrage**, dans un terminal dédié qu'on laisse ouvert toute la séance :
```powershell
$env:MCP_TOOL_TIMEOUT = "390000"   # le délai d'outil MCP du harnais, voir §7.0
vkd --auto-enroll-node --harness-timeout-secs 2700
```
Attendu, dans les traces :
```
vkd booted ledger_ok=true …
vkd listening endpoint=\\.\pipe\…
```

Pourquoi `vkd` et pas `vk boot` ? `vk boot` ne transmet pas `--harness-timeout-secs`. Le chien de garde global resterait alors à 300 s, et il couperait chaque niveau à cinq minutes. Les pas lourds (N4, N6, N8) demandent `timeout_s = 2700`. Le démon prend le plus petit des deux budgets, donc le sien doit être au moins aussi grand.

Fermer ce terminal, c'est couper l'alimentation : le driver et son Chrome meurent avec le démon. C'est voulu (Job Object, kill-on-close).

**3.3 Mode quotidien, facultatif : la tâche planifiée « Aubier vkd ».** Pour le pilote, la façon documentée reste le terminal du §3.2. Pour un usage de tous les jours (spec P7, condition (b)), `scripts\vkd-logon-task.ps1` inscrit une tâche planifiée qui lance la même ligne, `vkd --auto-enroll-node --harness-timeout-secs 2700`, à l'ouverture de votre session. Elle tourne dans votre session interactive, sans élévation, pour que Chrome s'ouvre sur votre bureau. Si elle ne démarre pas, Windows réessaie chaque minute, trois fois au plus ; qu'il relance aussi un `vkd` qui s'arrête en erreur reste à vérifier sur le nœud. Elle n'a pas de limite de durée et tourne aussi sur batterie.

Une fois, après le build du §2, dans un PowerShell élevé (« Exécuter en tant qu'administrateur ») ouvert depuis votre propre compte, à la racine du dépôt :
```powershell
powershell -ExecutionPolicy Bypass -File scripts\vkd-logon-task.ps1
```
Attendu : `tâche « Aubier vkd » inscrite pour <DOMAINE>\<vous>`, puis la ligne lancée. Le compte affiché doit être le vôtre. Le script est idempotent : relancé, il remplace la tâche.

Pour l'essayer sans attendre la prochaine session :
```powershell
Start-ScheduledTask -TaskName "Aubier vkd"
```
Attendu : la fenêtre console de `vkd` s'ouvre, avec les traces du §3.2. La fermer arrête le démon, comme au §3.2. Que Windows le relance ensuite reste à vérifier sur le nœud : sa relance documentée vise une tâche qui ne démarre pas, pas une action qui se termine en erreur. Pour un arrêt franc, `Stop-ScheduledTask` (§3.1). Avec la tâche, sautez le §3.2.

Pour la retirer :
```powershell
powershell -ExecutionPolicy Bypass -File scripts\vkd-logon-task.ps1 -Remove
```
Attendu : `tâche « Aubier vkd » désinscrite`.

---

## 4. Clé de l'appareil et bail de vivacité

Dans un **second** terminal, refaites la ligne `$env:PATH` du §2, puis :
```powershell
vk status
```
Attendu : `devices      1` (ou plus) et `ledger       N events, chain verified`.

`--auto-enroll-node` inscrit la clé du nœud, celle du trousseau Windows, au premier démarrage. C'est elle qui signe `vk mount`, `vk approve` et `vk stop`.

```powershell
vk liveness renew --business recette-maskai --until +2d
```
Attendu : `liveness of recette-maskai renewed until … ms (unix) by node:node-1; on the ledger as liveness.renewed`

Les pas que vous lancez vous-même sont des actes humains, et l'intercepteur I4 ne les bloque pas. Le renouvellement inscrit votre présence au ledger pour la séance.

---

## 5. Monter les arches

```powershell
vk mount claude-code --timeout 600
```
Attendu, un tableau :
```
ROLE   ARCH                         ID
draft  claude-code/claude-sonnet-5  sha256:…
judge  claude-code/claude-opus-5    sha256:…
```

```powershell
vk ls /arches
```
Attendu : ces deux lignes, chacune en `ready`.

Le `--timeout 600` donne dix minutes par appel au lieu de trois. Le rapport final est long, et la rédaction peut dépasser 180 s.

**Règle R21 : les arches sont nommées, pas identifiées.** La recette dit `"arch_id": "claude-code/claude-sonnet-5"` (plan et rédaction) et `"arch_id": "claude-code/claude-opus-5"` (jugement). C'est le `name` du manifeste de l'arch, pas son id. L'id est un hash de contenu : il change d'une machine à l'autre et d'une version de Claude Code à l'autre, donc une recette versionnée ne peut pas le porter. `vk task submit --steps` remplace chaque nom par l'id de la seule arch montée qui le porte. Un `arch_id` qui commence par `sha256:` passe tel quel.
- **Aucune arch ne porte le nom** : la soumission est refusée avec `no mounted arch is named …` et renvoie à `vk ls /arches`.
- **Plusieurs arches le portent**, par exemple l'ancienne et la nouvelle après une mise à jour de Claude Code : la soumission est refusée et liste les ids. Démontez celle que vous ne voulez pas (`vk umount <ID>`), ou écrivez l'id dans la recette.

---

## 6. Monter le driver et se connecter à MaskAI

**6.0 Depuis un paquet installé** (facultatif : c'est le chemin du firmware signé, là où le §6.1 charge le code depuis le débogueur). Le driver y est livré en un paquet signé qui embarque son propre CPython et ses bibliothèques, installé une fois sous `C:\Program Files\Aubier\drivers\`, et le noyau en vérifie l'empreinte complète avant chaque démarrage. Le §6.1 reste la voie par défaut du jalon A.

Prérequis, à lire avant la première commande :
- **Le driver doit pouvoir démarrer hors du dépôt.** `driver.py` charge `evaluer.py` et `journal.py` depuis `scripts\recette\` à l'import. Installé sous `Program Files`, ce chemin n'existe pas, et le montage rend `would not start`. Il faut que le driver déclare ce qu'il emprunte au dépôt dans un `pack.json` à côté de son manifeste (`{"vendor": ["drivers/vkdriver", "scripts/recette/evaluer.py", "scripts/recette/journal.py"]}`) et qu'il cherche `vendor\` avant la disposition du dépôt. C'est la branche du driver v2 du pilote, pas celle du paquet. Les cas, les oracles et le corpus (`RECETTE_DIR`, sous `docs\`) sont encore lus hors de l'arbre : tant qu'ils ne sont pas vendorés eux aussi, le paquet de `maskai-recette` ne rend pas la recette, et le §6.1 reste la voie.
- Un PowerShell **élevé** pour l'installation seulement. L'empaquetage et le montage se font dans le PowerShell ordinaire.
- Du réseau pour l'empaquetage seulement : le premier pack télécharge CPython (environ 48 Mo, vérifié contre son SHA-256 avant d'être lu) puis les roues de `requirements.txt`. L'installation ne touche pas au réseau.

1. **La clé de l'éditeur, une fois** (un acte humain, signé avec la clé de l'appareil du §4) :
```powershell
vk capital key init
```
Attendu :
```
publisher key  sha256:…
pinned here    yes
-----BEGIN AUBIER PUBLISHER KEY-----
…
-----END AUBIER PUBLISHER KEY-----
```
Cette clé signe des paquets, jamais une ligne du ledger ni un montage. Relancée, la commande montre la même clé. `vk capital key show` la réaffiche sans jamais en faire une.

2. **Empaqueter**, démon lancé (§3), dans le PowerShell ordinaire, à la racine du dépôt :
```powershell
vk driver pack --manifest drivers\maskai-recette\manifest.json --version 1.0.0 --out dist\maskai-recette-1.0.0
```
Attendu, après quelques minutes la première fois :
```
packed     maskai-1@1.0.0
into       <dépôt>\dist\maskai-recette-1.0.0
package    sha256:…
files      sha256:…
driver_id  sha256:…
```
`--out` doit être absent ou vide. La version est `MAJOR.MINOR.PATCH` en chiffres. Le démon écrit `driver.packed` au ledger (un acte humain, comme le montage).

3. **Installer**, dans un PowerShell **élevé**, à la racine du dépôt (aucun démon requis) :
```powershell
vk driver install dist\maskai-recette-1.0.0
```
Si le démon tourne avec `--state-dir X`, ajoutez `--state-dir X` : l'éditeur est épinglé dans le dossier d'état que lit le démon, et c'est le `pins` de la sortie qui dit où. **Si vous élevez avec un autre compte que celui qui fait tourner `vkd`** (l'UAC demande le mot de passe d'un autre administrateur), passez `--state-dir` = la ligne `state dir` de `vk status`, lancé dans le PowerShell ordinaire : sinon l'éditeur est épinglé dans le dossier d'état de l'autre compte, que le démon ne lit pas, et le montage du pas 4 est refusé (`is not pinned on this node: no pin in …`). La commande écrit d'abord sur stderr où elle épingle (`pinning publishers in the state dir …`), puis un avertissement (`warning: … is owned by …`) quand ce dossier appartient à un autre compte que celui du shell. Sur le nœud qui a fait le pack, la clé du pas 1 est déjà épinglée :
```
installed  maskai-1@1.0.0
into       C:\Program Files\Aubier\drivers\maskai-1\1.0.0
package    sha256:…
files      sha256:…
publisher  sha256:… (already pinned)
pins       <dossier d'état>\capital\publishers
acl        read-only: administrators and SYSTEM full, users read and execute
```
Sur un autre nœud, l'éditeur est inconnu : l'installation affiche son empreinte et sa clé publique, puis `pin this publisher on this node? [y/N]`. Comparez l'empreinte avec celle que l'éditeur vous a donnée par un autre canal avant de répondre `y` : toute autre réponse n'installe rien et n'épingle rien. C'est la confiance au premier usage, et c'est l'administrateur qui la donne.

Refus attendus, une ligne chacun, avec leur remède : `run it from an elevated shell` (shell non élevé) ; `already installed` (une version n'est jamais écrasée : empaquetez une version suivante) ; un arbre dont le chemin le plus profond dépasse **240 caractères** (voir plus bas) ; une racine où un groupe large peut écrire.

4. **Monter par nom et version** (un acte humain, signé) :
```powershell
vk mount driver maskai-1@1.0.0
```
Attendu :
```
DRIVER    ID          GOVERNED  SOURCE     VERSION
maskai-1  sha256:…    yes       installed  1.0.0
```
Le noyau a, avant de lancer quoi que ce soit : vérifié la signature contre la clé épinglée, relu le manifeste installé contre celui que le paquet signe, et recalculé l'empreinte de tout l'arbre. Chrome s'ouvre ensuite sur **https://app.maskai.fr/new**. Le profil est sous le dossier d'état (`<dossier d'état>\drivers\maskai-1\profile`, le `state_dir` de `vk status`), pas dans l'arbre installé : il est **réutilisé** d'une version à l'autre, et la session MaskAI faite à la main au §6.1 n'est pas à refaire.

Changer de version : `vk driver umount maskai-1`, puis `vk mount driver maskai-1@1.1.0`. Revenir en arrière : les mêmes deux commandes avec `1.0.0`. Retirer une version : `vk driver uninstall maskai-1@1.0.0`, dans un PowerShell élevé, refusé tant qu'elle est montée. **Démontez-la avant d'arrêter le démon** : sans démon sur le point d'accès du shell, la désinstallation ne peut pas demander ce qui est monté, n'en tient rien pour monté, et le dit sur stderr (`no daemon on <point d'accès>: nothing is treated as mounted`). Un démon arrêté avec une version montée garde ce montage au redémarrage, et son prochain acte échoue sur un arbre qui n'existe plus.

5. **Les règles de l'arbre installé.**
- **Ne lancez jamais le `python.exe` installé à la main sans `-B`.** L'arbre est lu seul, et `__pycache__` fait partie de l'empreinte : un `.pyc` écrit par un interpréteur lancé sans `-B`, dans un shell élevé, est un changement, et le driver est `tampered` au démarrage suivant. Le noyau lance `python\python.exe -E -s -B driver.py`. Pour essayer quelque chose à la main, faites-le sur une copie.
- Si un driver devient `degraded` avec la raison `tampered` (`vk driver ls`, colonne WHY) : l'arbre n'est plus celui qui a été signé. Le remède est `vk driver umount maskai-1`, puis (élevé) `vk driver uninstall maskai-1@1.0.0` et `vk driver install dist\maskai-recette-1.0.0`, puis le montage. Un montage tenté sur un arbre qui ne correspond pas est refusé avec ce remède, sans rien lancer.
- **Limite de 240 caractères.** Windows coupe à 260, et pip et la roue de Playwright (arbre `node` profond) échouent avant. L'installation refuse donc un arbre dont le chemin le plus profond, compté à son emplacement final, dépasse 240 : `C:\Program Files\Aubier\drivers\maskai-1\1.0.0\` en consomme une cinquantaine, avant les 105 du chemin le plus profond de l'interpréteur lui-même. La longueur du chemin le plus profond de Playwright reste à mesurer sur le nœud. Le remède est un nom ou une version plus courts, jamais une racine plus profonde.
- Un manifeste signé ne se modifie pas : ajouter un hôte à `hosts` (§6.3) demande un nouveau pack et donc une nouvelle version.

**6.1 Montage** (un acte humain, signé) :
```powershell
vk mount driver --manifest drivers\maskai-recette\manifest.json
```
Attendu :
```
DRIVER    ID          GOVERNED  SOURCE       VERSION
maskai-1  sha256:…    yes       development  -
```

Chrome s'ouvre sur **https://app.maskai.fr/new**, avec le profil dédié du driver (sous le dossier d'état, jamais votre profil personnel). **Connectez-vous à la main** avec le compte de test. Le noyau ne voit jamais le mot de passe (procédure §4).

**6.2 Vérification**
```powershell
vk driver ls
```
Attendu, sept colonnes (`NAME STATE GOVERNED SOURCE VERSION ACTIONS WHY`) :
```
NAME      STATE  GOVERNED  SOURCE       VERSION  ACTIONS                              WHY
maskai-1  ready  yes       development  -        mesurer_chargement,…,exporter_journal  -
```
Monté depuis un paquet (§6.0), la ligne dit `installed` et `1.0.0` à la place de `development` et `-`. Un driver installé et non monté est listé aussi, à l'état `installed`, avec ses versions.

Les dix-neuf actions, dans l'ordre du manifeste : `mesurer_chargement`, `nouvelle_conversation`, `joindre_document`, `poser_question`, `envoyer_message`, `lire_reponse`, `capturer_flux`, `choisir_modele`, `recherche_web`, `conversation_longue`, `verdict_oracle`, `lister_conversations`, `charge_concurrente`, `capture_ecran`, `exporter_journal`, `renommer_conversation`, `supprimer_conversation`, `ouvrir_avis`, `sonder_interface`. `choisir_modele`, `recherche_web` et `conversation_longue` sont nouvelles en v2 (§6.4), les quatre dernières en v2.3 (§6.5).

`governed: yes` veut dire que le Job Object a confirmé le confinement du processus du driver : le plafond mémoire du manifeste (`program.max_memory_bytes`, 6 Gio), et l'arbre est tué avec le démon. Avec un Python du Store, Chrome est hors du Job (§1.2).

**6.3 Ajouter un hôte à `hosts`, seulement si nécessaire.** La liste ne porte que sur les navigations de la page principale. Les CDN, polices et analytics d'autres hôtes ne sont pas bloqués : ils sont relevés dans `third_party_hosts` (`capturer_flux`, `network.json`). La connexion au montage n'est pas gardée non plus, donc une redirection vers un fournisseur d'identité y fonctionne.

Il faut ajouter un hôte quand une action rend `host` en nommant un hôte légitime. Cas typique : la session a expiré, et `/new` redirige vers l'identité pendant un pas.
1. Ajoutez l'hôte dans `"hosts"` de `drivers\maskai-recette\manifest.json`.
2. Démontez le driver : `vk driver umount maskai-1`. Comme le montage, c'est un acte humain : la commande signe avec la clé de l'appareil (R25). Attendu : `unmounted driver maskai-1`.
3. Remontez-le avec la commande du §6.1. Attendu : un **nouveau** `driver_id`, car le manifeste a changé. `files_sha256` est inchangé : les manifestes sont exclus de l'empreinte, rien n'est à régénérer.

Le profil Chrome est conservé au démontage, et la session MaskAI avec lui.

**6.4 Ce que la v2 du driver ajoute (2026-10-17).** Trois actions :

| Action | Ce qu'elle fait | Preuve |
|---|---|---|
| `choisir_modele {model}` | Ouvre la liste du bouton « Model: … » du compositeur, clique l'option qui correspond (texte ou valeur, sans tenir compte de la casse ni des séparateurs), puis relit le modèle sur le bouton. Refus `precondition` si aucune option ne correspond ou si la relecture diffère. | `model.json` |
| `recherche_web {mode}` | Clique le bouton « Web search: … » jusqu'à ce que son `data-mode` vaille `off`, `auto` ou `on` (quatre clics au plus ; si un menu s'ouvre, son entrée est choisie), puis relit le mode. | `web.json` |
| `conversation_longue {case, turns}` | Pose `turns` questions (2 à 20) dans la conversation en cours : les `messages` du cas, ou `first`, `filler` pour chaque tour du milieu, puis `last`. Chaque tour est lu comme `poser_question` ; la ligne garde le tour le plus lent. L'abandon d'un tour est journalisé KO disponibilité ; un tour coupé par l'échéance de l'action, avant 3 × le budget, est journalisé NON TESTÉ. Cette action peut durer : `max_action_ms` vaut 360 s (huit tours de 30 s d'abandon, plus ≈ 5 s de lecture par tour), d'où le §7.0. | `turns.json`, `answer.png` |

Les deux premières changent le compositeur et ne restaurent rien (`reversible: false`) : la recette lui rend son réglage d'origine avec le champ `previous` du premier appel. Elles servent trois cas nouveaux : N1-4 (recherche web coupée, même question que N1-1, pour une latence comparable au 13/10), N2-3 (six tours, le fait du premier retrouvé au dernier) et N5-3 (`gemini` choisi, la réponse doit être signée par ce modèle).

Ce qui change dans les actions existantes :
- la réponse est le texte du corps `.prose-llm`, jamais la carte entière. L'en-tête (panneau de l'Aiguilleur, bannière du modèle) va dans la preuve `guard.json`. Une réponse est finie quand le flux est fini **et** que son texte n'a plus bougé à l'écran depuis 2 s ;
- avant d'envoyer, le driver ouvre lui-même le popover « Chiffré » du compositeur, lit ce que MaskAI dit envoyer, puis le referme par Échap (preuve `sent.json`) ;
- v2.2 : une fois la réponse finie, le driver prend la capture `answer.png`, ouvre le panneau « Détails » de la dernière carte de réponse, lit le texte anonymisé envoyé au modèle, puis le referme d'un second clic s'il l'a bien ouvert (preuve `sent.json`, `source: "card-details"`). Le texte envoyé est le texte seul, sans le libellé du panneau : c'est lui que portent `sent_sha256` et la comparaison des passages a et b. Le popover « Chiffré » décrit l'état avant l'envoi : il ne sert plus que pour une carte sans bouton « Détails » (`source: "composer-popover"`). Le bouton de révélation des PII de la carte n'est jamais pressé, son état est noté (`reveal_pressed`). À moins de `MARGIN_S` + 3 s de l'échéance de l'hôte, le panneau n'est pas lu (`rejected: "délai de l'hôte"`) ;
- `joindre_document` suit la puce (uploading, indexing, puis ready ou failed) au plus 3 × le budget du cas. `poser_question` relit la puce et refuse `precondition` si elle n'est pas prête : la question ne part pas, et `verdict_oracle` journalise « question non envoyée (précondition) » ;
- à l'abandon, le driver clique sur le bouton d'arrêt : l'enquête « Votre avis » ne suit plus. La ligne de journal dit ce qui était parti (POST, flux, dernier nœud) ;
- chaque ligne de journal porte `ui_profile_sha256` (§13), et les captures masquent la barre latérale et le bloc du compte.

**6.5 Ce que la v2.3 ajoute (2026-10-19, PROCEDURE-3 §6).** Quatre actions, et `lister_conversations` qui prend un cas :

| Action | Ce qu'elle fait | Preuve |
|---|---|---|
| `lister_conversations {case}` | Pour un cas qui porte une question (M8-4) : ouvre la recherche ⌘K (`ControlOrMeta+K`, à défaut l'entrée « Search conversations » de la barre latérale, cliquée seulement identifiée : dans la barre latérale, son texte ou son libellé disant « search » ou « recherch », jamais le bouton de dictée ni un bouton du compositeur ou d'une carte ; sinon refus `precondition` avant tout clic, le cas journalisé NON TESTÉ « refus du pilote : … », les candidats vus dans `search.json` seulement, jamais un lien ni une ligne de conversation), lit la liste d'avant la frappe, y tape la question, et là seulement, lit les résultats quand la frappe les a changés (au plus 3 × le budget ; un résultat mène à une conversation, jamais un élément qui répète la requête), puis la referme par Échap. La palette est celle que la touche ou le clic a ouverte, jamais un champ ni une racine cmdk déjà là. `@list` : « recherche « q » : n résultats : t1 \| t2 … », ou « résultats inchangés par la frappe » (KO). Un élément qui montre le titre puis un extrait compte ; des éléments que le pilote ne relie à aucune conversation donnent « résultats non reconnus » (NON TESTÉ), comme une palette qui ne s'ouvre pas après l'entrée identifiée (troisième tour). Sans question, ou sans `case`, la liste de la barre latérale comme avant. | `search.json`, `screen.png` |
| `renommer_conversation {case}` | **Irréversible.** Renomme la seule conversation que le cas nomme par `same_conversation_as` (M8-5 : celle de M8-1) : la conversation que ce cas a créée, au registre du pilote, retrouvée dans la barre latérale par son adresse exacte, jamais par son rang ni par son titre. Son menu (un déclencheur qui annonce un menu, `aria-haspopup`, cherché niveau par niveau du lien jusqu'à la ligne de cette conversation et jamais au-dessus, ni dans un élément qui porte un titre ou une étiquette de groupe, révélé au survol ; le menu ouvert doit être le sien, `aria-expanded`) doit porter une entrée de renommage et une de suppression en liste blanche, ou, sous un déclencheur dont le libellé nomme les actions de la conversation (« Actions de la conversation », « Conversation actions », « Options de la conversation », « Chat actions », « Chat options », « Plus d'options », « More options » : sixième tour, le menu vu en direct le 20/10), seulement des entrées en liste blanche dont une seule de suppression et au plus une de renommage ; sans entrée de renommage, `renommer_conversation` est NON TESTÉ « refus du pilote : pas d'entrée de renommage dans le menu de la conversation », rien n'est cliqué (« Renommer », « Supprimer », « … la conversation », « Delete chat », « Modifier le titre », « Déplacer vers la corbeille »…, lues sans points de suspension ni indice de raccourci, même en texte simple : « Supprimer ⌘⌫ », « Renommer F2 », par la même règle que le script de la page), seules de leur sorte, et aucune entrée qui vise plusieurs conversations ; le nouveau titre est la question du cas, tapé dans un champ ouvert par le clic (jamais un champ déjà là ; dans une fenêtre, seulement une fenêtre ouverte par le clic, à un seul champ, sans choix, qui n'est pas « Votre avis »), validé par Entrée, puis relu sur cette conversation, tenu 2 s, attendu jusqu'à 3 × le budget : ce délai est `timing_s`. Un refus du pilote avant tout clic irréversible (menu non reconnu, pas de déclencheur dans la ligne) : NON TESTÉ « refus du pilote : … ». | `conversation.json`, `screen.png` |
| `supprimer_conversation {case}` | **Irréversible.** Supprime cette même conversation (M8-6), par l'entrée « Supprimer » du même menu, puis la confirmation de l'application, ouverte par ce clic et jugée sur son texte entier : acceptée seulement si elle nomme exactement une conversation (son nom ou son titre), sans nom de conversation au pluriel hors de ce nom ni nombre devant lui (troisième tour : « … et les conversations liées », « Les 3 conversations de ce fil » sont refusées ; « dans vos conversations », « de votre historique » ne comptent pas, sauf précédés d'un nombre ou d'un quantificateur, jugés sur le texte entier : « … et 3 de vos conversations », « some of your chats », « d'autres / certaines / plusieurs / la plupart de vos conversations » sont refusées, quatrième tour ; « autre(s) » / « other(s) » comme nom ou après un nombre ou un déterminant aussi, « et les deux autres », « and 2 others », « and the others », cinquième tour), et sans rien qui vise explicitement plusieurs conversations (« toutes les conversations », « chaque conversation », tout l'historique, vider, un dossier, un projet, un espace, un groupe, une sélection) ; sinon annulée par Échap ; sinon validée par son seul bouton de suppression, à défaut le seul « Continuer », « Oui » ou « OK » d'une fenêtre d'alerte (« Ne pas supprimer », « Garder » annulent). Le registre note « suppression demandée » avant le clic sur « Supprimer » (retirée, « suppression annulée », quand la confirmation est annulée et la conversation encore listée), « suppression confirmée » avant le clic de confirmation, puis « supprimée » quand la conversation n'est plus listée : absente, tenue 2 s, après une requête d'écriture acceptée (2xx) d'un hôte de l'application, la requête qui nomme la conversation d'abord (jamais une écriture d'un autre hôte), dans 3 × le budget (`timing_s`) ; rejouée, l'action ne clique plus rien. Une autre conversation disparue pendant l'action (relue après 2 s) : KO sécurité, « suppression en cascade » au registre, `cascade_delete` dans le résultat. Une confirmation native du navigateur n'est jamais répondue : refus `dialog`, NON TESTÉ (§8.4). Un refus du pilote avant le clic de confirmation : NON TESTÉ, de même qu'une entrée ou un bouton de confirmation acceptés puis introuvables au moment du clic, le registre laissé tel qu'il était. La confirmation native ouverte tard, pendant une lecture, et fermée par vous reste celle de l'application : NON TESTÉ. | `conversation.json`, `screen.png` |
| `ouvrir_avis {case}` | Ouvre l'enquête « Votre avis » depuis son entrée (`button[aria-label="Send feedback"]`, ou un autre libellé exact de `feedback_entry`, hors carte de réponse et compositeur ; sinon refus `precondition` avant tout clic, le cas NON TESTÉ « refus du pilote : … », les candidats dans `avis.json` seulement) et la ferme par sa croix, jamais par « Envoyer » ; rien n'y est choisi ni écrit (M8-8). Une fenêtre que le pilote ne reconnaît pas après l'entrée : refus `dialog`, NON TESTÉ, laissée ouverte (fermez-la à la main, jamais par « Envoyer »). | `avis.json`, `avis.png` |
| `sonder_interface` | Lecture seule, jouée en M0 : résout chaque sélecteur, relève l'entrée et la palette ⌘K (rien n'y est tapé), l'entrée de « Votre avis » (jamais cliquée), le bouton de dictée (jamais pressé), la liste `accept`, et le menu de la première conversation de la barre latérale (ouvert par un déclencheur `aria-haspopup` seulement, lu, refermé par Échap ; aucune entrée cliquée ; ni son titre ni son adresse gardés, pas même dans les entrées du menu). Les entrées de la recherche et de l'avis sont relevées telles que les actions les prendraient (identifiées, sinon les candidats vus). Applique au menu lu la règle de `renommer_conversation` et `supprimer_conversation`, et le dit : « M8-5/M8-6 : menu accepté », ou « M8-5/M8-6 : menu refusé (…) » (§8.3) ; les entrées, les candidats et le menu ne vont qu'à `probe.json`. Ses lectures sont des appels bornés : un dialogue ouvert les fait échouer, jamais attendre. Depuis le huitième tour, elle découvre aussi l'« Anonymiseur » de MaskAI (clé `anonymiser`) : les entrées de navigation sous l'entrée de la recherche (jamais une conversation), l'entrée « anonymis… » suivie si elle est sur un hôte de la liste (sinon `host_refused`, non suivie), sa page relevée (chemin, titres, commandes, zones de sortie, `accept`, un échantillon expurgé), puis retour à /new (`restored`) ; rien n'est tapé ni pressé d'autre que l'entrée ; le résultat dit « anonymiseur : trouvé (<chemin>) » ou « anonymiseur : non trouvé ». Ne refuse rien. | `probe.json` |

Avant tout clic, `renommer_conversation` et `supprimer_conversation` refusent `precondition` un cas qui ne les nomme pas (champ `action` de `cases.json`), une conversation absente du registre, supprimée ou dont la suppression est demandée ou confirmée (pour le renommage) ou absente de la barre latérale ; depuis la revue du 20/10 (deuxième tour), ces refus du pilote sont journalisés NON TESTÉ « refus du pilote : … », jamais KO. Le registre est `conversations.json`, dans le dossier de travail du driver, à côté du journal : il dit chaque conversation que le pilote a créée, avec le cas qui l'a créée et ses états (créée, renommée, suppression demandée, suppression annulée, suppression confirmée, supprimée, suppression en cascade avec leur nombre). Une conversation n'y entre que si la page arrive, après la question ou le téléversement, sur l'adresse d'une conversation qui n'était pas listée avant : une ligne apparue dans la barre latérale n'y entre jamais (revue du 20/10).

Ce qui change dans les actions existantes :
- `choisir_modele` relit l'état du bouton de dictée, sans jamais le presser : son résultat finit par « ; micro : idle » (ou « absent ») ; la `config` de chaque ligne porte `mic` ;
- `joindre_document` rend la liste `accept` du champ de fichier (et chaque ligne de pièce jointe la porte, `attach.accept`). Une puce en échec ne donne plus que sa classe (`error_class` : « service documentaire injoignable », « format refusé », « fichier trop volumineux », « échec de la pièce jointe ») et `internal_error_shown` (vrai aussi pour la classe « service documentaire injoignable », dans `attach` et sur la ligne) ; son libellé, brut ou expurgé, ne va qu'à la preuve `chip.json` ;
- une question restée sans carte de réponse : la ligne porte `error_class` (statut HTTP du flux, erreur du flux, erreur affichée, flux terminé sans réponse) et `internal_error_shown` (la ligne d'état, une alerte ou un toast a-t-il montré un hôte, un chemin, une pile ou un service injoignable) ; l'extrait expurgé, 80 caractères, et ce que la page et le flux ont dit, brut, vont à la preuve `failure.json`, jamais sur la ligne ;
- une réponse qui affiche une erreur interne (une pile, une erreur de connexion avec un hôte) : la preuve de la ligne la montre expurgée, et la ligne porte `internal_error_shown: true`. Les résultats de `poser_question`, `conversation_longue`, `charge_concurrente` et `verdict_oracle` portent ce drapeau ; chaque niveau qui pose des questions le note une fois avec `vk_log` « ERREUR INTERNE AFFICHÉE CAS » ;
- chaque ligne d'une question porte `sent_source` (nul avec `sent_rejected`), `sent_tokens` et `pii_declared` ; la note d'un passage b compare les entités masquées des deux textes envoyés (« entités masquées identiques » ou « entités masquées différentes : +X −Y », les types seuls, jamais les valeurs), à défaut leurs types (« types masqués … ») ; chaque ligne porte `modals_closed`, et `conversation` quand la question (ou le téléversement) a créé ou rouvert une conversation du registre, la ligne d'un abandon comprise ; celle de `charge_concurrente` porte `conversations` ;
- une observation partagée (`@load`, `@list`, `@model`, `@web`) ne juge qu'un cas : `verdict_oracle` refuse `precondition` un cas dont l'observation a déjà jugé un autre cas, sauf un cas sondé avant qu'elle soit faite ; une observation faite pour un cas qui nomme son action (M8-4 à M8-6) ne juge que lui, et un dialogue survenu pendant cette action est noté sous le cas, qui le lit (KO sécurité) ;
- depuis la revue du 20/10 (deuxième tour) : le bouton d'envoi n'est cliqué que dans le compositeur, hors fenêtre, jamais le micro ni « Send feedback » ; désactivé ou absent, rien n'est cliqué, et l'abandon le dit (« envoi refusé par l'interface ») ; le bouton d'arrêt, le sélecteur de modèle et le bouton de recherche web ne sont cherchés que dans le compositeur, jamais le micro ni un interrupteur (`aria-pressed`), et la recherche web n'est qu'un bouton `data-mode` off, auto ou on, ou libellé « Web search » / « Recherche web » ; `nouvelle_conversation` ne clique que le lien validé ou, dans la barre latérale, un libellé exact « Nouvelle conversation » / « New chat », sinon elle revient à `/new` ;
- les avis d'erreur (alerte, toast, ligne d'état qui dit une erreur) sont lus pour chaque question, répondue ou non, avant le clic d'arrêt d'un abandon, et après chaque action de M8 ; une alerte ou un toast déjà là avant l'envoi n'est jamais compté ; `internal_error_shown` se calcule sur eux seuls, et leur texte brut va à `notices.json` ;
- `lire_reponse` et `capturer_flux` rendent leurs textes expurgés (le brut reste dans `dom.html` et `network.json`), avec `internal_error_shown` ;
- un dialogue relevé pendant une action, même fermé par vous avant que le driver regarde, reste celui de l'action : refus `dialog`, KO sécurité, noté sous le cas que l'action joue (M8-2 et M8-7 compris) ; son message va au harnais expurgé, sans titre de conversation (un titre de 3 à 7 caractères cité en entier, un titre coupé reconnu à un début de 8 caractères ou plus : quatrième tour), le brut dans `dialog.json` ; la fenêtre « après la réponse » d'une question se ferme dès qu'un autre cas est en jeu.

**Lignes de correction (E8).** `scripts/recette/correct.py` rejoue hors ligne le jugement du driver sur l'observation gardée d'un cas, et écrit une ligne `step: correction` qui remplace la ligne en vigueur (`supersedes`), avec sa raison (`reason`) et les empreintes des règles qui jugent maintenant. Un cas inchangé n'a pas de ligne ; un cas jugé sur une observation partagée sans instantané (pilote antérieur à la v2.3) n'est pas rejoué, et le script le dit. `--reread-header` relit d'abord l'en-tête gardé de chaque carte avec les règles du driver d'aujourd'hui (la correction de M3 du 18/10, bannière sans fournisseur). Seuls le verdict et les ENF comptent : une preuve seule changée (la note de rejeu d'un passage b, par exemple) n'est corrigée qu'avec `--evidence`. `--observations FICHIER` rejoue sur un fichier d'observations mis de côté à une reprise (§12), pour les cas journalisés avant elle. Depuis la revue du 20/10 (deuxième tour), le script ne rejoue que l'observation de la ligne en vigueur : son verdict porte l'empreinte de la ligne (`line_sha256`), ou, plus ancienne, elle redonne le délai de la ligne ; une observation de ce pilote sans empreinte (elle porte `notices` ou `untested` : jamais jugée) est refusée (troisième tour) ; sinon « erreur : l'observation gardée du cas n'est pas celle de la ligne en vigueur », code 1, rien n'est écrit. `journal.py stats` compte les lignes en vigueur, corrections comprises. À blanc par défaut ; `--write` pour écrire, le driver démonté :
```powershell
drivers\maskai-recette\.venv\Scripts\python.exe -I scripts\recette\correct.py "$S\drivers\maskai-1\work" --cases M1-J1a,M3-B2 --reason "oracle corrigé le 19/10"
```

---

## 7. Soumettre la recette

**7.0 Pré-vol : le délai d'outil MCP du harnais.** Une action du driver peut durer jusqu'à `max_action_ms`, 360 s (`conversation_longue`, §6.4). Le harnais est Claude Code : il abandonne un appel d'outil MCP au bout de `MCP_TOOL_TIMEOUT` millisecondes, et l'action serait alors perdue côté harnais alors que le driver travaille encore. Il faut `MCP_TOOL_TIMEOUT` d'au moins **390000** (360 s plus une marge) dans l'environnement du **démon**, que le harnais hérite. C'est la première ligne du §3.2. Pour la tâche planifiée du §3.3, posez-la une fois pour votre compte, puis relancez la tâche :
```powershell
[Environment]::SetEnvironmentVariable("MCP_TOOL_TIMEOUT", "390000", "User")
```
Vérification, dans le terminal du démon avant de lancer `vkd` : `$env:MCP_TOOL_TIMEOUT` affiche `390000`. La répétition à blanc (§14) l'éprouve par la chaîne réelle : son niveau N4 joue une conversation de vingt tours en une seule action.

```powershell
$t = (vk task submit --steps recipes\recette-maskai.json --json | ConvertFrom-Json).id
vk task show $t
```
Attendu : `task       task-node-1-N`, puis seize lignes. La 1 est `plan`, les 2 à 11 sont `harness`, puis viennent 12 `judge`, 13 `approve`, 14 `draft`, 15 `approve` et 16 `release`. Le détail de la ligne 16 se termine par `recettage-chat-ia/runs/2026-10`.

`$t` garde l'id de la tâche pour toutes les commandes suivantes de ce terminal.

---

## 8. Le plan, puis les dix niveaux

**8.1 Le plan**
```powershell
vk task step $t
```
Attendu : la ligne 1 passe à `plan  done`.

**8.2 Un niveau.** Répétez ce bloc dix fois, de N0 à N9 :
```powershell
vk driver ls          # maskai-1 doit être "ready" ; sinon §11, ligne « Driver mort ou muet »
vk harness run $t
vk task show $t
```
Attendu après `vk harness run` :
```
status    running
exit      code 0
governed  yes
```
Dans `vk task show`, la ligne du niveau passe à `done`. Après N9, `status` reste `running` : le juge attend.

Pendant un niveau, ne touchez pas à la fenêtre Chrome, sauf pour fermer un dialogue (§8.4). Laissez-la visible.

Chaque niveau suit les mêmes règles, écrites dans son instruction :
- un seul `vk_driver_act` à la fois, toujours sur `maskai-1` ;
- chaque cas commence par `verdict_oracle` (reprise, §12) ;
- une note `vk_log` par cas, visible dans les traces de `vkd` (`harness log … harness=N1-1 : OK`) ;
- jamais de fichier écrit, jamais de `vk_request_approval` ;
- le niveau finit par `capture_ecran` puis `exporter_journal`.

**8.3 Lire le journal entre deux niveaux.** C'est le journal du driver, en clair, sur disque :
```powershell
$S = (vk status --json | ConvertFrom-Json).state_dir
Get-Content "$S\drivers\maskai-1\work\journal.jsonl" -Encoding UTF8 | ForEach-Object { $l = $_ | ConvertFrom-Json; "{0,-8} {1,-8} {2}" -f $l.case, $l.verdict, ($l.nfr -join ',') }
```
Attendu : une ligne par cas joué, par exemple `N1-1     OK       disponibilite,performance`.

**Premier passage : confirmer les SELECTORS.** C'est à faire après N0, N1 et N5. En recette 3, lisez d'abord la preuve `probe.json` de `sonder_interface` (M0) : elle dit, sélecteur par sélecteur, ce que la page trouve, et le balisage réel de la recherche ⌘K et du menu d'une conversation, avant que M8 en ait besoin. Le banc d'essai imite l'interface des 17, 18 et 19/10, mais une partie des `SELECTORS` de `drivers/maskai-recette/driver.py` reste une hypothèse :

| Sélecteur | Rôle | Le 17/10 |
|---|---|---|
| `assistant_message` | **Le plus important.** La carte de réponse, `article:has(.prose-llm)`. | vu (`article[aria-label="MASKAI response"]`) |
| `answer_body` | Le corps de la réponse, `.prose-llm` : l'oracle ne lit que lui. | vu (`.pl-6 > .prose-llm`) |
| `model_banner`, `guard_pii`, `guard_clean` | L'en-tête de la carte lu en texte : bannière du modèle et panneau de garde (`guard.json`). | texte vu ; balisage à confirmer |
| `new_conversation`, `new_conversation_labels` | Le bouton de nouvelle conversation ; à défaut, dans la barre latérale, un libellé exact « Nouvelle conversation » / « New chat » ; sinon le driver revient à `/new` (jamais « Effacer et démarrer une nouvelle conversation »). | vu (`a[href="/new"][data-slot="button"]`) |
| `conversation_items` | Les entrées de l'historique des conversations. | vu (`a[href*="/conversation/"]`) |
| `attach_remove` | Le bouton « Retirer <fichier> » ; la puce est son ancêtre qui porte du texte. | vu |
| `attach_pending`, `attach_uploading`, `attach_failed`, `attach_busy`, `attach_ready` | Les états de la puce. | « Indexing in progress » et « Failed » vus les 12–13/10 ; l'état prêt et l'indicateur d'activité à confirmer |
| `card_details_trigger` | **Le texte envoyé (v2.2).** Le bouton « Détails » de la dernière carte de réponse, cliqué une fois la réponse finie, puis recliqué pour refermer le panneau s'il s'est ouvert. Par structure d'abord, `button[data-slot="collapsible-trigger"][aria-label*="anonymis" i]`, puis le libellé « Détails » en secours ; cherché dans cette carte seulement, jamais le bouton de révélation des PII ni un bouton du corps de la réponse. | relevé le 18/10 dans le DOM d'une carte stockée par la recette 2, après le panneau de garde : `data-slot="collapsible-trigger"`, `aria-label="Voir le texte anonymisé envoyé au LLM"`, `aria-expanded`, `data-state` ; à confirmer au premier passage |
| `card_details_content` | Le panneau ouvert, `[data-slot="collapsible-content"]` dans la même carte (ou l'élément que nomme `aria-controls`) : son bloc de texte (`sent_text`, `block`) est le texte envoyé, à défaut son texte privé du libellé (`sent.json`, `source: "card-details"`) ; son texte entier (`content_text`) et son HTML (`content_html`) sont gardés. | attendu le 18/10 en collapsible Radix (`data-state="open"`) ; balisage intérieur à relever au premier passage, dans `content_html` |
| `details_label` | Le libellé en tête du panneau, retiré du texte envoyé (gardé dans `label`) ; à défaut, une première ligne qui finit par « : » et que la question ne contient pas. | « Texte anonymisé envoyé au LLM : » sur le banc ; formulation réelle à relever au premier passage (`label`, `content_text`) |
| `pii_reveal` | Le bouton qui révèle les PII dans la réponse, `button[data-slot="pii-reveal-toggle"]`, juste après « Détails » : jamais pressé, son état noté (`reveal_pressed`). | relevé le 18/10 (`aria-pressed="false"`) ; à confirmer |
| `sent_trigger`, `sent_popover`, `sent_text` | Le repli (v2.2) d'une carte sans bouton « Détails » : la pastille « Chiffré » du compositeur, son popover et son bloc de texte (`sent.json`, `source: "composer-popover"`). | pastille vue (le 18/10 : `aria-label="Data encrypted — no PII detected in this conversation"`) ; le 18/10, le popover décrit l'état avant l'envoi (« Aucune PII détectée — rien à anonymiser pour l'instant ») |
| `stop_button` | Le bouton d'arrêt de la génération, cliqué à l'abandon, dans le compositeur seul, jamais le micro ni un interrupteur. | un bouton vu sur la capture N1-3 ; aria-label à confirmer |
| `send_button` | Le bouton d'envoi, utilisé seulement si Entrée n'a rien envoyé, dans le compositeur seul ; désactivé, rien n'est cliqué (« envoi refusé par l'interface »). | vu le 18/10 (`aria-label="Send message"`, `data-action="send"`) |
| `model_button`, `listbox`, `option` | Le bouton du compositeur qui ouvre la liste des modèles (`choisir_modele`) : par structure d'abord, `button[aria-haspopup="listbox"]` (le compositeur n'est pas un `<form>` le 18/10), puis les libellés « Model: … » / « Modèle … » en secours. | liste vue le 18/10 : quatorze options `role="option"` dans une liste cmdk, `data-value` = identifiant fournisseur/modèle (`google/eu.claude-haiku-5-5`), texte = libellé affiché (« claude haiku 5-5 ») ; tous ouverts |
| `web_button`, `web_words` | Le bouton de recherche web (`recherche_web`) : par structure d'abord, `button[data-mode]` (`auto`, `off`, `on`), puis les libellés « Web search: … » / « Recherche web … » en secours ; dans le compositeur seul, et seulement un `data-mode` off, auto ou on ou l'un de ces libellés (jamais un interrupteur de thème). | cycle de clics vu le 18/10 : auto → on → off → auto, sans menu ; `data-mode` fait foi |
| `mic_button` | Le bouton de dictée (micro), `button[data-status][aria-pressed]` : jamais activé ; un état autre que `idle` au début d'une action est un refus `precondition` « dictée en cours » (§11). | vu le 18/10 (`aria-label="Dictate a message in real time (microphone)"`, `data-status="idle"`) |
| `auth_error` | La page d'erreur d'authentification, `/api/auth/error` sur l'URL, et la page d'entrée qui renvoie après chargement vers l'hôte d'authentification hors liste (profil du pilote non connecté) : la garde recharge la page d'entrée une fois, puis refuse `precondition` « session perdue » ou « non connecté » (§11) ; `mesurer_chargement` ne mesure jamais une page de connexion. | vues le 18/10 : l'erreur pendant une coupure réseau, revenue d'elle-même ; la redirection vers `authentification.maskai.fr` au premier M0 de la recette 2 (M0-1 et M0-2 avaient mesuré la page de connexion) |
| `maintenance` | La page de maintenance (« MAINTENANCE EN COURS / ON REVIENT TRÈS VITE ! »), lue sur le texte quand aucune zone de saisie n'est visible (§11). | vue au premier essai réel de la recette 2 |
| `mask` | Les zones masquées des captures : la barre latérale et le bloc du compte. | `nav[aria-label="Main navigation"]` et `aside` vus ; `data-slot` à confirmer |
| `sent_label` | Le panneau « SENT TO THE LLM » du 13/10, en secours du popover. | disparu |
| `sidebar`, `conversation_path` | La barre latérale (`aside`, `nav[aria-label="Main navigation"]`) et le chemin d'une conversation (`/conversation/<id>`) : le registre (v2.3) n'y cherche une conversation que par son adresse exacte. | vus le 17/10 |
| `search_entry`, `search_words` | L'entrée de la recherche ⌘K en tête de la barre latérale, `button:has-text("Search conversations")` (puis `[role="button"]`, un lien, « Rechercher », `aria-keyshortcuts` au seul jeton `Control+K` ou `Meta+K`) : le secours quand `ControlOrMeta+K` n'ouvre rien, cliqué seulement identifié (dans la barre latérale, son texte ou son libellé suivant `search_words`, jamais le micro, le compositeur, une carte, ni un lien ou une ligne de conversation). | texte et indice ⌘K vus le 18/10 ; balisage à confirmer |
| `search_input`, `search_result`, `search_empty` | La palette de recherche (attendue en cmdk : `[cmdk-input]`, `[cmdk-item]`, `[cmdk-empty]`), ses résultats cherchés dans la palette seule, et comptés seulement s'ils mènent à une conversation ; la palette est celle que la touche ou le clic a ouverte (jamais un champ ni une racine cmdk déjà là). Modale `own` « recherche » de `KNOWN_MODALS`. | à confirmer (`probe.json`, `search.json`) |
| `conversation_menu_trigger`, `group_heading`, `conversation_row`, `menu`, `menu_item`, `menu_rename`, `menu_delete` | Le menu d'une conversation : un déclencheur qui annonce un menu (`aria-haspopup`), cherché niveau par niveau du lien jusqu'à la ligne de cette conversation (`conversation_row`), jamais au-dessus, ni dans un élément qui porte un titre ou une étiquette de groupe (`group_heading`, shadcn compris), révélé au survol ; le menu ouvert doit être le sien (`aria-expanded`) ; ses entrées `[role="menuitem"]`, en liste blanche sur leur texte entier sans points de suspension ni indice de raccourci (« renommer », « supprimer », « modifier le titre », « déplacer vers la corbeille », suivis au plus de « la conversation », « chat »…), seules de leur sorte. | jamais lu ; à confirmer (`probe.json`, puis `conversation.json`) |
| `rename_field`, `rename_submit` | Le champ du nouveau titre (le focus, sinon un champ texte de la barre latérale ou d'une fenêtre), apparu après le clic sur « Renommer » (jamais un champ déjà là, un filtre par exemple), et le bouton qui valide une fenêtre de renommage. Modale `own` « renommer ». | à confirmer (`conversation.json`, `field`) |
| `KNOWN_MODALS` « suppression » | La confirmation de suppression, `[role="alertdialog"]`, ouverte par le clic sur « Supprimer », jugée sur son texte entier (annulée seulement si elle vise explicitement plusieurs conversations ou ne parle pas de supprimer une conversation, son titre pouvant tenir lieu du nom), validée par son seul bouton dont le texte suit `supprimer\|delete\|confirmer\|confirm\|effacer\|remove\|retirer`, à défaut le seul « Continuer », « Oui », « OK » ou « Valider » d'une fenêtre d'alerte (« Annuler », « Non », « Ne pas supprimer », « Garder » n'en sont jamais), jamais validée par la garde (Échap). Une confirmation native du navigateur n'est jamais répondue (§8.4). | à confirmer (`conversation.json`, `confirmation`) |
| `feedback_entry`, `feedback_words` | L'entrée de « Votre avis », `button[aria-label="Send feedback"]`, fixée en bas à gauche (puis les libellés exacts « Votre avis », « Envoyer un avis », « Donner votre avis », « Donnez votre avis » ; jamais un bouton deviné : `feedback_words` ne rend aucun bouton éligible, il nomme les candidats d'un refus dans `avis.json`, jamais au harnais ni un titre de conversation) ; l'enquête qu'elle ouvre doit être reconnue par `KNOWN_MODALS` « avis » (vue le 17/10 après les abandons). | entrée vue le 18/10 ; l'enquête qu'elle ouvre à confirmer (`avis.json`) |
| `input_status`, `notices` | Ce que dit la page d'une question restée sans réponse : `#chat-input-status` (`role="status"`, `aria-live="polite"`) sous le compositeur, les alertes et les toasts. | `#chat-input-status` vu le 18/10 ; son texte d'erreur et les toasts à confirmer (`failure.json`) |

Les symptômes d'un sélecteur faux :
- un KO `réponse vide : échec silencieux` alors que la réponse est visible dans Chrome : `assistant_message` ;
- `guard.json` avec `body_found: false`, et la note « corps de réponse introuvable » : `answer_body` ;
- `sent.json` avec `source: null` et `card.missing: "trigger"`, `rejected: "pas de bouton Détails sur la carte"` : `card_details_trigger` (le repli sur le popover a échoué aussi : `missing: "trigger"` y désigne `sent_trigger`, `missing: "popover"` désigne `sent_popover` ou `sent_text`) ;
- `sent.json` avec `source: "card-details"` et `rejected` (le texte ne compte pas, sans repli) : « le panneau ne s'est pas ouvert » ou « le panneau « Détails » ne montre aucun texte » désignent `card_details_content` (ou un bouton qui n'est pas le bon) ; « le panneau « Détails » ne montre pas le texte de la question » dit que le panneau montre un statut (voir `content_text` et `content_html`) ; « panneau « Détails » non lu (…) » : le clic ou la lecture a échoué (bouton caché ou recouvert) ; « délai de l'hôte » : l'action touchait à son échéance ;
- `sent.json` avec `closed: false` : le panneau « Détails » ne s'est pas refermé (`aria-expanded` resté à `true`), voir `card_details_trigger`. Un dialogue survenu pendant la lecture gèle la page : le panneau reste alors ouvert, et le refus `dialog` suit ;
- `sent.json` avec un `label` vide alors que `content_text` commence par un libellé : `details_label` ;
- un gros document « prêt » avec `ready_by: quiet` en 2 s : la puce ne montre aucun état reconnu, voir `attach_pending` et `attach_busy` ;
- toutes les réponses notées « affichage encore en mouvement », ou 30 s d'attente après chaque réponse : `stop_button` désigne un bouton qui ne disparaît pas ;
- `choisir_modele` ou `recherche_web` refusés `precondition` : `model_button`, `listbox`, `option` ou `web_button` (cherchés dans le compositeur seul depuis le deuxième tour de la revue du 20/10) ;
- v2.3 : `lister_conversations M8-4` refusé « la recherche ne s'ouvre pas » : `search_entry` et `search_input` ; refusé « refus du pilote : la recherche ne s'ouvre pas au clavier … son entrée n'est pas identifiée dans la barre latérale : rien n'est cliqué ; n candidat(s) dans search.json » (M8-4 NON TESTÉ) : le clavier n'a rien ouvert et aucun candidat n'a passé l'identification, lisez `search.json` (`candidates`) et `probe.json` de M0 (`search_entry`) ; « résultats inchangés par la frappe » (KO) : la palette a gardé la liste d'avant la frappe, voir `search.json` (`before`, `changed`) et `search_result` ; « menu refusé, une entrée viserait plus d'une conversation » ou « pas le menu d'une conversation (… exactes et seules de leur sorte …) » : les entrées réelles sont dans `conversation.json` (`menu.items`), `menu_rename` et `menu_delete` sont à ajuster, jamais à élargir ; « confirmation non reconnue … » ou « confirmation sans bouton de suppression unique » : son texte et ses boutons sont dans `conversation.json` (`confirmation`) ; `ouvrir_avis` refusé « refus du pilote : pas d'entrée « Votre avis » identifiée … ; n candidat(s) dans avis.json » : `feedback_entry` ; « pas de bouton de menu dans la ligne de la conversation » sur `renommer_conversation` : `conversation_menu_trigger`, `conversation_row` (lisez `probe.json` de M0, puis `conversation.json`) ; « le menu ouvert n'est pas celui du bouton de la conversation » : `menu` ; « pas de champ pour le nouveau titre » : `rename_field` ; « pas de confirmation de suppression » : `KNOWN_MODALS` « suppression » ; `ouvrir_avis` refusé `dialog` « refus du pilote : fenêtre non reconnue après l'entrée « Votre avis » » : l'enquête que l'entrée ouvre n'est pas celle de `KNOWN_MODALS` « avis ». Tous ces refus du pilote sont journalisés NON TESTÉ, jamais KO.

**Avant M8 : le menu des conversations.** Le résultat de `sonder_interface` (M0), noté par le harnais, dit « M8-5/M8-6 : menu accepté », « M8-5 : menu sans entrée de renommage ; M8-6 : menu accepté » (sixième tour : M8-5 sera NON TESTÉ, M8-6 joué ; lancez M8) ou « M8-5/M8-6 : menu refusé (…) ». **Sur tout autre texte que « menu accepté » (un menu refusé, ou « menu de la première conversation : … » quand la sonde n'a pas pu le lire), ne lancez pas M8** : M8-5 et M8-6 seraient journalisés NON TESTÉ (troisième tour de la revue). Lisez les entrées réelles dans `probe.json` (`menu.menu.items`), ajustez `menu_rename` et `menu_delete` à ces entrées exactes, jamais plus larges, puis suivez la procédure ci-dessous.

Après N5, relisez aussi le `model.json` de `choisir_modele` : sa liste `options` dit les modèles réellement proposés, et `gemini`, le modèle de N5-3, doit y figurer.

Dans tous ces cas :
1. Ne lancez pas le niveau suivant : entre deux niveaux, rien ne tourne. La tâche en cours est abandonnée telle quelle.
2. Corrigez `SELECTORS` dans `driver.py`.
3. Relancez `setup.ps1`, et recopiez le nouveau `files_sha256` qu'il affiche dans les deux manifestes.
4. Démontez le driver : `vk driver umount maskai-1` (signé avec la clé de l'appareil, comme le montage). Il garde ses observations en mémoire, donc on l'arrête avant de toucher à ses fichiers.
5. Mettez de côté le journal faux : renommez `$S\drivers\maskai-1\work\journal.jsonl`, `journal.cursor` et `observations.json` avec un suffixe daté. On ne les efface pas : ce sont des preuves.
6. Remontez le driver (§6.1). Attendu : un nouveau `driver_id`, puisque `files_sha256` a changé.
7. Soumettez de nouveau la recette (§7).

Autre point du premier passage : une modale ordinaire de MaskAI (sélecteur de modèle, menu) qui porterait `role=dialog` fait refuser `dialog`. Ajoutez-la à `KNOWN_MODALS`, puis suivez la même procédure. Une modification de `SELECTORS` ou de `KNOWN_MODALS` change `ui_profile_sha256` sur les lignes suivantes (§13) : le delta le montre.

**8.4 Un dialogue.** C'est attendu en N8, où l'injection HTML/JS est le test. Le pilote ne répond jamais à un dialogue : seul un humain répond à une boîte, et toujours par **Annuler** ou Échap, jamais par OK. Ce que vous voyez, et ce que vous faites :
1. **Le niveau s'arrête de lui-même**, le cas le plus courant. Le dialogue s'est ouvert pendant une action, et le driver l'a vu, ouvert ou déjà fermé (il garde un journal des dialogues). L'action rend `dialog`, le harnais appelle `verdict_oracle`, qui journalise le cas KO sécurité « dialogue inattendu » (le message expurgé ; le message brut dans la preuve `dialog.json`), puis `exporter_journal`, puis il s'arrête. Le niveau se ferme en `done`, et ses cas suivants ne sont pas joués dans cette tâche. Fermez le dialogue par Annuler, passez au niveau suivant (§8.2) ; les cas sautés se rattrapent par la reprise (§12).
Depuis le troisième tour de la revue, un dialogue relevé pendant l'action reste celui de l'action même quand l'action finit par son propre refus (un champ qui ne s'ouvre pas, une palette non reconnue) : refus `dialog`, KO sécurité, le refus d'origine dans `refused_first`, avec son `extra` (quatrième tour : un abandon pendant lequel un dialogue s'est ouvert n'écrit plus de ligne KO disponibilité ; le refus garde `internal_error_shown` et le profil d'abandon, et la ligne est KO sécurité « dialogue inattendu »).
2. **Le niveau semble figé, un dialogue à l'écran.** Il s'est ouvert pendant que le driver lisait la page, et cette lecture attend sa fermeture, son délai compris (Playwright 1.64, relevé sur le banc le 20/10 : une lecture déjà lancée ne revient qu'à la fermeture du dialogue ; `locator.count()` et `is_visible()` ne reviennent jamais, et le driver ne s'en sert plus). Après chaque clic de M8, le driver attend 100 ms et regarde avant de relire la page, mais un dialogue plus tardif tombe dans une lecture. Fermez-le par Annuler **avant la fin des 360 s de l'action** (`max_action_ms` du manifeste) : la lecture reprend, le driver retrouve le dialogue dans son journal, l'action rend `dialog`, et le cas est KO sécurité, comme en 1.
3. **Au-delà de 360 s**, le noyau tue le driver et Chrome (« deadline »), et le driver passe `degraded` (§11) : le cas n'est pas journalisé et le dialogue n'est pas noté. Lisez la fin de `$S\drivers\maskai-1\work\driver.log`, remontez le driver (§6.1), puis reprenez (§12).
4. **La confirmation native d'une suppression (M8-6).** Si MaskAI confirme la suppression par une boîte du navigateur (« Supprimer cette conversation ? ») plutôt que par une fenêtre de la page, le pilote ne la valide pas : `supprimer_conversation` rend `dialog` « refus du pilote : confirmation native … », M8-6 est journalisé NON TESTÉ, et le niveau s'arrête comme en 1. Annulez la boîte : la conversation de M8-1 reste dans MaskAI, et le registre la garde « suppression demandée », pour que le pilote ne la supprime jamais. Supprimez-la à la main après la recette si vous le souhaitez, et le rapport le dit.
5. **Une fenêtre inconnue après l'entrée de « Votre avis » (M8-8)** : `ouvrir_avis` rend `dialog`, M8-8 est NON TESTÉ, et la fenêtre reste ouverte. Fermez-la à la main, par sa croix ou Échap, jamais par « Envoyer ».
6. **Le popover « Chiffré »** du compositeur, ouvert à la main pendant un niveau, fait lui aussi refuser `dialog` : le driver ne referme que ce qu'il a ouvert lui-même. Fermez-le par Échap. Le panneau « Détails » d'une carte, lui, n'est pas une modale : ouvert à la main, il ne fait rien refuser et reste ouvert.

**8.5 Durées attendues.** Sur le banc d'essai, un niveau a pris 39 s pour 5 actions, et 44 s pour 12. Sur MaskAI :
- un cas message prend de 5 à 100 s, la garde AI Act de 3,5 s étant le plancher ;
- la v2 ajoute environ 3 s par question : 2 s d'affichage immobile avant de lire la réponse, et la lecture du popover « Chiffré » avant l'envoi ;
- la v2.2 ajoute moins d'une seconde par question : le panneau « Détails » ouvert, lu et refermé (0,4 s sur le banc), 1 s de plus quand il montre un statut plutôt que le texte ;
- un cas pièce jointe attend sa puce prête jusqu'à 3 × le budget du cas, puis la réponse jusqu'à 3 × le budget encore, soit 180 s au plus chacune pour les gros documents ;
- comptez de 1 h 30 à 2 h 30 pour les dix niveaux.

---

## 9. Juger, signer, rédiger, signer, publier

```powershell
vk task step $t                 # 12 juge
```
Attendu : `judge  done`, puis `approve  pending` sur la ligne 13.

**Avant de signer, lisez ce qui engage** (procédure §6, étape 5). D'abord les KO sécurité du journal, et les lignes qui portent `internal_error_shown: true` (une erreur interne montrée à l'utilisateur est une constatation de sécurité ; revue du 20/10, deuxième tour) :
```powershell
Get-Content "$S\drivers\maskai-1\work\journal.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { ($_.verdict -eq 'KO' -and $_.nfr -contains 'securite') -or $_.internal_error_shown } | Select-Object case, verdict, internal_error_shown, evidence | Format-List
```
Puis le verdict du juge lui-même (Ruling R22). Une approbation signe le **dernier** artefact du registre de la tâche, et c'est celui-là qu'on lit :
```powershell
$a = (vk task show $t --json | ConvertFrom-Json).artefacts
$a[-1].kind                     # attendu : verdict.md
vk artefact cat $a[-1].hash
```
Attendu : le texte du verdict, qui commence par `judge:`.

`vk artefact cat` est un acte humain, signé par la clé de l'appareil comme `vk approve`. C'est aussi une sortie de texte en clair hors du noyau. Le ledger l'inscrit donc avant de rendre les octets (Ruling R24) : un `artefact.released`, dont le hash engage la tâche, l'adresse de l'artefact et la destination `terminal:<appareil>`. Le ledger ne porte que ce hash, pas ces valeurs en clair. Une lecture refusée n'inscrit rien. Pour un artefact qui n'est pas du texte, ajoutez `--out fichier`. La page `vk task show $t` le dit aussi, sur la ligne `artefacts` : `N, latest verdict.md sha256:…`.

```powershell
vk approve $t                   # 13 : verdict et KO sécurité
```
Attendu : `approved sha256:…`, le même hash que `$a[-1].hash`, puis `run `vk task step … --all` to continue`.

```powershell
vk task step $t --all           # 13 se ferme, puis 14 rédaction ; s'arrête sur 15
```
Attendu : `draft  done` et `approve  waiting_human`.

Lisez le rapport avant de le signer, de la même façon :
```powershell
$a = (vk task show $t --json | ConvertFrom-Json).artefacts
$a[-1].kind                     # attendu : report.md
vk artefact cat $a[-1].hash --out "$env:TEMP\rapport-a-signer.md"
notepad "$env:TEMP\rapport-a-signer.md"
```
Attendu : `wrote N bytes to …`, puis le rapport dans le Bloc-notes, où les accents s'affichent sans faute quelle que soit la page de code du terminal.

**S'il ne convient pas, ne le signez pas.** La tâche reste en attente sur la ligne 15, et rien n'est publié. Supprimez la copie lue, qui peut porter ce qui doit rester privé :
```powershell
Remove-Item "$env:TEMP\rapport-a-signer.md"
```
Pour faire refaire le juge et la rédaction, appliquez la reprise (§12) : les dix niveaux sont rejoués depuis le journal sans toucher MaskAI.

S'il convient, signez-le, puis supprimez la copie lue de la même façon :
```powershell
vk approve $t                   # 15 : le rapport
Remove-Item "$env:TEMP\rapport-a-signer.md"
vk task step $t --all           # 15 se ferme, puis 16 publication
```
Attendu : `approved sha256:…`, puis `status    done` et seize lignes `done`. La ligne 16 donne le chemin complet de publication, sous `…\exports\recettage-chat-ia\runs\2026-10`.

Pourquoi `--all` ? `vk task step` exécute un seul pas. Après une signature, le premier pas exécuté ne fait que fermer l'approbation. `--all` enchaîne jusqu'au prochain point d'attente, ou jusqu'à la fin.

---

## 10. La preuve et la copie dans le dépôt

**10.1 Le ledger**
```powershell
vk ls /ledger
vk ledger verify
(vk dmesg -n 5000 --json | ConvertFrom-Json) | Group-Object kind | Select-Object Name, Count
```
Attendu :
- `N events, chain verifies` ;
- au moins 1 `driver.mounted` ;
- environ 160 `driver.acted` pour une passe sans reprise ;
- 2 `approval.recorded` de plus qu'avant la séance ;
- au moins 3 `artefact.released` de plus : la publication du pas 16, et une par lecture `vk artefact cat`. Il y a au moins les deux lectures avant signature, et `--out` compte comme une lecture.

**10.2 Le dossier publié**
```powershell
vk task show $t
$src = "$S\exports\recettage-chat-ia\runs\2026-10"
Get-ChildItem $src | Select-Object Name, Length
```
Attendu : des fichiers `<hash12>.<nom>`. On y trouve `report.md`, `verdict.md`, des `journal.jsonl` (un par export et par verdict), et les preuves : `screen.png`, `answer.png`, `attach.png`, `dom.html`, `network.json`, `timing.json`. Le préfixe est l'adresse de l'artefact, gardez les noms.

**Ce dossier peut contenir les fichiers d'une autre tâche.** La publication écrit dans le dossier existant sans le vider. Une tâche précédente publiée au même endroit y a laissé ses propres `report.md` et `verdict.md`, par exemple un rapport refusé puis refait. La reprise (§12) renomme le dossier avant de soumettre de nouveau, mais on ne copie jamais le dossier entier : seulement les fichiers dont le préfixe de 12 caractères est l'adresse d'un artefact de **cette** tâche. Les adresses dépendent de la tâche, donc deux tâches n'en partagent jamais.

**10.3 Expurger (contrainte 5), puis copier.** Le dépôt GitHub est **public**.
- **Un KO sécurité non corrigé par MaskAI**, c'est-à-dire un `KO` avec `securite` dans le journal, ou une ligne qui porte `internal_error_shown: true` (la requête du §9) : rien ne va dans le dépôt. Le rapport reste privé (contrainte 4). Prévenez MaskAI, et gardez le dossier hors du dépôt.
- **Sinon**, copiez les seuls fichiers de cette tâche, et parmi eux la seule liste blanche (`report.md`, `verdict.md`, `timing.json`, `journal.jsonl`), dans le dossier de préparation, puis cherchez les adresses, les comptes et les détails internes. Les preuves brutes du pilote ne se copient jamais : `chip.json`, `failure.json`, `notices.json`, `dialog.json`, `probe.json`, `search.json`, `conversation.json`, `avis.json`, `sent.json`, `guard.json`, `dom.html`, `network.json` (revue du 20/10, deuxième tour) :
```powershell
$mine = (vk task show $t --json | ConvertFrom-Json).artefacts | ForEach-Object { $_.hash.Substring(7, 12) }
$dst = "docs\functional\use-cases\recettage-chat-ia\runs\<AAAA-MM-JJ>"   # la date de la séance
New-Item -ItemType Directory -Force $dst | Out-Null
$allow = 'report.md', 'verdict.md', 'timing.json', 'journal.jsonl'   # la liste blanche : rien d'autre
$ours = Get-ChildItem $src -File | Where-Object { $mine -contains $_.Name.Substring(0, 12) }
$ours | Where-Object { $allow -contains $_.Name.Substring(13) } | Copy-Item -Destination $dst
"{0} fichiers de cette tâche, {1} copiés (liste blanche)" -f @($ours).Count, (Get-ChildItem $dst -File).Count
Select-String -Path "$dst\*.md","$dst\*.jsonl","$dst\*.json" -Encoding UTF8 -Pattern '\b(?:\d{1,3}\.){3}\d{1,3}\b','[\w.+-]+@[\w-]+\.[\w.]+','localhost:\d+','\b[a-z][a-z_-]{2,}[\w-]*:\d{2,5}\b','Traceback','File "','ENOTFOUND','getaddrinfo','\blookup\b','ECONNREFUSED|ETIMEDOUT|EHOSTUNREACH','[A-Za-z]:\\','/(?:srv|home|var|opt|usr|etc|app|tmp)/' | Select-Object Filename, LineNumber, Line
```
Attendu :
- `N fichiers de cette tâche, M copiés (liste blanche)` : M ne compte que les rapports, verdicts, `timing.json` et journaux de cette tâche ; les fichiers d'une autre tâche et les preuves brutes restent où ils sont.
- Puis aucune ligne de `Select-String`.

Chaque ligne trouvée se remplace par un marqueur (`<IP-INTERNE>`, `<COMPTE>`), comme dans le rapport du 2026-10-13.

Les PNG ne sont pas dans la liste blanche, et ne se cherchent pas par texte : une capture ne s'ajoute qu'à la main, après l'avoir regardée. Regardez-les un par un : le nom du compte apparaît-il dans l'en-tête de MaskAI ? Retirez du dossier ceux que vous ne publiez pas, et dites-le dans la section Traçabilité du rapport.
```powershell
git add docs/functional/use-cases/recettage-chat-ia/runs
git commit -m "recette(maskai): première recette gouvernée, rapport publié (version expurgée)"
```

---

## 11. Modes de défaillance (spec §6)

Ce tableau décrit ce que fait le système **tel qu'il est construit**. Là où il diffère de la spec, la différence est dite.

| Événement | Ce qui se passe | Ce que vous faites |
|---|---|---|
| Dialogue inattendu | Le cas est journalisé KO sécurité, même si vous avez fermé le dialogue avant que le driver regarde ; le niveau s'arrête et se ferme en `done`. Un dialogue ouvert pendant une lecture de la page gèle le niveau jusqu'à sa fermeture ; resté ouvert plus de 360 s (`max_action_ms`), le noyau tue le driver et Chrome, le driver est `degraded`, et le cas n'est ni journalisé ni noté. La confirmation native d'une suppression (M8-6) n'est jamais répondue : M8-6 NON TESTÉ. *La spec prévoyait `WaitingHuman` et la relance du même pas : ce n'est pas construit.* | Fermez par Annuler avant la fin des 360 s (§8.4), passez au niveau suivant, puis reprise (§12). Driver dégradé : fin de `driver.log`, remontage (§6.1), reprise (§12). |
| Suppression en cascade (M8-6) | Une autre conversation que celle de M8-1 a quitté la barre latérale pendant la suppression : M8-6 est KO sécurité, le registre note « suppression en cascade » et leur nombre, le résultat porte `cascade_delete`, et le harnais le note aussitôt (`vk_log` « SUPPRESSION EN CASCADE »). | Un défaut de sécurité : vérifiez dans MaskAI quelles conversations manquent (leurs adresses sont dans `conversation.json`, `others_gone`), prévenez MaskAI, gardez le rapport privé. |
| Délai d'action dépassé | Le driver abandonne à 3 × le budget du cas et journalise KO disponibilité. Le harnais passe au cas suivant. *La règle « trois de suite font échouer le pas » n'est pas construite.* | Si tout dépasse (MaskAI en panne) : `vk stop`, puis reprise plus tard. |
| Driver mort ou muet | `vk_driver_act` est refusé par le noyau, et le driver est `degraded` (`driver.unmounted{reason}`). La raison est un mot fixe (`dead`, `timeout`, `protocol: malformed response`, `would not start`…) : le détail (chemin, empreinte, extrait de la réponse) n'est jamais au ledger, il est dans `$S\drivers\maskai-1\work\driver.log`, ligne `[daemon] act … failed`. Le harnais s'arrête. Le pas finit `done` s'il avait déjà laissé une preuve, sinon `failed: harness produced no artefact`, et la tâche échoue. | **Avant chaque niveau**, `vk driver ls`. S'il est `degraded`, lisez la fin de `driver.log`, puis remontez avec la commande du §6.1 : c'est le remède. Si la tâche a échoué : reprise (§12). |
| Chrome affiche « Aw, Snap » (ou « Oh non ! »), ou le driver meurt `dead` sous une page lourde | Le plafond mémoire du Job (`program.max_memory_bytes`, 6 Gio) refuse les allocations de Chrome : faux KO disponibilité, ou driver dégradé. | Augmentez `max_memory_bytes` dans `manifest.json` (8589934592 pour 8 Gio), `vk driver umount maskai-1`, remontez (§6.1) : nouveau `driver_id`, `files_sha256` inchangé. Puis reprise (§12). |
| Service en maintenance (la carte « MAINTENANCE EN COURS / ON REVIENT TRÈS VITE ! », sans zone de saisie) | Les actions qui ont besoin de l'application rechargent la page une fois, puis refusent `precondition` « service en maintenance », sans attendre la zone de saisie. `verdict_oracle` journalise alors le cas KO disponibilité, « service en maintenance (page de maintenance affichée) ». Le cas suivant n'est pas jugé d'office : il est essayé, et refusé à son tour. | **À la première ligne « service en maintenance », ne lancez pas le niveau suivant** : `vk stop` n'est pas nécessaire. Reprenez plus tard (§12). Les cas journalisés pendant la maintenance sont rejoués tels quels à la reprise. Pour les rejouer pour de bon, mettez leurs lignes de côté par une correction (`supersedes`) ou mettez le journal de côté. |
| Navigation hors liste | L'action rend `host`. La page revient à l'entrée, le cas est KO sécurité, et l'hôte est au ledger (`hosts_seen`). | Un signal de sécurité. Si l'hôte est légitime : §6.3. |
| STOP humain | `vk stop` tue l'arbre du harnais et l'action en vol. Le driver est dégradé, et le pas finit `failed`, ce qui fait échouer la tâche. | `vk resume <stop_id>`, remontez le driver (§6.1), puis reprise (§12). |
| Démon tué (veille, fermeture de session) | Le journal est sur disque et le ledger est intact. Au redémarrage, le pas qui tournait est marqué `failed: interrupted`, et la tâche échoue. Le driver reste monté : il relance Chrome à sa première action, avec le même profil et la même session. | Relancez `vkd` (§3.2). Pour vérifier la session dans Chrome tout de suite, refaites `vk mount driver …` (§6.1), qui relance le processus. Puis reprise (§12). |
| Harnais emballé | `max_actions` le borne (3 × les actions du niveau + 10), et `timeout_s` aussi : 1800 s, ou 2700 s pour N4, N6 et N8. Au-delà, le refus du noyau arrête le harnais. | Lire `vk task show` ; reprendre si besoin. |
| Plus de 16 Mio de preuves dans un niveau | La règle du noyau (Ruling 21.6) refuse l'action, et le pas échoue en nommant le fichier. Sur le banc, un niveau pèse environ 60 Kio, mais sur MaskAI c'est **non mesuré**. | Si cela arrive en N4 ou N6 : signalez-le. Le remède est de couper le niveau en deux pas, ce qui change la recette. |
| Arch dont le nom est porté deux fois | `vk task submit` refuse (R21). | `vk umount <ID>` de l'ancienne arch (§5). |

---

## 12. La reprise

Le journal du driver est append-only et survit à tout : à la tâche, au démontage, au redémarrage du démon. Chaque cas commence par `verdict_oracle` :
- `replayed: true` : le cas est déjà journalisé, le harnais passe au suivant sans toucher MaskAI ;
- refus `precondition` : le cas reste à faire, le harnais le joue.

Le verdict rejoué est réinscrit au registre de la nouvelle tâche, ce qui permet au juge de voir tous les cas. **Pour reprendre, on soumet de nouveau la même recette.** Le registre des conversations (`conversations.json`, v2.3) ne se met jamais de côté : c'est lui qui dit à M8-5 et M8-6 quelle conversation la recette a créée. Cela vaut après un dialogue, une tâche échouée, un démon tué, ou un verdict ou un rapport qu'on refuse de signer. Avant de soumettre, deux choses sont à mettre de côté.

- **Les observations du driver.** `observations.json` garde la dernière observation de chaque cas. Depuis la v2.3, une observation partagée ne juge qu'un cas : une mesure de chargement (`@load`) qui a jugé N0-1 ne juge jamais N0-2, et une observation d'un pilote v2.2 qui porte déjà un verdict non plus (revue du 20/10). Le risque d'une ancienne mesure qui jugerait N0-2 est donc fermé. On retire tout de même le fichier : une reprise repart d'observations neuves, et les cas journalisés avant elle se rejugent hors ligne sur le fichier mis de côté (`correct.py --observations "$S\drivers\maskai-1\work\observations-essai$n.json"`, §6.5). Il se trouve dans `<dossier d'état>\drivers\maskai-1\work\observations.json`. Le driver le garde en mémoire, donc il faut l'arrêter avant de le déplacer.
- **Le dossier publié.** La publication écrit dans le dossier existant sans le vider. Une tâche déjà publiée y laisse son `report.md` et son `verdict.md` à côté des nouveaux. On le renomme : il devient la trace de l'essai précédent.

```powershell
$S = (vk status --json | ConvertFrom-Json).state_dir
$n = 1                                   # 1 au premier essai repris, puis 2, 3…
vk driver umount maskai-1               # signé avec la clé de l'appareil, comme le montage
$obs = "$S\drivers\maskai-1\work\observations.json"
if (Test-Path $obs) { Rename-Item $obs "observations-essai$n.json" -ErrorAction Stop }
$src = "$S\exports\recettage-chat-ia\runs\2026-10"
if (Test-Path $src) { Rename-Item $src "2026-10-essai$n" -ErrorAction Stop }
vk mount driver --manifest drivers\maskai-recette\manifest.json
$t = (vk task submit --steps recipes\recette-maskai.json --json | ConvertFrom-Json).id
```
Si un `Rename-Item` échoue, le nom `…essai$n` est déjà pris : arrêtez-vous, prenez le `$n` suivant et recommencez.

Attendu :
- `unmounted driver maskai-1`, puis le tableau `DRIVER … maskai-1 … yes` ;
- Chrome se rouvre, avec la même session MaskAI ;
- `vk task show $t` montre seize lignes `pending`.

Puis on refait les §8 et §9. Les niveaux dont tous les cas sont déjà journalisés passent en une vingtaine de secondes chacun. Le journal (`journal.jsonl`) et son curseur (`journal.cursor`) restent en place : ce sont eux qui font la reprise.

Chaque niveau finit par une capture d'écran inconditionnelle. Un pas de harnais qui ne laisse aucune preuve échoue (`harness produced no artefact`). Or un niveau dont tous les cas sont rejoués n'en laisserait aucune : un verdict rejoué n'a pas de preuve, et un export sans ligne nouvelle n'écrit pas de fichier. C'est vérifié sur le banc :
- un niveau entièrement rejoué, sans la capture, finit `failed` ;
- le même niveau, avec la capture, finit `done`.

---

## 13. Ce que veut dire `inputs_sha256`

Le verdict d'un cas ne dépend pas seulement du code du driver. Il dépend aussi de ses tables de calibration :
- `drivers/vkdriver/` ;
- `scripts/recette/evaluer.py` et `journal.py` ;
- `cases.json` ;
- les oracles (`corpus-v2/oracles-v2.json`, `corpus/oracles.json`) ;
- chaque fichier joint du corpus.

`inputs_sha256` est l'empreinte de tout cela.
- Sa version courte (12 caractères hexadécimaux) est dans le hello du driver : `version=0.2.0+<12 hex>`, dans les traces de `vkd` au montage (`driver mounted … version=…`).
- Sa version complète est sur **chaque ligne** du journal.

Deux exécutions dont les lignes portent la même `inputs_sha256` ont été jugées avec les mêmes règles. Si elle diffère, c'est un oracle, un document ou l'évaluateur qui a changé, et le delta entre les deux exécutions doit le dire. Le `driver_id`, lui, ne couvre que le dossier `drivers/maskai-recette/`.

Depuis la v2, chaque ligne porte aussi `ui_profile_sha256` : l'empreinte de `SELECTORS`, de `KNOWN_MODALS`, du motif du flux et des constantes de fin de réponse, c'est-à-dire des adresses et de la règle avec lesquelles la page a été lue. Le 17/10, l'essai au sélecteur faux et le passage corrigé portaient la même `inputs_sha256` (debrief, D12). Deux lignes de `ui_profile_sha256` différentes ont été lues avec des sélecteurs différents.

---

## 14. Répétition à blanc (facultative, environ 5 min)

Avec le driver v2, son niveau N4 joue aussi `fixture-case-3`, une conversation de vingt tours en une seule action (≈ 2 min) : c'est le contrôle de `max_action_ms` et de `MCP_TOOL_TIMEOUT` (§7.0) par la chaîne réelle, à faire avant la séance. Les chiffres ci-dessous sont ceux du 2026-10-15, avant la v2 : quatre `driver.acted` de plus sont attendus.

C'est la même chaîne que la séance réelle, sur le banc d'essai local et avec une arch simulée. Elle tourne dans un dossier d'état à part, pour ne pas mêler un faux run au ledger du nœud. Elle a été refaite le 2026-10-15 sur ce nœud, avec les lectures avant signature :
- les 8 pas sont passés à `done` ;
- 17 `driver.acted` ;
- 2 `approval.recorded` ;
- 3 `artefact.released` : les deux lectures `vk artefact cat` et la publication.

```powershell
$D = "$env:USERPROFILE\.cargo-target\vk-repetition"     # à effacer ensuite
$env:VK_ENDPOINT = '\\.\pipe\vk-repetition'
$env:VK_NODE_KEY_FILE = "$D\node.key"
Start-Process python -ArgumentList '-I','drivers\maskai-recette\fixture\serve.py','8765'
# dans un autre terminal (même $D, mêmes variables) :
$env:MCP_TOOL_TIMEOUT = "390000"
vkd --state-dir "$D\state" --endpoint '\\.\pipe\vk-repetition' --master-key-file "$D\master.key" --node-key-file "$D\node.key" --auto-enroll-node --web-port 0 --harness-timeout-secs 2700
# puis :
vk mount mock mock
vk mount driver --manifest drivers\maskai-recette\manifest-fixture.json
$t = (vk task submit --steps recipes\recette-fixture.json --json | ConvertFrom-Json).id
vk task step $t ; vk harness run $t ; vk harness run $t
vk task step $t
$a = (vk task show $t --json | ConvertFrom-Json).artefacts ; $a[-1].kind ; vk artefact cat $a[-1].hash
vk approve $t ; vk task step $t --all
$a = (vk task show $t --json | ConvertFrom-Json).artefacts ; $a[-1].kind ; vk artefact cat $a[-1].hash
vk approve $t ; vk task step $t --all
```
`$a[-1].kind` vaut `verdict.md`, puis `report.md`. Avec l'arch simulée, chaque texte commence par `judge: JUDGE: ROLE: judge`, puis par `draft: DRAFT: ROLE: draft`.
Attendu : `status    done`. Le dossier publié est sous `$D\state\exports\recettage-chat-ia\runs\fixture`.

Pour finir : Ctrl+C sur ce `vkd`, et fermez la fenêtre de `serve.py`. Puis `Remove-Item Env:VK_ENDPOINT, Env:VK_NODE_KEY_FILE` avant de revenir au vrai nœud.

---

## 15. Sortie du jalon A — liste de contrôle

- [ ] `vk task show $t` : seize lignes `done`, et `status done`.
- [ ] `vk ledger verify` : la chaîne se vérifie.
- [ ] Le ledger montre, depuis le début de la séance (`vk dmesg`, §10.1) :
  - au moins 1 `driver.mounted`. Un montage n'appartient à aucune tâche : c'est le pilote mis sous tension, et chaque remontage en ajoute un ;
  - les lignes `driver.acted` des dix niveaux ;
  - 2 `approval.recorded` pour la tâche qui publie : 1 de plus pour chaque verdict signé dans une tâche abandonnée ensuite ;
  - 1 `artefact.released` pour la publication de la tâche, plus 1 par lecture `vk artefact cat` (destination `terminal:…`), soit au moins 3.
- [ ] Les 34 cas de N0 à N9 (`cases.json`, hors banc d'essai) ont chacun une ligne au journal, ou bien sont nommés NON TESTÉ dans le rapport avec la raison.
- [ ] Le rapport, le verdict, `timing.json` et le journal (la liste blanche du §10.3, jamais les preuves brutes du pilote) sont sous `docs/functional/use-cases/recettage-chat-ia/runs/<date>/`. S'il reste un KO sécurité non corrigé, ou une ligne `internal_error_shown: true`, ils sont gardés privés et la raison est notée.
- [ ] Le delta avec `2026-10-13-maskai-benchmark.md` est écrit (notes par ENF, cinq défauts majeurs).
- [ ] Le garde-dialogue s'est déclenché au moins une fois en test : `test_dialog_guard_reports_and_never_answers`, déjà vert.
- [ ] La CI est verte.
- [ ] Le fondateur a reçu, en registre embarqué, ce que montre le ledger et ce que la recette a trouvé.

---

## 16. Après le tir : le debrief automatique

Le debrief lit ce que le nœud garde en clair après un tir (journaux, `driver.log`, console du démon, tables des tâches) et propose quoi changer, chaque proposition avec les lignes qui la fondent. Il ne change rien : c'est le post-mortem d'un crash dump, pas un correctif. Ce qui suit reste à vous : sélecteurs et modales (nouveau `driver_id`, remontage signé), budgets et oracles (nouvelle `inputs_sha256`, déclarée dans le delta), consigne de la recette, corrections `supersedes`, signatures.

Pour garder la console, lancez le démon du §3.2 ainsi, dans son terminal dédié :
```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$S = "$env:LOCALAPPDATA\VerticalAI\vk\data"
vkd --auto-enroll-node --harness-timeout-secs 2700 | ForEach-Object { $_; Add-Content -Path "$S\vkd-console.log" -Value $_ -Encoding UTF8 }
```
La première ligne compte : Windows PowerShell 5.1 lit la sortie de `vkd` dans la page de codes de la console (850 sur un poste français), et sans elle « refusée », « — » ou « × » arrivent abîmés dans le fichier ; le debrief ne lit plus alors ni les refus ni les modales. `Add-Content -Encoding UTF8` écrit de l'UTF-8. `$S` est ici le répertoire d'état par défaut, écrit en dur parce que le démon ne répond pas encore à `vk status` ; c'est celui que `vk status --json` donnera ensuite (un nœud lancé avec `--state-dir` met ici son propre répertoire).

Après le tir, assemblez le dossier et lancez le debrief. `$T` liste les tâches du tir (`vk ps`), ici celles du 17/10 ; un journal mis de côté au §8 se copie aussi, renommé `journal-<suffixe>.jsonl`. Chaque debrief a son dossier daté : un dossier de tir déjà rempli mêlerait les tâches de deux tirs.
```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$S = (vk status --json | ConvertFrom-Json).state_dir
$T = "task-node-1-2", "task-node-1-7", "task-node-1-16", "task-node-1-25", "task-node-1-39"
$D = Get-Date -Format "yyyy-MM-dd-HHmm"
$R = "$S\debrief\run-$D"; New-Item -ItemType Directory -Force "$R\tasks" | Out-Null
Copy-Item "$S\drivers\maskai-1\work\journal.jsonl", "$S\drivers\maskai-1\work\driver.log", "$S\vkd-console.log", drivers\maskai-recette\cases.json $R
foreach ($t in $T) { vk task show $t --json | Out-File -Encoding utf8 "$R\tasks\$t.json" }
vk dmesg -n 5000 --json | Out-File -Encoding utf8 "$R\dmesg.json"
vk driver debrief --run $R --out "$S\debrief\out-$D"
```
Attendu : `debrief: N proposals -> …`, une ligne par proposition (genre, cible, confiance, quoi changer), puis `nothing applied`. Lisez `debrief.md` (sections 1 à 3) ; les sections 4 et 5 sont les vôtres. Aucune ligne n'est ajoutée au ledger. `--until TASK` rejoue le debrief tel qu'il aurait été à la fin de cette tâche. Le dossier `--out` doit être neuf (ou vide), hors du dossier du tir et hors du dossier d'un driver : le debrief n'écrase rien, n'écrit jamais dans ce qu'il lit, et trois fichiers de plus dans un driver changeraient son `files_sha256`.

Écrivez chaque fichier avec `Out-File -Encoding utf8`, jamais avec `>` : Windows PowerShell 5.1 écrit alors de l'UTF-16, que le debrief refuse (`tasks/….json is UTF-16; remedy: rewrite it with Out-File -Encoding utf8`). Plus généralement, une pièce mal formée (champ manquant, horodatage sans fuseau, ligne coupée en pleine écriture) est refusée en une ligne qui nomme le fichier, la ligne et le champ : corrigez la copie dans `$R`, jamais l'original.

La console, `driver.log` et le journal du driver s'allongent d'un tir à l'autre (tant que le journal n'est pas mis de côté), et `vk dmesg` remonte avant le tir : copiez-les entiers. Le debrief n'en garde que la fenêtre du tir, de la création de la première tâche de `$T` à la fin de la dernière plus une minute (si une tâche tourne encore : jusqu'à l'heure du debrief, au plus 24 h après le dernier pas lancé — une ligne du lendemain matin encore dans ces 24 h est donc lue ; `--until` ne peut pas nommer une tâche qui tourne encore, le debrief le refuse), et, dans la console, les seules notes des tâches de `$T`. Une correction `supersedes` écrite après le tir compte tant qu'elle corrige une ligne du tir.

Les preuves de `proposals.json` ne reprennent jamais le texte d'une réponse, le texte envoyé ni le contenu d'un document (décision L-P57). Une ligne du journal y est coupée avant « réponse : « », avant « texte envoyé » et avant « manque : » (les motifs de l'oracle, qui disent les faits des documents) : il en reste le cas, le verdict, les durées et les nœuds du pipeline. Une ligne de `driver.log` n'y garde que les mots du driver avant le premier deux-points (sélecteur, modale, refus, minutage), jamais le texte de la page. Les notes du harnais sont reprises telles quelles : ce sont vos propres lignes `vk_log`. `debrief.md` et `debrief.json` ne portent que des identifiants de cas, des renvois `fichier:ligne`, des comptes et des titres de modale.
