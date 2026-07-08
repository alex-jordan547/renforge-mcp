define narrator = Character(None)
define w = Character("Wisp", color="#7ec8ff")
define elder = Character("Elder Maren", color="#e8b86d")

default renforge_choice = ""
default courage = 0
default lantern = False

# Skip the main menu and drop straight into the story. This overrides Ren'Py's
# default main menu (see the `main_menu` label hook in 00start.rpy) so the demo
# — and the RenForge bridge that drives it — starts playing immediately. The
# save/load/preferences/quit screens are still reachable in-game via Escape.
label main_menu:
    jump start

label start:
    $ renforge_choice = ""
    $ courage = 0
    $ lantern = False
    play music "audio/missing_theme.ogg"
    scene bg village with dissolve
    narrator "The village of Emberfall sleeps under a bruised dawn sky."
    show wisp glow with dissolve
    w "You're awake! The Elder is waiting for you by the old gate."
    jump village_gate

label village_gate:
    elder "The mountain light has gone out, child. Someone must carry a new flame to the summit."
    menu:
        "Take the lantern and go.":
            $ lantern = True
            $ courage += 1
            elder "Brave heart. The forest path is shorter — the ridge is safer."
            jump crossroads
        "Ask the Elder to send someone else.":
            elder "There is no one else. But I will not force you."
            jump stay_home

label stay_home:
    scene bg village with dissolve
    narrator "You stay. The festival lights feel dimmer this year."
    w "Maybe next season, then."
    jump ending_home

label crossroads:
    scene bg forest with dissolve
    w "Two ways up: through the deep woods, or along the ridge."
    menu:
        "Cut through the deep woods.":
            $ renforge_choice = "forest"
            jump forest_path
        "Climb along the ridge.":
            $ renforge_choice = "ridge"
            jump ridge_path

label forest_path:
    scene bg forest with dissolve
    narrator "The canopy swallows the sky. Something hums between the trees."
    call wisp_advice
    menu:
        "Follow the humming light.":
            $ courage += 1
            jump hidden_shrine
        "Keep to the marked trail.":
            jump cave_mouth

label hidden_shrine:
    scene bg shrine with dissolve
    narrator "A forgotten shrine, warm as a heartbeat. The flame in your lantern turns silver."
    $ lantern = True
    w "The old fire remembers you."
    jump summit

label cave_mouth:
    scene bg cave with dissolve
    narrator "The trail ends at a yawning cave. Cold air breathes out of the dark."
    menu:
        "Enter the cave.":
            $ courage += 1
            jump cave_depths
        "Turn back to the crossroads.":
            jump crossroads

label cave_depths:
    scene bg cave with dissolve
    narrator "Your lantern paints the walls with moving gold. The passage climbs, and climbs."
    w "It's a shortcut after all. Almost there."
    jump summit

label ridge_path:
    scene bg summit with dissolve
    narrator "Wind claws at your coat. Far below, Emberfall is a handful of sparks."
    call wisp_advice
    menu:
        "Shield the lantern from the wind.":
            $ courage += 1
            jump summit
        "Grip the rocks with both hands.":
            $ lantern = False
            narrator "A gust snatches the flame. The lantern goes dark in your hand."
            jump summit

label wisp_advice:
    w "Whatever you meet up there — it's not the dark you should fear, it's forgetting why you climbed."
    return

label summit:
    scene bg summit with dissolve
    narrator "The beacon tower stands empty, its great bowl cold."
    if lantern:
        jump ending_light
    else:
        jump ending_ash

label ending_light:
    scene bg shrine with dissolve
    narrator "You raise the lantern. The beacon catches — a column of light answers from the valley."
    w "You did it. Emberfall will see morning after all."
    if courage >= 2:
        elder "And they will tell it as a brave tale, because it was one."
    jump credits

label ending_ash:
    scene bg cave with dissolve
    narrator "Without a flame, the bowl stays dark. You memorize the path for a second try."
    w "Next time, we take the lantern."
    jump credits

label ending_home:
    narrator "Some stories wait for another teller."
    jump credits

label credits:
    scene bg village with dissolve
    narrator "— RenForge demo — thanks for playing. —"
    return
