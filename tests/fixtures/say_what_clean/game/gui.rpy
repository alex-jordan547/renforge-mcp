## Clean fixture for #81 say.what style position live tests
## No @gui.variant overrides → should unlock style_gui_dialogue position_mode

init -1 python:
    gui.init(1280, 720)

define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)
