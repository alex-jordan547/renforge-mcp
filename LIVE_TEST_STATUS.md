# Live Test Status for #81

## État: BLOQUÉ (diagnostic en cours)

### Ce qui fonctionne ✓

1. **Fixture bootable**: `tests/fixtures/say_what_clean` boot proprement sous Ren'Py 8.5.3
   - Copié `guisupport.rpy` depuis demo → `gui.scale()` disponible
   - Ajouté `style "say_dialogue"` explicite sur `text what id "what"`
   - Pas de `@gui.variant small()` override sur `gui.dialogue_xpos/ypos`

2. **Ownership proof hors-ligne** (via `debug_ownership.py`):
   ```
   ✓ Style binding proven (screens.rpy)
   ✓ GUI vars found: xpos=268, ypos=50
   ✓ position_mode = "style_gui_dialogue"
   ✓ No lock codes
   ```

3. **Runner API corrigé**:
   - `renpy.show_screen()` au lieu de `jump_out_of_context` (pas de JumpOutException)
   - `client.scene_tree()` pour bounds (pas `get_scene_tree`)
   - `client.request("editor_task0_status")` au lieu de `eval_expr(_renforge_editor_task0_status())`
   - `_renforge_editor_select(x,y)` au lieu de `_select_target`

### Blocker actuel ❌

**Symptôme**: `position_mode: None` dans live test (attendu: `style_gui_dialogue`)

**Cause diagnostiquée**: Ordre d'opérations editor/say screen incompatible

Le harness actuel:
```python
1. Launch Ren'Py + inject editor
2. Click RF button → ouvre editor overlay
3. Runner: renpy.show_screen("say", ...) → montre dialogue
4. Runner: client.scene_tree() → cherche widget "what"
5. Runner: _renforge_editor_select(x,y)
```

**Problème**: Quand editor overlay est ouvert (#2), le say screen montré après (#3) n'est pas analysé par le coordinator.

**Tentatives**:
- ✗ Appeler `editor_task0_start({"screen": "say"})` → cache le say screen, widget "what" invisible
- ✗ Montrer say screen avant d'ouvrir editor → pas testé car harness ouvre editor avant scenario

### Options pour débloquer

**Option A**: Montrer say screen AVANT d'ouvrir editor
- Modifier `run_say_what_live_test.py` pour `renpy.show_screen("say")` avant `click RF`
- Coordinator analyserait say à la sélection (screen déjà présent)

**Option B**: Ne PAS ouvrir editor overlay, utiliser direct select API
- Pas de click RF, juste appeler `_renforge_editor_select()` directement
- Mais violera "product path" requirement (pas de visual UI)

**Option C**: Montrer say screen dynamiquement après editor ouvert
- Dans runner, après confirmation editor ouvert
- Besoin de trigger refresh/analysis du coordinator

**Recommandation**: Option A (reorder harness)

### Commits récents

- `93e7ccd`: Fix live fixture (guisupport + explicit style) + runner API fixes
- Pushed to PR #83 branch `cursor/say-what-style-position-bd4b`

### Prochaines étapes

1. Implémenter Option A dans `run_say_what_live_test.py`
2. Re-run live test
3. Si unlock réussit → continuer scénario complet (drag/preview/save/reload/undo)
4. Si encore bloqué → documenter hard blocker + laisser #81 ouvert

## Test Command

```bash
cd /workspace && RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 python3 run_say_what_live_test.py
```

## Debug Command

```bash
cd /workspace && python3 debug_ownership.py
```
