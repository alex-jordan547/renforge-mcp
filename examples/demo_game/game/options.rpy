define config.name = "RenForge Demo"

# This demo is intentionally minimal and ships without the full GUI
# (gui.rpy / screens.rpy). Ren'Py's default quit flow shows a yes/no
# confirmation that needs those screens, so quitting an incomplete project
# crashes with "'Layout' object has no attribute 'yesno_prompt'". Quitting
# without confirmation avoids that path and lets the game exit cleanly (e.g.
# when the bridge launcher terminates the process).
define config.quit_action = Quit(confirm=False)
