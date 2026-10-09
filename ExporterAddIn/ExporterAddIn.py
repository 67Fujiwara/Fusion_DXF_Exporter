"""Fusion add-in entry point.

run()  : registers one toolbar button (UTILITIES > ADD-INS panel) and returns at once.
stop() : removes the button again.

All the real work lives in exporter_core.py; this file is only the add-in shell so that
"Run on Startup" can be ticked in the Scripts and Add-Ins dialog.
"""
import traceback

import adsk.core

from . import exporter_core as core

CMD_ID = 'panelStudio_FusionExporter'
PANEL_ID = 'SolidScriptsAddinsPanel'   # UTILITIES > ADD-INS panel

# While editing exporter_core.py, set True so each button press reloads it without
# toggling the add-in off and on.  Leave False for normal use (it costs a reload per click).
DEV_RELOAD = False

_ui = None


def run(context):
    global _ui
    app = adsk.core.Application.get()
    _ui = app.userInterface
    try:
        if DEV_RELOAD:
            import importlib
            importlib.reload(core)

        cmd_defs = _ui.commandDefinitions
        cmd_def = cmd_defs.itemById(CMD_ID)
        if cmd_def:                      # left over from a previous load; avoid doubled inputs
            cmd_def.deleteMe()
        cmd_def = cmd_defs.addButtonDefinition(
            CMD_ID,
            'Exporter',
            'f3d / f2d を STEP・PDF・DXF・DWG などに一括書き出し',
            './resources/exporter',
        )

        on_created = core.CommandCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        core.handlers.append(on_created)

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            old = panel.controls.itemById(CMD_ID)
            if old:
                old.deleteMe()
            panel.controls.addCommand(cmd_def)

        adsk.autoTerminate(False)        # keep the add-in resident after run() returns
    except Exception:
        _ui.messageBox('Exporter add-in failed to start:\n' + traceback.format_exc())


def stop(context):
    try:
        panel = _ui.allToolbarPanels.itemById(PANEL_ID) if _ui else None
        ctrl = panel.controls.itemById(CMD_ID) if panel else None
        if ctrl:
            ctrl.deleteMe()
        cmd_def = _ui.commandDefinitions.itemById(CMD_ID) if _ui else None
        if cmd_def:
            cmd_def.deleteMe()
        core.handlers.clear()
    except Exception:
        if _ui:
            _ui.messageBox('Exporter add-in failed to stop:\n' + traceback.format_exc())
