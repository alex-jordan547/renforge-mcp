# Améliorations MCP pour accélérer les agents

## Contexte

Ce document synthétise les frictions observées pendant une validation réelle
d'un projet Ren'Py avec RenForge MCP. La validation couvrait notamment le
rollback, la molette, le mode Auto, Quick Save, Quick Load et plusieurs variantes
de Skip, y compris les textes non vus, les écrans personnalisés et l'arrêt sur
un choix.

Le MCP a rempli son rôle principal : il a révélé un défaut d'interaction réel
qu'une vérification statique et les tests existants ne détectaient pas. Le
principal axe d'amélioration n'est donc pas la capacité fonctionnelle de
RenForge, mais le coût en temps, en appels MCP et en volume de contexte nécessaire
pour produire une preuve complète et déterministe.

## Objectif

Permettre à un agent de :

- lancer un jeu Ren'Py dans n'importe quel environnement courant ;
- atteindre un état de test déterministe ;
- cibler les contrôles par leur sens plutôt que par leurs coordonnées ;
- exécuter plusieurs interactions et assertions en un seul appel ;
- recevoir des résultats compacts et directement exploitables ;
- diagnostiquer automatiquement un échec d'interaction ;
- produire un rapport réutilisable dans une issue ou une pull request.

La cible mesurable proposée est de valider une matrice fonctionnelle comparable
à celle décrite ci-dessus en moins de cinq minutes, avec moins de dix appels MCP
et sans inspection manuelle des sauvegardes ou du store complet.

## Résumé des priorités

| Priorité | Amélioration | Effort estimé | Gain attendu |
| --- | --- | --- | --- |
| P0 | Sorties compactes par défaut | Faible | Très élevé |
| P0 | Lancement headless automatique | Moyen | Très élevé |
| P0 | Diagnostic de hit-testing | Moyen | Très élevé |
| P1 | Identifiants UI sémantiques | Moyen | Élevé |
| P1 | Sessions de test isolées | Moyen | Élevé |
| P1 | Scénarios batchés | Élevé | Très élevé |
| P2 | Événements métier structurés | Élevé | Élevé |

---

## 1. Rendre le lancement headless automatique et explicite

### Problème observé

Dans un environnement WSL sans session graphique classique, renforge_launch a
attendu environ soixante secondes avant d'échouer avec une erreur Python peu
actionnable :

    AttributeError: 'NoneType' object has no attribute 'update'

Cette erreur ne permet pas de distinguer un problème venant :

- du jeu ;
- de Ren'Py ;
- du bridge ;
- de l'absence de DISPLAY ;
- de l'audio SDL ;
- ou du processus de lancement RenForge.

Pour poursuivre la validation, il a fallu démarrer Ren'Py manuellement sous
Xvfb, configurer un pilote audio factice, injecter le bridge puis reprendre les
interactions avec le MCP.

### Comportement proposé

Le lancement devrait commencer par une détection explicite des capacités de
l'environnement :

    {
      "environment": "wsl",
      "display_available": false,
      "xvfb_available": true,
      "audio_available": false
    }

renforge_launch pourrait accepter une stratégie automatique :

    {
      "project_path": "/project",
      "display": "auto",
      "audio": "auto",
      "savedir": "temporary",
      "timeout": 15
    }

La valeur display: auto appliquerait la stratégie suivante :

1. utiliser la session graphique courante si elle est disponible ;
2. sinon démarrer automatiquement Xvfb ;
3. sinon utiliser un backend SDL headless compatible ;
4. sinon échouer rapidement avec un diagnostic précis.

La valeur audio: auto utiliserait SDL_AUDIODRIVER=dummy lorsqu'aucun
périphérique audio exploitable n'est disponible.

### Progression observable

Un lancement long ne devrait pas rester silencieux. Il devrait publier ou
retourner ses différentes phases :

    {
      "phase": "detecting_environment"
    }

    {
      "phase": "starting_virtual_display",
      "display": ":99"
    }

    {
      "phase": "starting_renpy",
      "pid": 12345
    }

    {
      "phase": "waiting_for_bridge",
      "port": 33859
    }

    {
      "ok": true,
      "ready": true,
      "display": ":99",
      "bridge_port": 33859,
      "startup_ms": 4280
    }

### Erreurs structurées

Les erreurs devraient posséder un code stable, la phase concernée et une
correction suggérée :

    {
      "ok": false,
      "code": "DISPLAY_UNAVAILABLE",
      "phase": "starting_renpy",
      "message": "Ren'Py requires a display and no virtual display could be started.",
      "suggested_fix": "Install xvfb or use display='external'."
    }

Autres codes utiles :

- RENPY_EXECUTABLE_NOT_FOUND ;
- DISPLAY_START_FAILED ;
- RENPY_PROCESS_EXITED ;
- BRIDGE_FILE_NOT_CREATED ;
- BRIDGE_CONNECTION_TIMEOUT ;
- AUDIO_INITIALIZATION_FAILED ;
- PROJECT_PATH_UNAVAILABLE.

### Nettoyage

renforge_stop devrait arrêter :

- le processus Ren'Py ;
- le processus Xvfb créé par RenForge ;
- le bridge injecté ;
- les fichiers de session temporaires ;
- le répertoire de sauvegarde temporaire, si demandé.

Le résultat devrait indiquer exactement ce qui a été nettoyé et signaler les
processus qui n'ont pas pu être arrêtés.

### Critères d'acceptation

- Un projet Ren'Py démarre sous WSL sans DISPLAY configuré.
- Le lancement nominal prend moins de quinze secondes.
- Une erreur indique précisément la phase et la cause.
- Le bridge est déclaré prêt seulement lorsqu'il accepte les commandes.
- Aucun processus Ren'Py ou Xvfb orphelin ne subsiste après renforge_stop.

---

## 2. Retourner des états compacts par défaut

### Problème observé

Une attente simple de l'écran choice a renvoyé presque tout le store Ren'Py :
constantes d'images, chemins audio, bases d'objets, statistiques, inventaire,
préférences et variables narratives. Le résultat utile tenait pourtant en
quelques champs :

    {
      "matched": "choice",
      "current_label": "show_thought",
      "config.skipping": null
    }

Les réponses trop volumineuses augmentent :

- la latence ;
- la consommation de tokens ;
- le risque de troncature ;
- le temps nécessaire à l'agent pour retrouver l'information utile ;
- la probabilité qu'un résultat important soit perdu au milieu du store.

### Profils de sortie proposés

Toutes les commandes live devraient accepter un profil commun :

- minimal : résultat de l'action et état strictement nécessaire ;
- interaction : label, écrans, dialogue, choix, Skip et Auto ;
- debug : pile Ren'Py et variables explicitement demandées ;
- full : store complet, uniquement sur demande explicite.

Exemple :

    {
      "screen": "choice",
      "timeout": 10,
      "state_profile": "minimal",
      "include": [
        "current_label",
        "config.skipping",
        "_preferences.skip_after_choices"
      ]
    }

Réponse attendue :

    {
      "ok": true,
      "matched": {
        "type": "screen",
        "value": "choice"
      },
      "elapsed_ms": 114,
      "state": {
        "current_label": "show_thought",
        "config.skipping": null,
        "_preferences.skip_after_choices": false
      }
    }

### Limites de sérialisation

Les outils devraient également accepter :

    {
      "max_depth": 3,
      "max_items": 50,
      "max_output_bytes": 8192
    }

Une valeur tronquée doit être explicitement signalée :

    {
      "variables": {
        "__truncated__": true,
        "__total_items__": 642
      }
    }

### Cohérence entre les outils

Le même format compact devrait être utilisé par :

- renforge_wait_until ;
- renforge_click_element ;
- renforge_click_at ;
- renforge_advance ;
- renforge_select_choice ;
- renforge_send_input ;
- renforge_control ;
- renforge_game_state_compact.

structuredContent devrait contenir les données stables destinées aux agents.
Le contenu textuel peut rester une synthèse lisible, mais ne devrait pas dupliquer
un store complet.

### Critères d'acceptation

- Une réponse courante fait moins de deux kilo-octets.
- Aucun store complet n'est renvoyé sans demande explicite.
- Les champs additionnels restent accessibles via include.
- Une troncature est toujours signalée.
- Tous les outils live utilisent le même schéma de résultat de base.

---

## 3. Exécuter un scénario complet en un appel

### Problème observé

Tester une seule exigence demande actuellement plusieurs appels :

1. obtenir les éléments UI ;
2. récupérer un frame_id ;
3. cliquer ;
4. attendre l'interaction suivante ;
5. évaluer une variable ;
6. capturer le nouvel état ;
7. vérifier manuellement le résultat.

Une matrice couvrant Back, Auto, Quick Save, Quick Load et les variantes de Skip
produit rapidement plusieurs dizaines d'allers-retours MCP.

### Outil proposé

Ajouter un outil renforge_run_scenario :

    {
      "name": "Skip stops at choices",
      "timeout": 15,
      "steps": [
        {
          "set": {
            "_preferences.skip_unseen": true,
            "_preferences.skip_after_choices": false,
            "config.skip_delay": 75
          }
        },
        {
          "click": {
            "target": "quick_menu.skip"
          }
        },
        {
          "wait": {
            "screen": "choice"
          }
        },
        {
          "assert": {
            "expr": "config.skipping is None",
            "message": "Skip must stop before the choice"
          }
        }
      ]
    }

### Résultat attendu

    {
      "ok": true,
      "scenario": "Skip stops at choices",
      "duration_ms": 1280,
      "steps": [
        {
          "index": 0,
          "status": "passed",
          "duration_ms": 4
        },
        {
          "index": 1,
          "status": "passed",
          "action": "Skip(fast=False)"
        },
        {
          "index": 2,
          "status": "passed",
          "matched": "screen:choice"
        },
        {
          "index": 3,
          "status": "passed",
          "actual": null
        }
      ]
    }

### Primitives recommandées

Le scénario doit rester volontairement limité à des opérations RenForge
prévisibles :

- set ;
- eval ;
- click ;
- click_at ;
- advance ;
- scroll ;
- wait ;
- assert ;
- select_choice ;
- capture ;
- save ;
- load.

L'objectif n'est pas de créer un second framework de test complet, mais de
réduire les allers-retours et de conserver un rapport commun.

### Diagnostic automatique en cas d'échec

Lorsqu'une étape échoue, RenForge devrait automatiquement collecter :

- une capture d'écran ;
- le label courant ;
- les écrans actifs ;
- le dialogue visible ;
- les choix disponibles ;
- les erreurs Ren'Py ;
- les derniers événements ;
- la valeur réelle de l'assertion ;
- l'élément qui a reçu le dernier clic.

Exemple :

    {
      "ok": false,
      "failed_step": 3,
      "expected": "config.skipping is None",
      "actual": "slow",
      "screenshot": "scenario-skip-failure.png",
      "current_screen": "choice",
      "last_action": "Skip(fast=False)"
    }

### Options d'exécution

Options utiles :

- timeout global et timeout par étape ;
- arrêt au premier échec par défaut ;
- poursuite optionnelle pour obtenir une matrice complète ;
- capture automatique uniquement en cas d'échec ;
- restauration d'un checkpoint entre deux variantes ;
- niveau de détail minimal, interaction ou debug.

### Critères d'acceptation

- Une matrice fonctionnelle complète nécessite moins de dix appels MCP.
- Chaque étape possède un timeout borné.
- Un échec conserve l'état avant toute action de récupération.
- Le rapport identifie précisément l'étape et la valeur incorrecte.
- Un scénario réussi produit une réponse compacte.

---

## 4. Donner des identifiants sémantiques aux éléments UI

### Problème observé

Certains contrôles ont dû être actionnés avec des coordonnées logiques. Cette
approche est fragile :

- un changement de résolution peut déplacer le bouton ;
- une animation peut modifier ses limites ;
- un autre écran peut recouvrir la zone ;
- le bouton peut être visible sans recevoir le clic ;
- un displayable transparent peut intercepter l'événement.

Dans la validation à l'origine de ce document, un écran personnalisé de pensée
recouvrait les contrôles du menu rapide et interceptait les clics. Le problème
n'était pas visible dans les tests statiques.

### Élément UI enrichi

renforge_list_ui_elements devrait exposer un identifiant stable et les
informations nécessaires au diagnostic :

    {
      "id": "quick_menu.q_save",
      "screen": "quick_menu",
      "type": "button",
      "action": "QuickSave",
      "bounds": {
        "x": 2350,
        "y": 1340,
        "width": 120,
        "height": 100
      },
      "zorder": 90,
      "enabled": true,
      "visible": true,
      "clickable": true,
      "covered": false
    }

Le clic devient alors :

    {
      "target": "quick_menu.q_save"
    }

### Sélecteurs alternatifs

Un élément pourrait être ciblé par :

- son identifiant explicite ;
- son écran et son action ;
- son écran, son type et son texte ;
- un chemin synthétique généré par RenForge.

Exemple :

    {
      "target": {
        "screen": "quick_menu",
        "action": "QuickSave"
      }
    }

Une cible ambiguë doit produire une erreur explicite avec les correspondances :

    {
      "ok": false,
      "code": "AMBIGUOUS_UI_TARGET",
      "matches": [
        "quick_menu.q_save",
        "touch_quick_menu.q_save"
      ]
    }

### Diagnostic de hit-testing

Ajouter renforge_hit_test pour inspecter la pile interactive à une coordonnée :

    {
      "x": 2411,
      "y": 1390
    }

Réponse :

    {
      "topmost": {
        "id": "thought_popup.dismiss_area",
        "screen": "thought_popup",
        "zorder": 100,
        "action": "Return"
      },
      "underneath": [
        {
          "id": "quick_menu.q_save",
          "screen": "quick_menu",
          "zorder": 90,
          "action": "QuickSave"
        }
      ],
      "warning": "The intended button is covered by another interactive displayable."
    }

renforge_click_element devrait également retourner l'élément ayant réellement
reçu l'événement, pas seulement la cible demandée.

### Coordonnées logiques et physiques

Les outils doivent distinguer clairement :

- les coordonnées logiques Ren'Py ;
- les coordonnées de la fenêtre ;
- les coordonnées physiques de la capture ;
- le facteur de mise à l'échelle.

Cette distinction évite les erreurs lors d'un changement de résolution, d'un
redimensionnement ou d'un affichage HiDPI.

### Origine des identifiants

Le système peut combiner :

- les id Ren'Py existants ;
- un attribut de test optionnel tel que mcp_id ;
- l'écran parent ;
- le type du displayable ;
- l'action native ;
- le texte ou l'image ;
- un index uniquement en dernier recours.

### Critères d'acceptation

- Les contrôles restent ciblables après un changement de résolution.
- Le MCP distingue les éléments homonymes de plusieurs écrans.
- Un clic indique l'élément qui a réellement reçu l'événement.
- Une superposition interactive est détectable.
- Les coordonnées logiques et physiques sont toujours différenciées.

---

## 5. Fournir des sessions de test isolées et déterministes

### Problème observé

Le comportement de Skip dépend notamment :

- des dialogues déjà vus ;
- du contenu de persistent ;
- des préférences laissées par une session précédente ;
- des sauvegardes existantes ;
- du point exact où le jeu est lancé ;
- de la vitesse de Skip ou Auto.

Un scénario peut donc passer ou échouer selon l'état local plutôt que selon le
code testé.

### Configuration de session proposée

renforge_launch devrait accepter :

    {
      "session": {
        "savedir": "temporary",
        "persistent": "empty",
        "preferences": "defaults",
        "random_seed": 42,
        "language": null,
        "cleanup_on_stop": true
      }
    }

Modes proposés pour persistent :

- empty : nouvel état persistant ;
- existing : état réel du joueur ;
- copy : copie isolée de l'état réel ;
- fixture : état prédéfini pour un scénario.

Exemple :

    {
      "session": {
        "savedir": "temporary",
        "persistent": {
          "fixture": "all-prologue-dialogue-seen"
        }
      }
    }

### Warp et checkpoints stables

Le warp ne devrait pas dépendre uniquement d'un numéro de ligne :

    {
      "warp": {
        "label": "prologue_classroom_choice",
        "fallback": {
          "file": "game/7SS_prologue.rpy",
          "line": 274
        }
      }
    }

Des checkpoints de test nommés seraient plus robustes que des lignes susceptibles
de changer à chaque modification du scénario.

### Réinitialisation en cours de session

Un outil de restauration rapide pourrait replacer le jeu dans un état connu :

    {
      "fixture": "issue_51_before_choice",
      "restart_interaction": true
    }

Cela permettrait de tester plusieurs variantes de préférences sans relancer
complètement Ren'Py.

### Protection des données utilisateur

Le mode de test isolé ne doit jamais :

- écraser une sauvegarde réelle ;
- modifier persistent sans demande explicite ;
- écrire dans le savedir par défaut ;
- laisser des fixtures après la fermeture de la session.

Le résultat de lancement devrait indiquer clairement les chemins utilisés.

### Critères d'acceptation

- Le même scénario produit le même résultat dix fois de suite.
- Aucun test isolé ne lit ou n'écrase les sauvegardes personnelles.
- Les préférences initiales sont connues et visibles.
- Le répertoire temporaire est supprimé automatiquement.
- Les fixtures peuvent déclarer les dialogues vus et non vus.

---

## 6. Exposer des événements métier Ren'Py structurés

### Problème observé

Plusieurs résultats ont dû être déduits indirectement :

- Quick Save en comparant la date de modification d'un fichier ;
- Quick Load en comparant le dialogue avant et après ;
- Back en comparant la longueur de l'historique ;
- Auto en observant la progression du dialogue ;
- Skip en lisant config.skipping.

Ces preuves sont valides, mais coûteuses et parfois ambiguës.

### Flux d'événements proposé

Le bridge pourrait exposer des événements normalisés.

Quick Save :

    {
      "event": "quick_save.completed",
      "timestamp": 1784030598.4,
      "slot": "quick-1",
      "path": "saves/quick-1-LT1.save",
      "correlation_id": "click-42"
    }

Quick Load :

    {
      "event": "quick_load.completed",
      "slot": "quick-1",
      "restored_label": "show_thought",
      "restored_dialogue": "..."
    }

Skip :

    {
      "event": "skip.stopped",
      "reason": "choice",
      "screen": "choice"
    }

Raisons utiles pour un arrêt de Skip :

- user_click ;
- unseen_dialogue ;
- choice ;
- transition ;
- end_of_context ;
- explicit_stop.

Auto :

    {
      "event": "auto.changed",
      "enabled": true
    }

    {
      "event": "auto.advanced",
      "from_interaction": 128,
      "to_interaction": 129,
      "delay_ms": 940
    }

Rollback :

    {
      "event": "rollback.completed",
      "from_history_index": 4,
      "to_history_index": 3
    }

### Corrélation avec les interactions

Chaque interaction MCP devrait recevoir un identifiant :

    {
      "interaction_id": "click-42",
      "target": "quick_menu.q_save"
    }

Tous les événements résultants incluraient cet identifiant. L'agent pourrait
alors attribuer avec certitude la sauvegarde ou le changement d'état au clic
qu'il vient d'effectuer.

### Attente de l'effet

renforge_click_element pourrait accepter :

    {
      "wait_for_effect": true,
      "effect_timeout": 5
    }

Et retourner :

    {
      "clicked": "quick_menu.q_save",
      "action": "QuickSave",
      "effect": {
        "event": "quick_save.completed",
        "slot": "quick-1"
      }
    }

### Critères d'acceptation

- Une action native importante produit un événement de succès ou d'échec.
- Les événements indiquent pourquoi Skip ou Auto s'est arrêté.
- Chaque événement peut être corrélé à l'interaction MCP d'origine.
- Les preuves nominales ne nécessitent plus d'inspection manuelle des fichiers.

---

## Architecture cible d'une validation

Une validation idéale pourrait commencer par un lancement unique :

    {
      "project_path": "/project",
      "display": "auto",
      "audio": "auto",
      "state_profile": "minimal",
      "session": {
        "savedir": "temporary",
        "persistent": "empty",
        "preferences": "defaults",
        "cleanup_on_stop": true
      },
      "warp": {
        "label": "test_quick_menu"
      }
    }

Puis exécuter une matrice :

    {
      "name": "Quick menu acceptance matrix",
      "steps": [
        {
          "click": {
            "target": "quick_menu.back"
          }
        },
        {
          "assert": {
            "event": "rollback.completed"
          }
        },
        {
          "click": {
            "target": "quick_menu.auto"
          }
        },
        {
          "assert": {
            "event": "auto.changed",
            "enabled": true
          }
        },
        {
          "click": {
            "target": "quick_menu.q_save"
          }
        },
        {
          "assert": {
            "event": "quick_save.completed"
          }
        }
      ]
    }

Enfin, RenForge produirait un rapport compact :

    {
      "ok": true,
      "scenario": "Quick menu acceptance matrix",
      "passed": 6,
      "failed": 0,
      "duration_ms": 4210,
      "artifacts": [
        "quick-menu-final.png"
      ]
    }

## Ordre d'implémentation recommandé

### Étape 1 : réduire immédiatement la latence et le contexte — FAIT

1. [x] Ajouter state_profile et include à renforge_wait_until.
2. [x] Appliquer des limites de sérialisation communes (`state_compact.py`).
3. [x] Profils partagés sur `game_state` / `game_state_compact` / `wait_until`.
   (`structuredContent` MCP dédié reste optionnel : les outils renvoient déjà
   des dicts JSON-serialisables compacts.)

### Étape 2 : fiabiliser le lancement — FAIT (noyau)

1. [x] Détecter WSL, DISPLAY, Xvfb et l'audio (`launch_env.py`).
2. [x] Introduire les phases et codes d'erreur structurés (`LaunchError`).
3. [x] Nettoyage process group Xvfb + artefacts bridge + savedir temporaire.
4. [ ] Backend SDL headless hors Xvfb (fallback ultime encore non branché).

### Étape 3 : rendre les interactions explicables — FAIT (noyau)

1. [x] Identifiants UI sémantiques (`screen.action`, `mcp_id`, covered/clickable).
2. [x] Ajouter renforge_hit_test.
3. [x] Retourner la cible réellement cliquée (`received_by`).
4. [x] Marquer `coordinate_space: logical` sur les éléments ; conversion
   screenshot→logical déjà présente pour click_at/hit_test.

### Étape 4 : rendre les tests reproductibles — PARTIEL

1. [x] savedirs temporaires (`savedir=temporary` + `RENFORGE_SAVEDIR`).
2. [x] Mode persistent `empty` (best-effort via session init).
3. [ ] Fixtures / checkpoints nommés et `random_seed` / language.

### Étape 5 : réduire les allers-retours — FAIT (noyau)

1. [x] Ajouter renforge_run_scenario.
2. [x] Collecter automatiquement les diagnostics à l'échec.
3. [x] Rapport compact (`passed` / `failed` / `duration_ms` / steps).

### Étape 6 : enrichir la preuve métier — FAIT (noyau)

1. [x] Normaliser les événements Quick Save, Quick Load, Skip, Auto et rollback.
2. [x] Ajouter les identifiants de corrélation (`interaction_id` / `correlation_id`).
3. [x] `wait_for_effect` sur `renforge_control` et `renforge_click_element`.

## Mesures de succès

Les améliorations peuvent être évaluées avec les indicateurs suivants :

- démarrage headless réussi en moins de quinze secondes ;
- réponse MCP nominale inférieure à deux kilo-octets ;
- absence de store complet dans les réponses par défaut ;
- zéro processus orphelin après cent cycles launch/stop ;
- même résultat sur dix exécutions d'un scénario isolé ;
- moins de dix appels MCP pour une matrice fonctionnelle complète ;
- diagnostic automatique d'un contrôle recouvert ;
- rapport final directement copiable dans une issue ou une pull request.

## Conclusion

RenForge dispose déjà des primitives nécessaires pour inspecter et piloter un
jeu réel. Les gains les plus importants viennent maintenant de l'orchestration
de ces primitives :

1. lancer automatiquement dans tous les environnements ;
2. retourner uniquement l'information utile ;
3. comprendre quel élément reçoit réellement une interaction ;
4. garantir un état de test reproductible ;
5. exécuter et rapporter une séquence complète en un seul appel.

Le trio à traiter en premier est le lancement headless automatique, les sorties
compactes et le diagnostic de hit-testing. Il réduirait déjà fortement le temps
nécessaire à un agent pour trouver et prouver un défaut réel.
