# Runbook — apprendre un driver Office (Word, PowerPoint) à partir des documents du cabinet

Pour le fondateur, sur son nœud Windows, dans sa session interactive. Version du 2026-10-19.

- Spec : `docs/technical/specs/2026-10-17-driver-learning-office-design.md`.
- Section technique : `docs/technical/technical-readme.md`, « Learned drivers ».
- Ce que le noyau garantit et ce qu'il croit sur parole : `contracts/tcb.md`, §Drivers.

Chaque étape donne la commande, puis la ligne attendue (« Attendu »). Toutes les commandes se tapent dans PowerShell, à la racine du dépôt. Si la sortie ne ressemble pas à la ligne attendue, on s'arrête et on lit le §8.

---

## 0. La carte en une minute

Apprendre un driver, c'est générer un pilote de périphérique à partir de la datasheet du périphérique et d'un lot de cartes échantillons. Ici le périphérique est Word (ou PowerPoint), la datasheet est sa bibliothèque de types COM, et les cartes échantillons sont les documents du cabinet.

| Ici | En firmware |
|---|---|
| `vkforge` | La chaîne de production d'un firmware : six postes, chacun un banc séparé sous chien de garde, qui produit une image à partir de la datasheet et d'échantillons. |
| `inventory` | Lire la datasheet : la bibliothèque de types de l'application (`catalogue.json`), la table des registres. |
| `bind` | Associer la table des verbes de l'ontologie à ces registres (`binding-word.json`). Un verbe sans registre est `unbound`. |
| `mine` | Mesurer les échantillons : quelles valeurs tous les documents partagent (`invariants.json`, sans aucun texte). C'est de la lecture de zip, Office n'est jamais ouvert. |
| `plan` | Écrire la séquence d'initialisation : une action, ses étapes, ses pré- et postconditions (`plan.json`). |
| `generate` | Compiler l'image : `driver/driver.py` et `manifest.json`. |
| `qualify` | Le banc de test de l'image : le driver rejoué sur des copies du lot, chaque postcondition revérifiée dans le code. |
| `learned` (table du noyau) | Le dossier de fabrication de l'image : les hashes de tout ce dont elle est issue. |
| La porte du montage | La ROM de boot qui refuse une image sans rapport de test signé, ou dont le rapport n'est plus celui que l'usine a classé. |
| `.vault.json` | La mémoire de travail du mineur (le texte clair d'un pied de page), en RAM protégée : sous le répertoire privé du démon (`<état>\forge\<nom>\`), jamais dans le dossier `--out`. Après `plan`, elle ne garde que les valeurs dont le plan se sert : les candidats d'une ambiguïté purgée (dix pieds de page différents, un nom de client dans chacun) en sortent. Elle est effacée dès que `generate` rend la main. |
| `.salt` | Le sel de l'apprentissage : 32 octets tirés au sort par `learn`, posés à côté du coffre (`<état>\forge\<nom>\.salt`). Les jetons de `invariants.json` en dépendent : sans lui, on ne peut pas vérifier un pied de page deviné. Il survit à `generate`, parce que `qualify` en a besoin. |

---

## 1. Prérequis, une fois par nœud

**1.1 Office de bureau installé** (Word, et PowerPoint pour le §7). L'inventaire lit sa bibliothèque de types ; sans elle, `learn` s'arrête avec `remedy: install Microsoft 365 desktop`.

**1.2 Un CPython 3.13 non empaqueté.** Le Python du Microsoft Store ne convient pas (R27 : ses enfants échappent au Job du noyau). Voir le §1.1 du runbook `recette-maskai.md` pour `uv python install 3.13`.

**1.3 Le virtualenv de vkforge** (lxml, comtypes) :
```powershell
powershell -ExecutionPolicy Bypass -File drivers\vkforge\setup.ps1
```
Attendu, en dernière ligne : `VK_FORGE_PYTHON par défaut : C:\Users\<vous>\AppData\Local\vk\venvs\vkforge\Scripts\python.exe`. Avec un Python de python.org : ajouter `-Python <chemin>\python.exe`.

Chaque driver généré reçoit sa propre jonction `.venv` vers ce virtualenv. Le déplacer ou le recréer ailleurs casse les drivers déjà appris.

---

## 2. Démarrer le démon avec vkforge

Dans un terminal que l'on garde ouvert (le démon s'arrête avec lui, c'est voulu) :
```powershell
$env:PATH = "$env:CARGO_TARGET_DIR\debug;$env:PATH"
vkd --auto-enroll-node --vkforge-root "$PWD\drivers"
```
Attendu, dans les traces :
```
vkforge: vk driver learn and vk driver qualify are served
```
Sans `--vkforge-root`, `vk driver learn` et `vk driver qualify` sont refusés : `vkforge is not configured on this daemon; remedy: start vkd with --vkforge-root <repository>\drivers`. La racine est une option du démon, jamais d'une requête : aucun client du tube ne peut nommer le code que le démon lance.

**Ne jamais poser `VK_FORGE_FIXTURES=1` sur ce nœud.** C'est la prise de test de la CI (catalogues enregistrés, application factice à la place d'Office). Une qualification faite sous elle est marquée `fake true` : un démon relancé sans la prise refuse de monter ce driver (`was qualified on the fake application; remedy: vk driver qualify on the real one`), et il faut le requalifier sur le vrai Office. Si elle est posée, `vkd` le dit en clair : `VK_FORGE_FIXTURES=1: vk driver learn reads the recorded catalogues and vk driver qualify replays on the fake application, not Office`. Dans ce cas on arrête le démon et on relance sans.

Les actes humains (`learn`, `mount`, `umount`) demandent la clé d'appareil du nœud, inscrite par `--auto-enroll-node` (§4 du runbook `recette-maskai.md`) ; ils ne demandent pas de bail de vivacité.

---

## 3. Apprendre : `vk driver learn`

Le dossier du cabinet : au moins cinq documents `.docx` (ou `.pptx`, jamais les deux), d'un même genre, directement dans le dossier. Le dossier `--out` est nouveau et son nom devient celui du driver (1 à 32 caractères de `a-z`, `0-9`, `-`) ; il ne doit pas être dans le corpus.

```powershell
vk driver learn --family office-document --app word --corpus C:\cabinet\contrats --out C:\drivers-appris\word-maison
```
Options : `--threshold 0.8` (la part des documents qui doit porter une valeur pour qu'elle soit une règle ; par défaut 0,8) et `--arch NOM`. Sans `--arch`, aucun modèle n'est appelé : zéro token. Avec `--arch`, l'arch (qui doit pouvoir `Plan`) tranche entre les candidats d'une ambiguïté (deux polices de corps à 0,7 et 0,3 d'appui, par exemple), une question par ambiguïté, et son choix est écrit dans le plan avec `resolved_by: arch:<id>`.

Attendu, cinq lignes (les valeurs de l'exemple varient avec le corpus et la version d'Office) :
```
learned word-maison  sha256:…
plan sha256:… -> C:\drivers-appris\word-maison\plan.json (read it: vk driver plan show C:\drivers-appris\word-maison)
unbound: set_slide_layout
ambiguities: none
excluded: none
```
- `unbound` : les verbes de l'ontologie que cette application n'a pas (ici `set_slide_layout`, propre à PowerPoint, sur Word). Ils sont exclus du plan ; le §6 les propose à un arch. Un verbe `partial` (ici `set_placeholder_text`) est lié, mais pas entièrement : il n'est pas listé ici.
- `ambiguities` : `<invariant> purged` (aucun arch) ou `<invariant> arch:<id>`.
- `excluded` : ce que le plan a laissé de côté et pourquoi, par exemple `title_style (a custom style has no built-in anchor (v1))`.

Le grand-livre reçoit `driver.learned` ; le fichier `.vault.json` n'existe plus (il n'a jamais été dans `--out`) ; le sel `.salt` reste à côté, pour `qualify`.

Avec moins de dix documents, le plan demande autant de passes de qualification que de documents (`min_passes` = le nombre de documents, au plus dix) : un corpus de six documents se qualifie à 6/6.

---

## 4. Relire le plan : `vk driver plan show`

On relit avant de qualifier, comme on relit une séquence d'initialisation avant de flasher :
```powershell
vk driver plan show C:\drivers-appris\word-maison
```
Attendu : l'action (`<nom> (reversible: no)`), ses étapes numérotées (`1. open(path="$arg.document")`, `2. set_font_run(...)`, …), `pre:`, `post:`, puis les seuils que la porte du montage exigera (`qualification: min_passes 10, min_rate 1.0`), `ambiguities:` et `excluded:`. Une action `reversible: no` est normale : elle écrit dans le document.

---

## 5. Qualifier, puis monter

**5.1 Qualifier.** Le driver est rejoué sur des copies du corpus d'apprentissage (le même dossier, inchangé). Chaque copie est d'abord dégradée, pour que seule une vraie réparation fasse passer la postcondition. Le driver lance Word lui-même, ou s'attache à l'instance Word que vous avez déjà ouverte (d'où la réserve 3 du §8). Le Word qu'il lance, il le reconnaît à son processus (un seul nouveau WINWORD.EXE, lancé par le service DcomLaunch) et le tient dans un Job « tuer à la fermeture » : ce Word ne survit pas au driver, sauf l'exception bornée de la réserve 2.
```powershell
vk driver qualify --driver C:\drivers-appris\word-maison\driver --corpus C:\cabinet\contrats
```
Attendu :
```
qualified word-maison: 10/10 passes (0 failures), fake false -> C:\Users\<vous>\AppData\Local\vk\...\drivers\word-maison\work\qualify\qualification.json
```
`fake false` : le rejeu a eu lieu sur le vrai Office. `fake true` n'a de sens qu'en CI. Le noyau enregistre `driver.qualified` (le hash de `qualification.json`, les passes et les échecs). Un rejeu sous les seuils du plan est enregistré tout de même : c'est la porte du §5.2 qui le refuse.

Pendant le rejeu, ne touchez pas à Word et n'ouvrez pas les documents du corpus. Le driver ferme, sans jamais répondre, les boîtes de dialogue du Word qu'il a lancé et reconnu, et seulement celles-là (le §8 en dit les limites).

**5.2 Monter** (acte humain, comme tout montage de driver) :
```powershell
vk mount driver --manifest C:\drivers-appris\word-maison\driver\manifest.json
```
Attendu, une ligne de tableau : `word-maison  sha256:…  yes  development` (le nom, l'id, `GOVERNED`, la source). C'est la porte : pour un driver `kind: com`, le noyau refuse le montage sans qualification valide.

On monte depuis le dossier du `learn`, jamais depuis un paquet : `vk driver pack` refuse un driver appris dans cette version (`a learned driver is not packaged in this version; remedy: mount it from its learn directory`) ; l'étape de pack optionnelle du rapport de la Task 12 est caduque.

Un driver monté est un driver comme un autre : chaque `vk_driver_act` est un pas de tâche sous le harnais, supervisé, chaque acte approuvé. Seul le rejeu de `qualify` tourne hors harnais, comme enfant de son étage, dans le Job (c'est le seul écart de supervision, `contracts/tcb.md`).

---

## 6. Le repli : `vk driver learn --deliberate`

Pour un verbe `unbound`, on peut demander à un arch de **proposer** une règle de projection (le membre de la bibliothèque de types qui le réaliserait). Il propose, il n'applique rien : pas de trace de l'application, pas de code, pas de modification de l'ontologie.
```powershell
vk driver learn --deliberate set_slide_layout --family office-document --app word --out C:\drivers-appris\word-maison --arch NOM
```
`--out` est la sortie d'un apprentissage précédent (il doit contenir `catalogue.json` de cette application) ; `--arch` est obligatoire, et l'arch doit pouvoir `Generate` ; ni `--corpus` ni `--threshold` ne sont admis (`--deliberate takes no corpus or threshold; remedy: drop them`). Le verbe doit exister dans l'ontologie.

Attendu, trois lignes :
```
proposal for set_slide_layout on word by arch:sha256:… -> C:\drivers-appris\word-maison\proposals\set_slide_layout.json
rule: Slide.Layout / PpSlideLayout
nothing applied: a human versions a rule into the ontology
```
Si la réponse de l'arch ne contient pas d'objet `{"member": …, "constants": …}`, la deuxième ligne est `rule: none (read the answer in the file)` : on lit alors le champ `answer` du fichier. La décision est lue par un humain, qui versionne la règle dans `drivers/vkforge/ontology/office-document.json` si elle convient, puis réapprend (le hash de l'ontologie change, donc le driver aussi).

---

## 7. PowerPoint

Mêmes étapes avec `--app powerpoint` et un dossier de présentations `.pptx`. Rien ne change dans vkforge, seulement le catalogue, la liaison et le corpus. Trois points propres à PowerPoint :

- Le faux Word de la CI ne sait que le `.docx` : un driver PowerPoint se qualifie **contre le vrai PowerPoint, sur ce nœud**, jamais sur le faux.
- PowerPoint n'a qu'une instance. Dès la première action d'un driver PowerPoint appris, et jusqu'à son démontage, toute présentation que vous ouvrez rejoint l'instance du driver et partage son sort : rendue visible à une fin normale, dans les bornes de la réserve 2 du §8, fermée avec le driver sinon (un STOP, un délai dépassé, un démontage pendant une action), et ses boîtes de dialogue fermées comme celles du driver. On n'ouvre donc pas de présentation tant qu'un driver PowerPoint est monté.
- Le corpus doit avoir été **produit par PowerPoint lui-même**. Les `.pptx` générés des fixtures de test ne sont pas acceptés pour les changements de police : sur eux, les passes de qualification échouent (`postcondition`).

---

## 8. Quand ça coince

Toute étape qui refuse parle d'une ligne, `vkforge <étape>: <ce qui ne va pas>; remedy: <quoi faire>`, et cette ligne est le refus. Les journaux complets sont sous `%LOCALAPPDATA%\vk\...\forge\<nom>\` (état du démon). Ce dossier garde aussi `.salt`, la clé des jetons de `invariants.json` : on n'en transmet que les fichiers `forge-*.out` et `forge-*.err`, jamais le dossier entier.

| Ligne | Cause | Remède |
|---|---|---|
| `corpus has N files; remedy: more documents of one kind` | moins de cinq documents `.docx`/`.pptx` | ajouter des documents du même genre |
| `corpus mixes .docx and .pptx files; remedy: more documents of one kind` | les deux sortes dans un dossier | un dossier par sorte |
| `no invariant reaches the threshold 0.8 over N files; remedy: more documents of one kind` | rien de commun assez constant | plus de documents, ou `--threshold` plus bas |
| `out <dir> is not empty; remedy: a new directory` | `--out` déjà utilisé | un nouveau dossier (`learn` n'écrit jamais par-dessus) |
| `out <dir> is inside the corpus; remedy: a new directory outside it` | `--out` dans le corpus | ailleurs |
| `no invariant can be driven on word (excluded: …); remedy: read plan-excluded.json; vk driver learn --deliberate VERB proposes a binding` | tout est exclu (style personnalisé, verbe sans liaison) | lire `plan-excluded.json`, puis le §6 |
| `arch <id> cannot plan; remedy: an arch with the Plan capability` | `--arch` ne sait pas planifier | une autre arch |
| `arch <id> cannot draft; remedy: an arch with the Generate capability` | `--deliberate` : l'arch ne sait pas rédiger | une autre arch |
| `--deliberate needs --arch: a proposal is drafted by an arch; remedy: --arch NAME` | pas d'arch | donner `--arch` |
| `out <dir> holds no catalogue.json; remedy: --out names a learned driver's output` | `--deliberate` hors d'un apprentissage | le `--out` d'un `learn` |
| `corpus changed since vkforge mine; remedy: qualify on the corpus the driver was learned from, or learn again` | le corpus a bougé depuis le `learn` | rejouer sur le même dossier, ou réapprendre |
| `salt file <…>\.salt cannot be read (…); remedy: vk driver learn again: …` | le sel de l'apprentissage a disparu : répertoire `forge` vidé à la main, ou un `learn` du même nom arrêté avant `mine`, même interrompu (il vide `<état>\forge\<nom>\` dès son départ et ne tire son sel qu'au `mine`) ; ou le driver a été appris avant le sel | réapprendre (§3), puis requalifier ; ce refus a effacé la qualification précédente, et le montage refuse le driver jusque-là |
| `salt file <…>\.salt holds N bytes, not 32; remedy: vk driver learn again: …` | le fichier du sel a été tronqué ou remplacé : `learn` l'écrit avec exactement 32 octets | réapprendre (§3), puis requalifier ; ce refus a effacé la qualification précédente, et le montage refuse le driver jusque-là |
| `the salt does not give back the footer_text token of invariants.json; remedy: vk driver learn again: …` | le sel n'est plus celui de cet apprentissage : un autre `learn` du même nom l'a remplacé | réapprendre, puis requalifier ; ce refus a effacé la qualification précédente, et le montage refuse le driver jusque-là |
| `cannot perturb <id> for qualification` | une postcondition que la qualification ne sait pas dégrader | c'est un défaut de vkforge, pas du cabinet |
| l'action rend `dialog` | une boîte de dialogue a retenu l'appel (connexion, écrasement) ; dans le Word que le driver a lancé, elle est fermée, jamais acceptée ; dans le vôtre, elle reste ouverte (réserve 3) | traiter la cause (se connecter à Office, fermer les documents ouverts ; dans votre Word, répondre à la boîte), puis requalifier |
| `document opened read-only (locked elsewhere)` | une copie en lecture seule, ou un document tenu ouvert ailleurs | fermer le document ; une copie lecture seule reste lecture seule |
| `office version changed; remedy: vk driver learn again` | Office a changé de version : la bibliothèque de types n'est plus celle du catalogue | réapprendre (jamais monter sur l'ancienne liaison) |
| `driver <id> is not qualified; remedy: vk driver qualify --driver DIR --corpus DIR` | montage sans qualification enregistrée | §5.1 |
| `driver <id> is not qualified: N passes of M, the plan asks P at rate R; remedy: …` | rejeu sous les seuils | lire `qualification.json`, corriger, requalifier |
| `driver <id>: qualification.json changed since vk driver qualify; remedy: …` | le fichier de rapport a été modifié après le rejeu | requalifier |
| `driver <id> was qualified on the fake application; remedy: vk driver qualify on the real one` | le dernier rejeu a eu lieu sur le faux Word (`VK_FORGE_FIXTURES=1`), et ce démon tourne sans la prise | requalifier sur le vrai Office (§5.1), démon sans la prise |
| `a learned driver is not packaged in this version; remedy: mount it from its learn directory` | `vk driver pack` sur un driver appris | monter depuis le dossier du `learn` (§5.2) |
| `learn of <nom> already in progress; remedy: wait for it` (ou `qualify`, `deliberate`) | une seule demande à la fois par nom de driver | attendre sa fin |
| `the plan changed since vk driver learn; remedy: learn again` | `plan.json` modifié à la main | réapprendre ; les fichiers d'un driver appris ne s'éditent pas |

**Les cinq réserves de l'opérateur** (elles figurent aussi dans `contracts/tcb.md`) :

1. **Le démon ne meurt pas entre `mine` et `generate`.** S'il meurt, `<état>\forge\<nom>\.vault.json` reste sur le disque avec du texte clair, sous l'ACL privée du répertoire d'état (jamais dans `--out`) : avant ou pendant `plan` (avec `--arch`, chaque appel au modèle, un par ambiguïté, tombe dans cette fenêtre), il contient tous les candidats du mineur, par exemple le nom d'un client dans chacun de dix pieds de page ; après `plan`, seulement les valeurs que le plan garde. Ni la réduction que `plan` fait (le fichier est tronqué puis réécrit sur place, dans le répertoire privé), ni l'effacement qui suit `generate`, ne sont un effacement sécurisé : les octets écartés quittent le fichier mais peuvent rester dans les blocs libres du disque jusqu'à leur réutilisation. Le prochain `learn` du même nom vide ce répertoire ; sinon on le supprime à la main.
2. **Un rejeu tué à son délai n'abandonne plus le Word qu'il a lancé.** Le driver reconnaît le Word qu'il lance (un seul nouveau `WINWORD.EXE`, lancé par le service `DcomLaunch`) et le tient dans un Job « tuer à la fermeture » : tué, il l'emporte ; à une fin normale, il le ferme aussi. Si Windows refuse de le mettre dans le Job (`driver.log` : `job: process … not held`), le driver le ferme aussitôt, avant d'y ouvrir quoi que ce soit, et l'action refuse `precondition` : aucun Word du driver ne tourne hors d'un Job. Si le démarrage a dépassé son délai, ou si la fermeture échoue, le driver termine ce seul processus par son numéro, après avoir vérifié que ce numéro désigne toujours le Word qu'il a reconnu (même exécutable, même parent `DcomLaunch`, même heure de création) ; si cette vérification échoue ou ne peut pas se faire, il ne termine rien, et la même ligne de `driver.log` le dit (`not ended: …`) : on ferme alors ce Word à la main, sans enregistrer. S'il n'a pas pu le reconnaître (`start: treated as attached: …`), il le traite comme le vôtre et ce Word reste, même à une fin normale : on le ferme à la main, sans enregistrer. Un Word que vous aviez ouvert vous-même n'est jamais dans ce Job. Une seule exception, bornée (décision D-35) : à une fin normale, si le driver trouve dans son Word un document que vous y avez ouvert vous-même, en mode protégé compris, il ferme d'abord les siens sans enregistrer, vous rend ce Word visible et le laisse ouvert ; `driver.log` le dit (`quit: process N handed to the user, K foreign document(s) open in it; its Job disarmed`), sans nom de document. Le driver en juge par le nom : un document que vous rouvrez après une action compte comme le sien, et part avec ce Word. Au moindre doute, ou si le driver meurt, ce Word part avec votre document. Un driver appris avant cette version garde l'ancien adaptateur, sans Job, jusqu'à ce qu'on le réapprenne (§3).
3. **Les boîtes de dialogue de votre propre Word ne sont jamais fermées.** Le driver ne ferme que celles du Word qu'il a lancé et reconnu. Attaché à votre Word, il refuse `dialog` et laisse la boîte ouverte : on y répond soi-même, puis on requalifie. Une boîte qui s'ouvre dans le Word du driver est fermée, elle, même quand elle concerne un document à vous qui s'y trouve : un document ouvert d'un double-clic peut atterrir dans le Word du driver (réserve 2).
4. **L'appel de modèle d'une ambiguïté tient le verrou du noyau**, comme `task.step`. Un arch lent suspend les autres appels du démon pendant sa réponse.
5. **Le noyau ne contrôle aucune capacité à l'`infer`** : seules les vérifications de `learn` (l'arch sait `Plan`) et de `--deliberate` (l'arch sait `Generate`) font barrière, avant tout étage.
