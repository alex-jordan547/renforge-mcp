init python:
    """
    Squelette de commande Ren'Py `renforge_dump`.

    Objectif de phase 1/2:
    - exposer une API de commande stable pour l'outil Ren'Py
    - laisser un point d'entrée qui pourra être remplacé par un vrai dump AST
    - être sûr même si cette commande n'est pas enregistrée dans cette version.
    """

    import json


    def renforge_dump():
        """
        Commande placeholder.
        Retourne un JSON minimal aujourd'hui, puis pourra retourner le vrai AST.
        """
        payload = {
            "status": "not_implemented",
            "message": "Probe stub active. Hooker ici l'export AST réel.",
        }
        return json.dumps(payload, ensure_ascii=False)


    def _safe_register():
        for obj_name in ("register_command", "register_python_command"):
            cmd_api = getattr(renpy, obj_name, None)
            if callable(cmd_api):
                try:
                    cmd_api("renforge_dump", renforge_dump)
                    return
                except Exception:
                    pass

    # En pratique le dépôt Ren'Py expose plusieurs formes de hook selon version.
    try:
        _safe_register()
    except Exception:
        pass

