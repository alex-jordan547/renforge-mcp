# RenForge MCP

RenForge est un serveur [Model Context Protocol](https://modelcontextprotocol.io/)
pour les projets Ren'Py. Un agent peut lire le projet, lancer le jeu, observer
un écran, cliquer un contrôle et vérifier l'état runtime, sans avoir à deviner
la structure ou les coordonnées du jeu.

## Installation

Le serveur MCP utilise `stdio` et se lance avec :

```bash
uvx renforge serve
```

Avec une installation locale :

```bash
pipx install renforge
renforge serve
```

Pour développer RenForge :

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[ui,test]"
renforge serve
```

Le dashboard est un processus séparé :

```bash
renforge ui --project /chemin/vers/le-jeu
```

Quand le dashboard est actif, un `renforge_launch` demandé par un client MCP
est délégué au dashboard. C'est particulièrement utile sous WSLg ou lorsqu'un
client MCP n'a pas directement accès à l'affichage graphique.

## Configuration d'un client

La commande est la même dans tous les clients. Exemple de configuration JSON :

```json
{
  "mcpServers": {
    "renforge": {
      "command": "uvx",
      "args": ["renforge", "serve"]
    }
  }
}
```

Pour Codex CLI :

```bash
codex mcp add renforge -- uvx renforge serve
```

Ou dans `~/.codex/config.toml` :

```toml
[mcp_servers.renforge]
command = "uvx"
args = ["renforge", "serve"]
```

Pour Claude Code :

```bash
claude mcp add renforge -- uvx renforge serve
```

Tous les outils liés au projet prennent `project_path`. Il est préférable
d'appeler `renforge_info` au début : il donne `active_project`, c'est-à-dire le
projet sélectionné dans le dashboard, plutôt que de laisser l'agent deviner un
chemin.

## Workflow recommandé

```text
renforge_info
  -> active_project
renforge_launch(project_path)
  -> jeu et bridge disponibles
renforge_game_state_compact(project_path)
  -> label et état courant
renforge_list_ui_elements(project_path)
  -> contrôles visibles + frame_id
renforge_click_element(..., expected_frame_id=frame_id)
  -> interaction sûre
```

Pour une action par image :

```text
renforge_find_image_on_screen(project_path, template_path)
  -> bounds, center, coordinate_space="screenshot", frame_id
renforge_click_at(
  project_path,
  x=center.x,
  y=center.y,
  coordinate_space="screenshot",
  expected_frame_id=frame_id,
)
```

`frame_id` protège contre un clic sur un écran devenu obsolète. Si le jeu a
changé entre l'observation et le clic, RenForge renvoie une erreur de garde et
l'agent doit relire l'écran avant de réessayer.

Les bornes de `renforge_list_ui_elements` sont en coordonnées Ren'Py logiques.
Les bornes trouvées par `renforge_find_image_on_screen` sont en coordonnées du
PNG capturé ; il faut donc reprendre son `coordinate_space: "screenshot"` pour
`renforge_click_at`. RenForge convertit alors vers les coordonnées logiques,
y compris lorsque WSLg redimensionne la capture.

## Catalogue des outils

### Découverte et analyse statique

| Outil | Usage |
| --- | --- |
| `renforge_info` | Version, dashboard et projet actif. À appeler en premier. |
| `renforge_context` | Dashboard actif et projet Ren'Py sélectionné. |
| `renforge_inspect_project` | Résumé léger d'un projet Ren'Py. |
| `renforge_scan_project` | Scan des scripts, labels, liens et métadonnées. Utiliser les filtres et la pagination pour les gros projets. |
| `renforge_find_references` | Définitions et usages Ren'Py exacts, y compris interpolations texte. |
| `renforge_parse_lint` | Parse une sortie `renpy lint`. |
| `renforge_inspect_image` | Inspecte un fichier image, avec crop et zoom facultatifs. |

### Cycle de vie du jeu

| Outil | Usage |
| --- | --- |
| `renforge_launch` | Lance ou réutilise le jeu et injecte le bridge temporaire. `warp` accepte `fichier:ligne`. |
| `renforge_jump` | Redémarre le jeu sur un label ou un `fichier:ligne` via le warp Ren'Py. |
| `renforge_new_game` | Nouvelle partie depuis le label `start`. |
| `renforge_stop` | Arrête le jeu et retire le bridge injecté. |
| `renforge_game_state` | État complet, incluant les variables. |
| `renforge_game_state_compact` | État borné ; sélectionner les variables par nom ou préfixe. |
| `renforge_advance` | Avance le dialogue courant. |
| `renforge_screenshot` | Capture le jeu ; largeur, hauteur, crop et échelle sont facultatifs. |

### Choix et interface

| Outil | Usage |
| --- | --- |
| `renforge_list_choices` | Choix narratifs visibles. |
| `renforge_select_choice` | Choisit par texte, de préférence, ou index. |
| `renforge_list_ui_elements` | Contrôles focusables visibles : ID, texte, rôle, écran, bornes, centre, état et `frame_id`. |
| `renforge_click_element` | Clique un contrôle par ID ou texte. Accepte `exact`, `screen` et `expected_frame_id`. |
| `renforge_click_at` | Clique des coordonnées `logical` ou `screenshot`, avec gardes `expected_frame_id` et `expected_state`. |
| `renforge_find_image_on_screen` | Cherche un PNG local dans la capture courante et renvoie confiance, bornes, centre et garde de frame. |

### État et exécution contrôlée

| Outil | Usage |
| --- | --- |
| `renforge_eval` | Évalue une expression Python dans `store`. À réserver au diagnostic et au développement. |
| `renforge_get_var` | Lit une variable du store. |
| `renforge_set_var` | Écrit une variable du store. |
| `renforge_poll_events` | Lit les événements de labels, dialogues et exceptions depuis un curseur. |
| `renforge_autopilot` | Explore des branches et rapporte couverture des labels et crashs. |

### Projet, traductions, builds et documentation Ren'Py

| Outil | Usage |
| --- | --- |
| `renforge_assets` | Images et audios orphelins ou manquants. |
| `renforge_languages` | Langues présentes sous `game/tl/`. |
| `renforge_translation_stats` | Avancement et manques d'une langue. |
| `renforge_generate_translations` | Génère ou met à jour `game/tl/<langue>/`. Écrit dans le projet. |
| `renforge_export_dialogue` | Exporte les dialogues en texte. |
| `renforge_web_build` | Build navigateur ; requiert le DLC web du SDK. |
| `renforge_distribute` | Distribution desktop (`pc`, `mac`, `linux`, etc.). |
| `renforge_search_docs` | Recherche dans la documentation Ren'Py hors ligne. |
| `renforge_get_doc` | Lit une page de documentation Ren'Py. |
| `renforge_list_docs` | Liste les pages de documentation disponibles. |

## Écritures et précautions

Ces outils modifient l'état du jeu ou le projet : `renforge_launch`,
`renforge_jump`, `renforge_new_game`, `renforge_stop`, `renforge_click_*`,
`renforge_set_var`, `renforge_generate_translations`, `renforge_web_build` et
`renforge_distribute`.

Pratiques recommandées :

- préférer `renforge_game_state_compact` à l'état complet ;
- borner `renforge_scan_project` avec `file_glob`, `symbol`, `offset` et
  `limit` ;
- utiliser une copie ou une branche pour les traductions et builds ;
- lister l'UI avant de cliquer et toujours transmettre le `frame_id` ;
- après une erreur de garde, refaire une capture ou une liste plutôt que
  rejouer le même clic.

## Dépannage

| Symptôme | Cause probable et correction |
| --- | --- |
| `no running game` | Appeler `renforge_launch` ou démarrer le dashboard avec `renforge ui`. |
| Aucun `active_project` | Sélectionner le projet dans le dashboard ou fournir explicitement `project_path`. |
| Échec `expected_frame_id guard failed` | L'écran a changé ; relancer `renforge_list_ui_elements` ou `renforge_find_image_on_screen`. |
| Clic au mauvais endroit sous WSLg | Réutiliser `coordinate_space` renvoyé par la recherche visuelle. |
| Pas d'affichage depuis le client MCP | Démarrer `renforge ui --project …` : le lancement sera délégué au processus qui possède l'affichage. |
| Outil MCP absent | Mettre à jour RenForge puis redémarrer la session MCP afin de recharger le catalogue. |

## Vérification rapide

Après la configuration, demander à l'agent :

> Appelle `renforge_info`, inspecte mon projet Ren'Py, puis liste les contrôles
> visibles du jeu.

Une réponse correcte indique le projet actif, le résumé du projet et les
contrôles accompagnés de leurs bornes et d'un `frame_id`.
