# Runbook — recette MaskAI gouvernée (pilote Aubier, jalon A)

Pour le fondateur, sur son nœud Windows, dans sa session interactive. Version du 2026-10-15.

- Recette exécutée : `recipes/recette-maskai.json`, seize pas.
- Procédure suivie : `docs/use-cases/recettage-chat-ia/PROCEDURE.md`.
- Répétition à blanc : `recipes/recette-fixture.json`, huit pas, sur le banc d'essai local.

Chaque étape donne la commande exacte, puis la ligne attendue (« Attendu »). Toutes les commandes se tapent dans PowerShell, à la racine du dépôt. Si la sortie ne ressemble pas à la ligne attendue, on s'arrête et on lit le §11 avant d'aller plus loin.

---

## 0. La carte en une minute

Le pilote se lit comme un système embarqué.

| Ici | En firmware |
|---|---|
| `vkd` | Le noyau du RTOS. Il ordonnance les pas et tient le registre de la tâche. Il écrit le ledger : une boîte noire append-only, chaînée par hash. |
| Une arch (`claude-code/…`) | Un coprocesseur que le noyau appelle pour le plan, le jugement et la rédaction. |
| Le driver `maskai-1` | Un pilote de périphérique. Le périphérique est Chrome, le bus est Playwright, et les douze actions du manifeste sont l'API du pilote. |
| `hosts` du manifeste | La table MPU du pilote. Une navigation hors de la liste est défaite, et l'action rend `host`. |
| Le harnais (Claude Code confiné) | Une tâche utilisateur sans privilège. Son seul appel système utile est `vk_driver_act`. |
| `max_actions`, `timeout_s` d'un pas | Le budget d'appels de la tâche, et son chien de garde. |
| `vkd --harness-timeout-secs` | Le chien de garde global. Le plus court des deux chiens de garde gagne. |
| `vk approve` | Le point de validation, signé par la clé de l'appareil : la preuve de présence humaine. |
| `vk stop` | L'arrêt d'urgence. Il tue l'arbre du harnais et l'action en vol. |
| `driver_id`, `files_sha256` | L'empreinte du firmware du pilote. Un octet changé dans `drivers/maskai-recette/` donne un autre pilote. |
| `inputs_sha256` | L'empreinte des tables de calibration du verdict (§13). |

Une séance complète se déroule ainsi :
1. On démarre `vkd`, on monte deux arches et le driver, puis on se connecte à MaskAI à la main.
2. On soumet la recette, puis on lance un pas de plan et dix exécutions du harnais (N0 à N9).
3. On lance le juge, puis on signe. On lance la rédaction, puis on signe. On publie.
4. On copie le résultat dans le dépôt, après l'avoir expurgé.

---

## 1. Prérequis, une fois par nœud et par montage (P8)

Docker n'est pas requis.

**1.1 Python 3.13**
```powershell
python --version
```
Attendu : `Python 3.13.x`

**1.2 Le virtualenv du driver** : Playwright 1.64.0, Chromium, et le contrôle de l'empreinte.
```powershell
powershell -ExecutionPolicy Bypass -File drivers\maskai-recette\setup.ps1
```
Attendu, en dernières lignes :
```
files_sha256 : sha256:c973188ee5dc12e1bdf289c54ee2340476dbba29ea0e403b5572b1dc867d8a2f
manifest.json : à jour
manifest-fixture.json : à jour
```
Pourquoi un virtualenv à part ? Les chemins internes de Playwright dépassent la limite MAX_PATH de 260 caractères sous OneDrive. Le virtualenv vit donc sous `%LOCALAPPDATA%\vk\venvs\maskai-recette`, et `drivers\maskai-recette\.venv` est une jonction vers lui. `.venv` est exclu de l'empreinte.

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
$env:PATH = "$env:CARGO_TARGET_DIR\release;$env:PATH"
```
Attendu : `Finished `release` profile [optimized] target(s) in …`

La ligne `$env:PATH` est à refaire dans chaque terminal ouvert pour la séance.

---

## 3. Démarrer le noyau

Le démon tourne en mode interactif, dans son propre terminal (P7) : Chrome doit s'ouvrir sur votre bureau, et un service en session 0 ne le peut pas.

**3.1 Un seul démon à la fois.** Si `vk status` répond, un `vkd` tourne déjà, par exemple lancé par `vk boot`. Arrêtez-le : Ctrl+C dans son terminal, ou bien :
```powershell
Get-Process vkd -ErrorAction SilentlyContinue | Stop-Process
```

**3.2 Démarrage**, dans un terminal dédié qu'on laisse ouvert toute la séance :
```powershell
vkd --auto-enroll-node --harness-timeout-secs 2700
```
Attendu, dans les traces :
```
vkd booted ledger_ok=true …
vkd listening endpoint=\\.\pipe\…
```

Pourquoi `vkd` et pas `vk boot` ? `vk boot` ne transmet pas `--harness-timeout-secs`. Le chien de garde global resterait alors à 300 s, et il couperait chaque niveau à cinq minutes. Les pas lourds (N4, N6, N8) demandent `timeout_s = 2700`. Le démon prend le plus petit des deux budgets, donc le sien doit être au moins aussi grand.

Fermer ce terminal, c'est couper l'alimentation : le driver et son Chrome meurent avec le démon. C'est voulu (Job Object, kill-on-close).

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

**6.1 Montage** (un acte humain, signé) :
```powershell
vk mount driver --manifest drivers\maskai-recette\manifest.json
```
Attendu :
```
DRIVER    ID          GOVERNED
maskai-1  sha256:…    yes
```

Chrome s'ouvre sur **https://app.maskai.fr/new**, avec le profil dédié du driver (sous le dossier d'état, jamais votre profil personnel). **Connectez-vous à la main** avec le compte de test. Le noyau ne voit jamais le mot de passe (procédure §4).

**6.2 Vérification**
```powershell
vk driver ls
```
Attendu : `maskai-1  ready  yes  mesurer_chargement,…,exporter_journal  -`

`governed: yes` veut dire que le Job Object a confirmé le confinement : 2 Gio, et l'arbre est tué avec le démon.

**6.3 Ajouter un hôte à `hosts`, seulement si nécessaire.** La liste ne porte que sur les navigations de la page principale. Les CDN, polices et analytics d'autres hôtes ne sont pas bloqués : ils sont relevés dans `third_party_hosts` (`capturer_flux`, `network.json`). La connexion au montage n'est pas gardée non plus, donc une redirection vers un fournisseur d'identité y fonctionne.

Il faut ajouter un hôte quand une action rend `host` en nommant un hôte légitime. Cas typique : la session a expiré, et `/new` redirige vers l'identité pendant un pas.
1. Ajoutez l'hôte dans `"hosts"` de `drivers\maskai-recette\manifest.json`.
2. Démontez le driver : `vk driver umount maskai-1`. Attendu : `unmounted driver maskai-1`.
3. Remontez-le avec la commande du §6.1. Attendu : un **nouveau** `driver_id`, car le manifeste a changé. `files_sha256` est inchangé : les manifestes sont exclus de l'empreinte, rien n'est à régénérer.

Le profil Chrome est conservé au démontage, et la session MaskAI avec lui.

---

## 7. Soumettre la recette

```powershell
vk task submit --steps recipes\recette-maskai.json
```
Attendu : `task      task-node-1-N`, puis seize lignes. La 1 est `plan`, les 2 à 11 sont `harness`, puis viennent 12 `judge`, 13 `approve`, 14 `draft`, 15 `approve` et 16 `release`. Le détail de la ligne 16 est `recettage-chat-ia/runs/2026-10`.

Notez l'id de la tâche :
```powershell
$t = "task-node-1-N"
```
Remplacez `N` par le numéro affiché.

---

## 8. Le plan, puis les dix niveaux

**8.1 Le plan**
```powershell
vk task step $t
```
Attendu : la ligne 1 passe à `plan  done`.

**8.2 Un niveau.** Répétez ce bloc dix fois, de N0 à N9 :
```powershell
vk driver ls          # maskai-1 doit être "ready" ; sinon §11, ligne « driver dégradé »
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

**Premier passage : confirmer les SELECTORS.** C'est à faire après N0 et après N1. Sept sélecteurs de `drivers/maskai-recette/driver.py` sont des hypothèses, que seul le banc d'essai a validées :

| Sélecteur | Rôle |
|---|---|
| `assistant_message` | **Le plus important.** La réponse est lue par lui. |
| `new_conversation` | Le bouton de nouvelle conversation. À défaut, le driver revient à `/new`. |
| `send_button` | Le bouton d'envoi, utilisé seulement si Entrée n'a rien envoyé. |
| `stop_button` | Le bouton d'arrêt de la génération. |
| `attach_chip` | L'élément qui montre l'état de la pièce jointe. |
| `conversation_items` | Les entrées de l'historique des conversations. |
| `sent_label` | La disposition du panneau « SENT TO THE LLM ». |

Le symptôme d'un sélecteur faux est un KO `réponse vide : échec silencieux` alors que la réponse est visible dans Chrome. C'est `assistant_message` qui est faux. Dans ce cas :
1. Ne lancez pas le niveau suivant : entre deux niveaux, rien ne tourne. La tâche en cours est abandonnée telle quelle.
2. Corrigez `SELECTORS` dans `driver.py`.
3. Relancez `setup.ps1`, et recopiez le nouveau `files_sha256` qu'il affiche dans les deux manifestes.
4. Mettez de côté le journal faux : renommez `$S\drivers\maskai-1\work\journal.jsonl`, `journal.cursor` et `observations.json` avec un suffixe daté. On ne les efface pas : ce sont des preuves.
5. Démontez et remontez le driver (§6.3).
6. Soumettez de nouveau la recette (§7).

Autre point du premier passage : une modale ordinaire de MaskAI (sélecteur de modèle, menu) qui porterait `role=dialog` fait refuser `dialog`. Ajoutez-la à `KNOWN_MODALS`, puis suivez la même procédure.

**8.4 Un dialogue.** C'est attendu en N8, où l'injection HTML/JS est le test. Le harnais appelle `verdict_oracle`, qui journalise le cas en KO sécurité, puis `exporter_journal`, puis il s'arrête. Le niveau se ferme en `done`, et ses cas suivants ne sont pas joués dans cette tâche.
1. Dans Chrome, fermez le dialogue par **Annuler** ou Échap, jamais par OK. Le driver ne répond jamais lui-même à un dialogue.
2. Continuez avec le niveau suivant (§8.2).
3. Les cas sautés se rattrapent par la reprise (§12).

**8.5 Durées attendues.** Sur le banc d'essai, un niveau a pris 39 s pour 5 actions, et 44 s pour 12. Sur MaskAI :
- un cas message prend de 5 à 100 s, la garde AI Act de 3,5 s étant le plancher ;
- un cas pièce jointe peut aller jusqu'à 60 s d'indexation, plus 3 × le budget du cas, soit 180 s au plus pour les gros documents ;
- comptez de 1 h 30 à 2 h 30 pour les dix niveaux.

---

## 9. Juger, signer, rédiger, signer, publier

```powershell
vk task step $t                 # 12 juge
```
Attendu : `judge  done`, puis `approve  pending` sur la ligne 13.

**Avant de signer, lisez ce qui engage** (procédure §6, étape 5) : les KO sécurité du journal.
```powershell
Get-Content "$S\drivers\maskai-1\work\journal.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.verdict -eq 'KO' -and $_.nfr -contains 'securite' } | Select-Object case, evidence | Format-List
```

> **Limite connue du pilote.** `vk` n'affiche pas le texte d'un artefact avant sa publication. `/artefacts/<hash>` ne rend que l'enveloppe. Vous signez donc `verdict.md`, puis `report.md`, sur la foi du journal ; leur texte ne se lit qu'après le pas 16, dans le dossier publié. Si le texte publié ne convient pas, on ne le copie pas dans le dépôt, et on relance les pas 12 à 16 dans une nouvelle tâche.

```powershell
vk approve $t                   # 13 : verdict et KO sécurité
```
Attendu : `approved sha256:…`, puis `run `vk task step … --all` to continue`.

```powershell
vk task step $t --all           # 13 se ferme, puis 14 rédaction ; s'arrête sur 15
```
Attendu : `draft  done` et `approve  waiting_human`.

```powershell
vk approve $t                   # 15 : le rapport
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
- 1 `artefact.released` de plus.

**10.2 Le dossier publié**
```powershell
vk task show $t
$src = "$S\exports\recettage-chat-ia\runs\2026-10"
Get-ChildItem $src | Select-Object Name, Length
```
Attendu : des fichiers `<hash12>.<nom>`. On y trouve `report.md`, `verdict.md`, des `journal.jsonl` (un par export et par verdict), et les preuves : `screen.png`, `answer.png`, `attach.png`, `dom.html`, `network.json`, `timing.json`. Le préfixe est l'adresse de l'artefact, gardez les noms.

**10.3 Expurger (contrainte 5), puis copier.** Le dépôt GitHub est **public**.
- **Un KO sécurité non corrigé par MaskAI**, c'est-à-dire un `KO` avec `securite` dans le journal : rien ne va dans le dépôt. Le rapport reste privé (contrainte 4). Prévenez MaskAI, et gardez le dossier hors du dépôt.
- **Sinon**, copiez dans un dossier de préparation, puis cherchez les adresses et les comptes :
```powershell
$dst = "docs\use-cases\recettage-chat-ia\runs\2026-10-15"   # la date de la séance
New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item "$src\*" $dst
Select-String -Path "$dst\*.md","$dst\*.jsonl","$dst\*.json","$dst\*.html" -Encoding UTF8 -Pattern '\b(?:\d{1,3}\.){3}\d{1,3}\b','[\w.+-]+@[\w-]+\.[\w.]+','localhost:\d+' | Select-Object Filename, LineNumber, Line
```
Attendu : aucune ligne. Chaque ligne trouvée se remplace par un marqueur (`<IP-INTERNE>`, `<COMPTE>`), comme dans le rapport du 2026-10-13.

Les PNG ne se cherchent pas par texte. Regardez-les un par un : le nom du compte apparaît-il dans l'en-tête de MaskAI ? Retirez du dossier ceux que vous ne publiez pas, et dites-le dans la section Traçabilité du rapport.
```powershell
git add docs/use-cases/recettage-chat-ia/runs
git commit -m "recette(maskai): première recette gouvernée, rapport publié (version expurgée)"
```

---

## 11. Modes de défaillance (spec §6)

Ce tableau décrit ce que fait le système **tel qu'il est construit**. Là où il diffère de la spec, la différence est dite.

| Événement | Ce qui se passe | Ce que vous faites |
|---|---|---|
| Dialogue inattendu | Le cas est journalisé KO sécurité, le niveau s'arrête et se ferme en `done`. *La spec prévoyait `WaitingHuman` et la relance du même pas : ce n'est pas construit.* | Fermez par Annuler, passez au niveau suivant, puis reprise (§12). |
| Délai d'action dépassé | Le driver abandonne à 3 × le budget du cas et journalise KO disponibilité. Le harnais passe au cas suivant. *La règle « trois de suite font échouer le pas » n'est pas construite.* | Si tout dépasse (MaskAI en panne) : `vk stop`, puis reprise plus tard. |
| Driver mort ou muet | `vk_driver_act` est refusé par le noyau, et le driver est `degraded` (`driver.unmounted{reason}`). Le harnais s'arrête. Le pas finit `done` s'il avait déjà laissé une preuve, sinon `failed: harness produced no artefact`, et la tâche échoue. | **Avant chaque niveau**, `vk driver ls`. S'il est `degraded`, remontez avec la commande du §6.1 : c'est le remède. Si la tâche a échoué : reprise (§12). |
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

Le verdict rejoué est réinscrit au registre de la nouvelle tâche, ce qui permet au juge de voir tous les cas. **Pour reprendre, on soumet de nouveau la même recette** (§7), puis on refait les §8 et §9.

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
- Sa version courte (12 caractères hexadécimaux) est dans le hello du driver : `version=0.1.0+<12 hex>`, dans les traces de `vkd` au montage (`driver mounted … version=…`).
- Sa version complète est sur **chaque ligne** du journal.

Deux exécutions dont les lignes portent la même `inputs_sha256` ont été jugées avec les mêmes règles. Si elle diffère, c'est un oracle, un document ou l'évaluateur qui a changé, et le delta entre les deux exécutions doit le dire. Le `driver_id`, lui, ne couvre que le dossier `drivers/maskai-recette/`.

---

## 14. Répétition à blanc (facultative, environ 3 min)

C'est la même chaîne que la séance réelle, sur le banc d'essai local et avec une arch simulée. Elle tourne dans un dossier d'état à part, pour ne pas mêler un faux run au ledger du nœud. Elle a été faite le 2026-10-15 sur ce nœud : les 8 pas sont passés à `done`, avec 17 `driver.acted`, 2 `approval.recorded` et 1 `artefact.released`.

```powershell
$D = "$env:USERPROFILE\.cargo-target\vk-repetition"     # à effacer ensuite
$env:VK_ENDPOINT = '\\.\pipe\vk-repetition'
$env:VK_NODE_KEY_FILE = "$D\node.key"
Start-Process python -ArgumentList '-I','drivers\maskai-recette\fixture\serve.py','8765'
# dans un autre terminal (même $D, mêmes variables) :
vkd --state-dir "$D\state" --endpoint '\\.\pipe\vk-repetition' --master-key-file "$D\master.key" --node-key-file "$D\node.key" --auto-enroll-node --web-port 0 --harness-timeout-secs 2700
# puis :
vk mount mock mock
vk mount driver --manifest drivers\maskai-recette\manifest-fixture.json
vk task submit --steps recipes\recette-fixture.json      # notez $t
vk task step $t ; vk harness run $t ; vk harness run $t
vk task step $t ; vk approve $t ; vk task step $t --all ; vk approve $t ; vk task step $t --all
```
Attendu : `status    done`. Le dossier publié est sous `$D\state\exports\recettage-chat-ia\runs\fixture`.

Pour finir : Ctrl+C sur ce `vkd`, et fermez la fenêtre de `serve.py`. Puis `Remove-Item Env:VK_ENDPOINT, Env:VK_NODE_KEY_FILE` avant de revenir au vrai nœud.

---

## 15. Sortie du jalon A — liste de contrôle

- [ ] `vk task show $t` : seize lignes `done`, et `status done`.
- [ ] `vk ledger verify` : la chaîne se vérifie.
- [ ] Le ledger montre, pour cette tâche :
  - au moins 1 `driver.mounted` ;
  - les lignes `driver.acted` des dix niveaux ;
  - 2 `approval.recorded` ;
  - 1 `artefact.released`.
- [ ] Les 37 cas de N0 à N9 ont chacun une ligne au journal, ou bien sont nommés NON TESTÉ dans le rapport avec la raison.
- [ ] Le rapport, le journal et les preuves expurgés sont sous `docs/use-cases/recettage-chat-ia/runs/<date>/`. S'il reste un KO sécurité non corrigé, ils sont gardés privés et la raison est notée.
- [ ] Le delta avec `2026-10-13-maskai-benchmark.md` est écrit (notes par ENF, cinq défauts majeurs).
- [ ] Le garde-dialogue s'est déclenché au moins une fois en test : `test_dialog_guard_reports_and_never_answers`, déjà vert.
- [ ] La CI est verte.
- [ ] Le fondateur a reçu, en registre embarqué, ce que montre le ledger et ce que la recette a trouvé.
