define narrator = Character("Narrator")
define e = Character("Eileen")
default renforge_choice = ""

# Skip the main menu and drop straight into the story. This overrides Ren'Py's
# default main menu (see the `main_menu` label hook in 00start.rpy) so the demo
# — and the RenForge bridge that drives it — starts playing immediately. The
# save/load/preferences/quit screens are still reachable in-game via Escape.
label main_menu:
    jump start

label start:
    $ renforge_choice = ""
    narrator "Ren'Forge démarre. Ton script de démo est prêt."
    jump choice

label choice:
    e "Deux routes s'offrent à toi."

    menu:
        "Suivre la route lumineuse.":
            $ renforge_choice = "good"
            jump good

        "Suivre la route sombre.":
            $ renforge_choice = "bad"
            jump bad

label good:
    e "Tu as choisi la lumière."
    if renforge_choice == "good":
        narrator "Le joueur a bien sauvé sa variable de choix."
    return

label bad:
    e "Tu as choisi l'ombre."
    if renforge_choice == "bad":
        narrator "La variable de choix a bien été mise à jour."
    return
