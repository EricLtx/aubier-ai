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
files_sha256 : sha256:09235a3d30ed1291bd94bffeec9ce5105f72a882e09067f27f33a76b92891a77
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

Les quinze actions, dans l'ordre du manifeste : `mesurer_chargement`, `nouvelle_conversation`, `joindre_document`, `poser_question`, `envoyer_message`, `lire_reponse`, `capturer_flux`, `choisir_modele`, `recherche_web`, `conversation_longue`, `verdict_oracle`, `lister_conversations`, `charge_concurrente`, `capture_ecran`, `exporter_journal`. Les trois du milieu sont nouvelles en v2 (§6.4).

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
- v2.2 : une fois la réponse finie, le driver ouvre le panneau « Détails » de la dernière carte de réponse, lit le texte anonymisé envoyé au modèle, puis le referme d'un second clic (preuve `sent.json`, `source: "card-details"`). Le popover « Chiffré » décrit l'état avant l'envoi : il ne sert plus que pour une carte sans bouton « Détails » (`source: "composer-popover"`). Le bouton de révélation des PII de la carte n'est jamais pressé, son état est noté (`reveal_pressed`) ;
- `joindre_document` suit la puce (uploading, indexing, puis ready ou failed) au plus 3 × le budget du cas. `poser_question` relit la puce et refuse `precondition` si elle n'est pas prête : la question ne part pas, et `verdict_oracle` journalise « question non envoyée (précondition) » ;
- à l'abandon, le driver clique sur le bouton d'arrêt : l'enquête « Votre avis » ne suit plus. La ligne de journal dit ce qui était parti (POST, flux, dernier nœud) ;
- chaque ligne de journal porte `ui_profile_sha256` (§13), et les captures masquent la barre latérale et le bloc du compte.

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

**Premier passage : confirmer les SELECTORS.** C'est à faire après N0, N1 et N5. Le banc d'essai imite l'interface des 17 et 18/10, mais une partie des `SELECTORS` de `drivers/maskai-recette/driver.py` reste une hypothèse :

| Sélecteur | Rôle | Le 17/10 |
|---|---|---|
| `assistant_message` | **Le plus important.** La carte de réponse, `article:has(.prose-llm)`. | vu (`article[aria-label="MASKAI response"]`) |
| `answer_body` | Le corps de la réponse, `.prose-llm` : l'oracle ne lit que lui. | vu (`.pl-6 > .prose-llm`) |
| `model_banner`, `guard_pii`, `guard_clean` | L'en-tête de la carte lu en texte : bannière du modèle et panneau de garde (`guard.json`). | texte vu ; balisage à confirmer |
| `new_conversation` | Le bouton de nouvelle conversation. À défaut, le driver revient à `/new`. | vu (`a[href="/new"][data-slot="button"]`) |
| `conversation_items` | Les entrées de l'historique des conversations. | vu (`a[href*="/conversation/"]`) |
| `attach_remove` | Le bouton « Retirer <fichier> » ; la puce est son ancêtre qui porte du texte. | vu |
| `attach_pending`, `attach_uploading`, `attach_failed`, `attach_busy`, `attach_ready` | Les états de la puce. | « Indexing in progress » et « Failed » vus les 12–13/10 ; l'état prêt et l'indicateur d'activité à confirmer |
| `card_details_trigger` | **Le texte envoyé (v2.2).** Le bouton « Détails » de la dernière carte de réponse, cliqué une fois la réponse finie, puis recliqué pour refermer le panneau. Par structure d'abord, `button[data-slot="collapsible-trigger"][aria-label*="anonymis" i]`, puis le libellé « Détails » en secours ; cherché dans cette carte seulement. | relevé le 18/10 dans le DOM d'une carte stockée par la recette 2, après le panneau de garde : `data-slot="collapsible-trigger"`, `aria-label="Voir le texte anonymisé envoyé au LLM"`, `aria-expanded`, `data-state` ; à confirmer au premier passage |
| `card_details_content` | Le panneau ouvert, `[data-slot="collapsible-content"]` dans la même carte (ou l'élément que nomme `aria-controls`) : tout son texte est le texte envoyé (`sent.json`, `source: "card-details"`), son HTML est gardé (`content_html`). | attendu le 18/10 en collapsible Radix (`data-state="open"`) ; balisage intérieur à relever au premier passage, dans `content_html` |
| `pii_reveal` | Le bouton qui révèle les PII dans la réponse, `button[data-slot="pii-reveal-toggle"]`, juste après « Détails » : jamais pressé, son état noté (`reveal_pressed`). | relevé le 18/10 (`aria-pressed="false"`) ; à confirmer |
| `sent_trigger`, `sent_popover`, `sent_text` | Le repli (v2.2) d'une carte sans bouton « Détails » : la pastille « Chiffré » du compositeur, son popover et son bloc de texte (`sent.json`, `source: "composer-popover"`). | pastille vue (le 18/10 : `aria-label="Data encrypted — no PII detected in this conversation"`) ; le 18/10, le popover décrit l'état avant l'envoi (« Aucune PII détectée — rien à anonymiser pour l'instant ») |
| `stop_button` | Le bouton d'arrêt de la génération, cliqué à l'abandon. | un bouton vu sur la capture N1-3 ; aria-label à confirmer |
| `send_button` | Le bouton d'envoi, utilisé seulement si Entrée n'a rien envoyé. | vu le 18/10 (`aria-label="Send message"`, `data-action="send"`) |
| `model_button`, `listbox`, `option` | Le bouton du compositeur qui ouvre la liste des modèles (`choisir_modele`) : par structure d'abord, `button[aria-haspopup="listbox"]` (le compositeur n'est pas un `<form>` le 18/10), puis les libellés « Model: … » / « Modèle … » en secours. | liste vue le 18/10 : quatorze options `role="option"` dans une liste cmdk, `data-value` = identifiant fournisseur/modèle (`google/eu.claude-haiku-5-5`), texte = libellé affiché (« claude haiku 5-5 ») ; tous ouverts |
| `web_button`, `web_words` | Le bouton de recherche web (`recherche_web`) : par structure d'abord, `button[data-mode]` (`auto`, `off`, `on`), puis les libellés « Web search: … » / « Recherche web … » en secours. | cycle de clics vu le 18/10 : auto → on → off → auto, sans menu ; `data-mode` fait foi |
| `mic_button` | Le bouton de dictée (micro), `button[data-status][aria-pressed]` : jamais activé ; un état autre que `idle` au début d'une action est un refus `precondition` « dictée en cours » (§11). | vu le 18/10 (`aria-label="Dictate a message in real time (microphone)"`, `data-status="idle"`) |
| `auth_error` | La page d'erreur d'authentification, `/api/auth/error` sur l'URL, et la page d'entrée qui renvoie après chargement vers l'hôte d'authentification hors liste (profil du pilote non connecté) : la garde recharge la page d'entrée une fois, puis refuse `precondition` « session perdue » ou « non connecté » (§11) ; `mesurer_chargement` ne mesure jamais une page de connexion. | vues le 18/10 : l'erreur pendant une coupure réseau, revenue d'elle-même ; la redirection vers `authentification.maskai.fr` au premier M0 de la recette 2 (M0-1 et M0-2 avaient mesuré la page de connexion) |
| `maintenance` | La page de maintenance (« MAINTENANCE EN COURS / ON REVIENT TRÈS VITE ! »), lue sur le texte quand aucune zone de saisie n'est visible (§11). | vue au premier essai réel de la recette 2 |
| `mask` | Les zones masquées des captures : la barre latérale et le bloc du compte. | `nav[aria-label="Main navigation"]` et `aside` vus ; `data-slot` à confirmer |
| `sent_label` | Le panneau « SENT TO THE LLM » du 13/10, en secours du popover. | disparu |

Les symptômes d'un sélecteur faux :
- un KO `réponse vide : échec silencieux` alors que la réponse est visible dans Chrome : `assistant_message` ;
- `guard.json` avec `body_found: false`, et la note « corps de réponse introuvable » : `answer_body` ;
- `sent.json` avec `source: null` : `card.missing: "trigger"` et `rejected: "pas de bouton Détails sur la carte"` désignent `card_details_trigger` ; `missing: "content"` désigne `card_details_content` ; `rejected: "le panneau « Détails » ne montre pas le texte de la question"` dit que le panneau montre un statut (voir `text` et `content_html`) ; en repli sur le popover, `missing: "trigger"` désigne `sent_trigger`, `missing: "popover"` désigne `sent_popover` ou `sent_text` ;
- `sent.json` avec `closed: false` : le panneau « Détails » ne s'est pas refermé (`aria-expanded` resté à `true`), voir `card_details_trigger` ; avec `error` : le clic sur « Détails » a échoué (bouton caché ou recouvert) ;
- un gros document « prêt » avec `ready_by: quiet` en 2 s : la puce ne montre aucun état reconnu, voir `attach_pending` et `attach_busy` ;
- toutes les réponses notées « affichage encore en mouvement », ou 30 s d'attente après chaque réponse : `stop_button` désigne un bouton qui ne disparaît pas ;
- `choisir_modele` ou `recherche_web` refusés `precondition` : `model_button`, `listbox`, `option` ou `web_button`.

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

**8.4 Un dialogue.** C'est attendu en N8, où l'injection HTML/JS est le test. Le harnais appelle `verdict_oracle`, qui journalise le cas en KO sécurité, puis `exporter_journal`, puis il s'arrête. Le niveau se ferme en `done`, et ses cas suivants ne sont pas joués dans cette tâche.
1. Dans Chrome, fermez le dialogue par **Annuler** ou Échap, jamais par OK. Le driver ne répond jamais lui-même à un dialogue. Le popover « Chiffré » du compositeur, ouvert à la main pendant un niveau, fait lui aussi refuser `dialog` : le driver ne referme que ce qu'il a ouvert lui-même. Fermez-le par Échap. Le panneau « Détails » d'une carte, lui, n'est pas une modale : ouvert à la main, il ne fait rien refuser et reste ouvert.
2. Continuez avec le niveau suivant (§8.2).
3. Les cas sautés se rattrapent par la reprise (§12).

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

**Avant de signer, lisez ce qui engage** (procédure §6, étape 5). D'abord les KO sécurité du journal :
```powershell
Get-Content "$S\drivers\maskai-1\work\journal.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.verdict -eq 'KO' -and $_.nfr -contains 'securite' } | Select-Object case, evidence | Format-List
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
- **Un KO sécurité non corrigé par MaskAI**, c'est-à-dire un `KO` avec `securite` dans le journal : rien ne va dans le dépôt. Le rapport reste privé (contrainte 4). Prévenez MaskAI, et gardez le dossier hors du dépôt.
- **Sinon**, copiez les seuls fichiers de cette tâche dans le dossier de préparation, puis cherchez les adresses et les comptes :
```powershell
$mine = (vk task show $t --json | ConvertFrom-Json).artefacts | ForEach-Object { $_.hash.Substring(7, 12) }
$dst = "docs\functional\use-cases\recettage-chat-ia\runs\<AAAA-MM-JJ>"   # la date de la séance
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -File | Where-Object { $mine -contains $_.Name.Substring(0, 12) } | Copy-Item -Destination $dst
"{0} fichiers dans le dossier publié, {1} copiés" -f (Get-ChildItem $src -File).Count, (Get-ChildItem $dst -File).Count
Select-String -Path "$dst\*.md","$dst\*.jsonl","$dst\*.json","$dst\*.html" -Encoding UTF8 -Pattern '\b(?:\d{1,3}\.){3}\d{1,3}\b','[\w.+-]+@[\w-]+\.[\w.]+','localhost:\d+' | Select-Object Filename, LineNumber, Line
```
Attendu :
- `N fichiers dans le dossier publié, N copiés`. Les deux nombres sont égaux quand le dossier ne contient que cette tâche ; s'ils diffèrent, les fichiers en trop sont d'une autre tâche, et ils restent où ils sont.
- Puis aucune ligne de `Select-String`.

Chaque ligne trouvée se remplace par un marqueur (`<IP-INTERNE>`, `<COMPTE>`), comme dans le rapport du 2026-10-13.

Les PNG ne se cherchent pas par texte. Regardez-les un par un : le nom du compte apparaît-il dans l'en-tête de MaskAI ? Retirez du dossier ceux que vous ne publiez pas, et dites-le dans la section Traçabilité du rapport.
```powershell
git add docs/functional/use-cases/recettage-chat-ia/runs
git commit -m "recette(maskai): première recette gouvernée, rapport publié (version expurgée)"
```

---

## 11. Modes de défaillance (spec §6)

Ce tableau décrit ce que fait le système **tel qu'il est construit**. Là où il diffère de la spec, la différence est dite.

| Événement | Ce qui se passe | Ce que vous faites |
|---|---|---|
| Dialogue inattendu | Le cas est journalisé KO sécurité, le niveau s'arrête et se ferme en `done`. *La spec prévoyait `WaitingHuman` et la relance du même pas : ce n'est pas construit.* | Fermez par Annuler, passez au niveau suivant, puis reprise (§12). |
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

Le verdict rejoué est réinscrit au registre de la nouvelle tâche, ce qui permet au juge de voir tous les cas. **Pour reprendre, on soumet de nouveau la même recette.** Cela vaut après un dialogue, une tâche échouée, un démon tué, ou un verdict ou un rapport qu'on refuse de signer. Avant de soumettre, deux choses sont à mettre de côté.

- **Les observations du driver.** `observations.json` garde la dernière mesure non jugée. N0-1 et N0-2 se jugent sur la même mesure de chargement (`@load`). Si N0-1 est journalisé et N0-2 ne l'est pas, une ancienne mesure resterait là pour juger N0-2. L'instruction N0 mesure déjà avant le verdict de N0-2 ; on retire en plus le fichier. Il se trouve dans `<dossier d'état>\drivers\maskai-1\work\observations.json`. Le driver le garde en mémoire, donc il faut l'arrêter avant de le déplacer.
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
- [ ] Le rapport, le journal et les preuves expurgés sont sous `docs/functional/use-cases/recettage-chat-ia/runs/<date>/`. S'il reste un KO sécurité non corrigé, ils sont gardés privés et la raison est notée.
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
