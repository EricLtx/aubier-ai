# Procédure Aubier — recette fonctionnelle et benchmark d'un chat IA

Version 2, 2026-10-13. Issue de deux exécutions réelles sur MaskAI (`app.maskai.fr`), les
12 et 13 octobre 2026. Cette procédure est faite pour être exécutée par un agent et apprise
par Aubier : chaque étape a une entrée, une sortie, une preuve et, quand il le faut, un
point de validation humaine. Les outils cités sont dans ce dépôt (`scripts/recette/`,
`docs/functional/use-cases/recettage-chat-ia/`). La compétence agent correspondante est
`.claude/skills/recettage-chat-ia/SKILL.md`.

## 1. Pourquoi ce cas d'usage démontre la valeur d'Aubier

Une recette de chat IA est longue, répétitive et doit être refaite à chaque version. Elle
exige pourtant du jugement : savoir qu'un IBAN restitué avec un « C » de trop est un défaut
grave, ou qu'un « je ne vois pas de document » est un échec et pas une réponse polie. C'est le
profil exact d'un processus vertical Aubier :

- **Le savoir-faire devient du capital.** Grille, corpus, oracles, scripts d'instrumentation
  et journal forment un paquet portable (couche L3 « Capital ») qui survit au changement de
  modèle et se réutilise d'un produit à l'autre.
- **La qualification est rejouable.** Les oracles permettent de rejouer la même recette sur
  la version suivante et de ne rapporter que le delta (couche L4 « Qualification »).
- **Le registre fait foi.** Chaque mesure est un événement daté dans un journal append-only ;
  le rapport se reconstruit depuis le journal, jamais depuis la mémoire de l'agent.
- **L'humain valide ce qui engage.** L'agent exécute et propose des verdicts ; un humain
  valide les KO de sécurité et la publication. Ces validations sont les étiquettes dont la
  qualification a besoin.

## 2. Les six exigences non fonctionnelles

Chaque test est rattaché à une ou plusieurs ENF. Le rapport donne un score par ENF.

| ENF | Question | Ce qu'on mesure |
|---|---|---|
| Performance | Combien de temps l'utilisateur attend-il ? | chargement, délai au premier texte, réponse complète, part de chaque étape serveur, coût par kilo-octet |
| Véracité | Les réponses et les données restituées sont-elles exactes ? | valeurs attendues, pièges, restitution fidèle (`restituer(masquer(x)) == x`), cohérence des compteurs |
| Disponibilité | Le service répond-il, et échoue-t-il proprement ? | erreurs, relances, échecs silencieux, délais d'abandon, limites de traitements simultanés |
| Sécurité | Les données sensibles et le système sont-ils protégés ? | inventaire masqué vs attendu, fuites, injections, messages d'erreur internes, codes HTTP |
| Conformité | Les promesses et les obligations sont-elles tenues ? | catégories spéciales (santé, syndicat, handicap), minimisation, transparence, vitrine vs réalité |
| Scalabilité | Que se passe-t-il quand le volume monte ? | documents longs, tableaux de milliers de lignes, croissance de la latence, traitements concurrents |

## 3. La grille de niveaux (N0–N9)

| Niveau | Question | Cas types | ENF |
|---|---|---|---|
| N0 Socle | La page se charge-t-elle vite ? | TTFB, FCP, poids, thème, réglages | performance |
| N1 Conversation | Peut-on dialoguer sans accroc ? | envoi, rendu, envoi vide, arrêt, erreur + relance, streaming | disponibilité |
| N2 Multi-tours | Le contexte tient-il ? | suivi, cohérence, tour perdu après erreur | véracité |
| N3 Pertinence | Les réponses sont-elles justes ? | factuel, calcul, piège, consigne, langue | véracité |
| N4 Outils | Les outils promis marchent-ils ? | web, pièces jointes par format, OCR, document long, génération | disponibilité, scalabilité |
| N5 Multi-modèles | Le choix de modèle est-il réel ? | disponibles vs promis, même prompt par modèle | conformité, performance |
| N6 Confidentialité | Les données sont-elles protégées sans être abîmées ? | inventaire, typage, empans, déterminisme, catégories spéciales, formats inhabituels, structuré (JSON/CSV) | sécurité, conformité, véracité |
| N7 Organisation | Retrouve-t-on son travail ? | historique, recherche, renommage, export, langue | — |
| N8 Robustesse | Que se passe-t-il quand ça va mal ? | injections, erreurs, codes HTTP, charge concurrente, délais | sécurité, disponibilité |
| N9 Transparence | Les promesses sont-elles tenues et traçables ? | vitrine vs réalité, garde-fous, ce qui part au modèle | conformité |

## 4. Entrées

| Entrée | Qui la fournit |
|---|---|
| URL de l'application et session ouverte sur un compte de test | humain (jamais l'agent) |
| Promesses du produit (vitrine, documentation) | agent, en lecture |
| Corpus + oracles (`corpus/`, `corpus-v2/`) | ce dépôt ; régénérable avec les scripts |
| Niveaux et ENF à couvrir | humain (par défaut : tout) |
| Budget d'agent et de quota produit | humain |

## 5. Règles de conduite de l'agent

Données fictives uniquement. Pas de création de compte ni de mot de passe. Ne rien supprimer
qui existait avant ; lister ce qui a été créé. Pas de message, d'achat ni de retour au nom de
l'utilisateur. Tout réglage modifié est remis. Un défaut de sécurité est signalé tout de
suite et le rapport reste privé tant qu'il n'est pas corrigé.

## 6. Déroulé en sept étapes

1. **Reconnaissance (15 min).** Lire la vitrine, lister les promesses vérifiables. Relever
   l'arbre d'accessibilité de l'application : c'est la carte des fonctions. Vérifier les
   canaux (pièce jointe de 2 Ko, message court) avant de planifier.
2. **Instrumentation (10 min).** Mesures de chargement. Intercepteur du flux de réponse
   (annexe A). Console et réseau à blanc.
3. **Corpus (5 min si réutilisé, 45 min sinon).** `corpus/` (10 documents, 9 formats) et
   `corpus-v2/` (5 légers, 5 volumineux). Pour un autre métier, changer l'univers fictif dans
   les générateurs et réécrire les oracles.
4. **Exécution, niveau par niveau (1 h 30 à 3 h).** Une conversation neuve par cas. Pour
   chaque cas : horodater, joindre par le champ caché derrière le trombone, vérifier l'état
   de la pièce jointe, envoyer la question des oracles mot pour mot, relever réponse, texte
   envoyé au modèle, étapes serveur, réseau, console ; journaliser (`journal.py add`).
   Rejouer deux fois les cas de masquage. Tester la charge concurrente exprès (deux
   traitements lourds en parallèle) et la journaliser comme test de disponibilité. Fixer un
   délai d'abandon par test (3 × le budget) et le journaliser comme KO disponibilité.
5. **Jugement (20 min).** `evaluer.py` sur les oracles, puis lecture humaine des cas
   PARTIEL. Classer : KO sécurité, KO bloquant, KO, PARTIEL, OK. Distinguer mesuré et déduit.
   **Validation humaine** des KO sécurité et du verdict global.
6. **Rapport (30 min).** Synthèse (verdict, défauts majeurs, score par ENF) ; méthode ;
   un chapitre par ENF ; corpus et pièces jointes ; recommandations P0/P1/P2 ; traçabilité
   (créé, modifié, non testé). `journal.py stats` fournit les agrégats. **Validation
   humaine** avant diffusion ; version publique expurgée (pas d'adresse interne, pas de
   compte).
7. **Rejeu (à chaque version).** Rejouer les KO et un échantillon d'OK ; comparer au journal
   précédent ; n'écrire que le delta.

## 7. Barème

| Score | Signification |
|---|---|
| 5 | niveau attendu d'un produit professionnel |
| 4 | défauts mineurs |
| 3 | utilisable avec précautions |
| 2 | défauts majeurs sur des fonctions promises |
| 1 | non utilisable sur ce critère |

Un KO sécurité plafonne « sécurité » à 2. Une fonction promise entièrement KO plafonne son
ENF à 2. Un échec silencieux plafonne « disponibilité » à 2.

## 8. Ce qu'Aubier a appris (deux exécutions)

| Situation | Règle |
|---|---|
| Script dans la page coupé à 45 s | tranches < 36 s ; horodatage posé avant l'envoi |
| Sélecteur de fichiers natif non pilotable | `input[type=file]` caché derrière le trombone |
| Long texte à saisir | setter natif du `textarea` + événement `input`, puis un espace |
| Accents via le shell Windows | base64 encodé hors page, décodé dans la page |
| Pièce jointe en échec avant envoi | lire l'état de la puce avant d'envoyer |
| Compteurs contradictoires | recouper panneau, indicateur, événements serveur |
| Défaut de masquage invisible dans la réponse | relever le texte envoyé ; chercher masques collés et jetons cassés (`evaluer.py` le fait) |
| Même test, deux résultats | rejouer deux fois |
| Réponse polie qui cache un échec | « je ne vois pas de document » = KO |
| Attente qui ne vient pas du modèle | horodater chaque étape serveur |
| Deux traitements lourds en parallèle | tester la concurrence exprès, comme test de disponibilité |
| Traitement qui ne finit jamais | délai d'abandon = 3 × budget, KO disponibilité |
| Tour perdu après un échec | poser une question de suivi pour vérifier que le contexte réel suit l'historique affiché |
| Un seul traitement de fichier à la fois par compte | sérialiser les jobs lourds, ne pas conclure « détecteur en panne » trop vite |

## Annexe A — Intercepteur du flux de réponse

À exécuter dans la page avant les tests. Il horodate chaque ligne du flux SSE
(`/api/chat/stream` chez MaskAI ; adapter le motif) dans `window.__ev`.

```js
window.__ev = [];
if (!window.__wrapped) {
  const original = window.fetch;
  window.fetch = async (...args) => {
    const url = (args[0] && args[0].url) || String(args[0]);
    const t0 = Date.now();
    const response = await original(...args);
    if (/chat\/stream/.test(url) && response.body) {
      const [forApp, forUs] = response.body.tee();
      (async () => {
        try {
          const reader = forUs.getReader();
          const decoder = new TextDecoder();
          for (;;) {
            const { done, value } = await reader.read();
            if (done) { window.__ev.push([Date.now() - t0, '<END>']); break; }
            for (const line of decoder.decode(value, { stream: true }).split('\n')) {
              if (line.trim()) window.__ev.push([Date.now() - t0, line.slice(0, 160)]);
            }
          }
        } catch (e) { window.__ev.push([Date.now() - t0, '<ABORT> ' + e.name]); }
      })();
      return new Response(forApp, { status: response.status, statusText: response.statusText, headers: response.headers });
    }
    return response;
  };
  window.__wrapped = true;
}
```

Lecture : `window.__ev.filter(([t, l]) => /"node"/.test(l)).map(([t, l]) => t + ' ms ' + l)`.
Un `<ABORT>` sans `<END>` signe une coupure réseau : vérifier si l'interface l'a signalée.

## Annexe B — Renseigner la zone de saisie par programme

```js
const ta = document.querySelector('textarea');
const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
setter.call(ta, texte);           // texte = decodeURIComponent(escape(atob(b64))) si besoin
ta.dispatchEvent(new Event('input', { bubbles: true }));
```

Puis taper un espace au clavier pour que l'éditeur prenne la valeur en compte.

## Annexe C — Format du journal

Une ligne JSON par événement : `ts`, `step` (reconnaissance, instrumentation, corpus, exec,
jugement, rapport, rejeu, correction), `case`, `verdict` (OK, PARTIEL, KO, NON TESTÉ),
`timing_s`, `pipeline_ms` (étape → ms), `nfr` (liste), `evidence`. Une correction est une
nouvelle ligne avec `supersedes` = index de la ligne corrigée ; on n'édite jamais.
Les lignes écrites par le driver `maskai-recette` portent aussi `inputs_sha256`, l'empreinte des entrées du verdict (évaluateur, journal, cas, oracles, corpus).
