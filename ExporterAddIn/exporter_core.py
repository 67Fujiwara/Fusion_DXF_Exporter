"""Bulk exporter for Fusion: dialog + export loop.

Trimmed and reshaped from aconz2/Fusion360Exporter (public domain).  Differences:

* add-in friendly: no adsk.terminate(), handlers live in module lists the entry file clears
* f2d drawings export to PDF, DXF and DWG (DXF/DWG need the Sept 2026 Fusion API or newer)
* the project list is only fetched when it is actually needed (dialog opens instantly)
* file.versions (one server round trip per file) is never touched unless old versions were asked for
* documents can be opened hidden, which skips most of the UI work per file
* a document is only opened when at least one requested output is still missing
"""
import base64
import hashlib
import itertools
import json
import os
import re
import time
import traceback
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from functools import partial
from pathlib import Path
from typing import Dict, List, NamedTuple

import adsk.core
import adsk.drawing
import adsk.fusion

# Set True for a single run if you want the mtime of already exported files corrected.
update_existing_file_times = False

# '_' gives `name_v42.dxf`; Fusion's own manual export uses ' ' (`name v42.dxf`).
VERSION_SEPARATOR = '_'

log_file = None
log_fh = None

handlers = []          # the command-created handler; owned by ExporterAddIn.run()/stop()
dialog_handlers = []   # execute / inputChanged handlers of the dialog currently open

# `project` or `project/folder` as shown in the dropdown -> (project id, folder id or None)
project_folders_d = {}
projects_loaded = False
last_selected_projects = None

last_settings_path = Path(__file__).parent / 'last_settings.json'


def log(*args):
    if log_fh is None:
        return
    print(*args, file=log_fh)
    log_fh.flush()


def init_directory(name):
    directory = Path(name)
    directory.mkdir(exist_ok=True, parents=True)
    return directory


def init_logging(directory):
    global log_file, log_fh
    log_file = directory / '{:%Y_%m_%d_%H_%M}.txt'.format(datetime.now())
    log_fh = open(log_file, 'w', encoding='utf-8')


def load_last_settings():
    if not last_settings_path.exists():
        return {}
    try:
        with open(last_settings_path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_last_settings(d):
    with open(last_settings_path, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=2, ensure_ascii=False)


# f3d first: it is exported before anything gets unhidden so its thumbnail stays as designed
class Format(Enum):
    F3D = 'f3d'
    STEP = 'step'
    STL = 'stl'
    IGES = 'igs'
    SAT = 'sat'
    SMT = 'smt'
    TMF = '3mf'
    PDF = 'pdf'
    DXF = 'dxf'
    DWG = 'dwg'


FormatFromName = {x.value: x for x in Format}

DESIGN_FORMATS = (Format.F3D, Format.STEP, Format.STL, Format.IGES, Format.SAT, Format.SMT, Format.TMF)
DRAWING_FORMATS = (Format.PDF, Format.DXF, Format.DWG)

# name of the DrawingExportManager factory for each drawing format
DRAWING_EXPORT_METHODS = {
    Format.PDF: 'createPDFExportOptions',
    Format.DXF: 'createDXFExportOptions',
    Format.DWG: 'createDWGExportOptions',
}

DEFAULT_SELECTED_FORMATS = {Format.DXF.value}

archive_extensions = ['.zip', '.rar', '.gz', '.tar.gz', '.tar.bz2', '.tar.xz']


class Ctx(NamedTuple):
    app: adsk.core.Application
    folder: Path
    formats: List[Format]
    projects_folders: Dict[str, List[str]]  # {projectId: [folderId+]}; [] means the whole project
    use_active_folder: bool
    unhide_all: bool
    save_sketches: bool
    num_versions: int                       # -1 means all versions
    export_non_design_files: bool
    open_hidden: bool                       # open documents without showing them (faster)

    def extend(self, other):
        return self._replace(folder=self.folder / other)

    def to_dict(self):
        d = self._asdict()
        d.pop('app')
        d['folder'] = str(d['folder'])
        d['formats'] = [x.value for x in d['formats']]
        d['projects_folders'] = {k: list(v) for k, v in d['projects_folders'].items()}
        return d

    def dumps(self):
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d, app):
        d = dict(d)
        d['app'] = app
        d['folder'] = Path(d['folder'])
        d['formats'] = [FormatFromName[x] for x in d['formats']]
        d['projects_folders'] = {k: set(v) for k, v in d['projects_folders'].items()}
        d.setdefault('open_hidden', False)
        return cls(**d)


class LazyDocument:
    """Opens the Fusion document on first use only, so files whose outputs all exist cost nothing."""

    def __init__(self, ctx: Ctx, file: adsk.core.DataFile):
        self._ctx = ctx
        self._document = None
        self.file = file
        self.unhidden = False

    def open(self):
        if self._document is not None:
            return
        visible = not self._ctx.open_hidden
        log(f'Opening `{self.file.name}` v{self.file.versionNumber}' + ('' if visible else ' (hidden)'))
        self._document = self._ctx.app.documents.open(self.file, visible)
        if visible:
            self._document.activate()

    def unhide_all(self):
        if self.unhidden:
            return
        unhide_all_in_component(self.rootComponent)
        self.unhidden = True

    def close(self):
        if self._document is None:
            return
        log(f'Closing `{self.file.name}` v{self.file.versionNumber}')
        self._document.close(False)  # never save

    @property
    def design(self):
        return adsk.fusion.FusionDocument.cast(self._document).design

    @property
    def drawing(self):
        return adsk.drawing.DrawingDocument.cast(self._document).drawing

    @property
    def rootComponent(self):
        return self.design.rootComponent

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@dataclass
class Counter:
    saved: int = 0
    skipped: int = 0
    errored: int = 0

    def __add__(self, other):
        return Counter(self.saved + other.saved, self.skipped + other.skipped, self.errored + other.errored)

    def __iadd__(self, other):
        self.saved += other.saved
        self.skipped += other.skipped
        self.errored += other.errored
        return self


def unhide_all_in_component(component):
    component.isBodiesFolderLightBulbOn = True
    component.isSketchFolderLightBulbOn = True
    for brep in component.bRepBodies:
        brep.isLightBulbOn = True
    for body in component.meshBodies:
        body.isLightBulbOn = True
    for occurrence in component.occurrences:
        occurrence.isLightBulbOn = True
        unhide_all_in_component(occurrence.component)


def sanitize_filename(name: str) -> str:
    """Replace characters Windows refuses in file names; append a short hash so names stay unique."""
    with_replacement = re.sub(r'[:\\/*?<>|"]', ' ', name)
    if name == with_replacement:
        return name
    log(f'filename `{name}` contained bad chars, replacing by `{with_replacement}`')
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f'{with_replacement}_{digest}'


def set_mtime(path: Path, time_: int):
    os.utime(path, (time_, time_))


def output_path_exists(path: Path, file: adsk.core.DataFile) -> bool:
    if path.exists():
        if update_existing_file_times:
            set_mtime(path, file.dateModified)
            log(f'{path} already exists, but mtime was corrected')
        else:
            log(f'{path} already exists, skipping')
        return True
    for archive_extension in archive_extensions:
        if path.with_name(path.name + archive_extension).exists():
            log(f'{path} already exists as archive, skipping')
            return True
    return False


def export_sketch(ctx: Ctx, doc: LazyDocument, component, sketch) -> Counter:
    output_path = ctx.folder / f'{sanitize_filename(sketch.name)}.dxf'
    if output_path_exists(output_path, doc.file):
        return Counter(skipped=1)
    log(f'Exporting sketch {sketch.name} in {component.name} to {output_path}')
    output_path.parent.mkdir(exist_ok=True, parents=True)
    sketch.saveAsDXF(str(output_path))
    set_mtime(output_path, doc.file.dateModified)
    return Counter(saved=1)


def visit_sketches(ctx: Ctx, doc: LazyDocument, component) -> Counter:
    counter = Counter()
    for sketch in component.sketches:
        try:
            counter += export_sketch(ctx, doc, component, sketch)
        except Exception:
            log(traceback.format_exc())
            counter.errored += 1
    for occurrence in component.occurrences:
        counter += visit_sketches(ctx.extend(sanitize_filename(occurrence.name)), doc, occurrence.component)
    return counter


def tree_gen(folder) -> Path:
    """Relative path of the data folder's parents, e.g. A/B for a folder C inside B inside A."""
    names = []
    df = folder
    while df.parentFolder:
        names.append(sanitize_filename(df.parentFolder.name))
        df = df.parentFolder
    names.reverse()
    return Path(*names) if names else Path()


def export_filename(ctx: Ctx, file: adsk.core.DataFile, format: Format = None) -> Path:
    extension = file.fileExtension if format is None else format.value
    name = f'{sanitize_filename(file.name)}{VERSION_SEPARATOR}v{file.versionNumber}.{extension}'
    return ctx.folder / name


def export_file(ctx: Ctx, format: Format, doc: LazyDocument) -> Counter:
    """One f3d design -> one file of `format`."""
    output_path = export_filename(ctx, doc.file, format)
    if output_path_exists(output_path, doc.file):
        return Counter(skipped=1)

    doc.open()
    design = doc.design
    em = design.exportManager

    output_path.parent.mkdir(exist_ok=True, parents=True)
    output_path_s = str(output_path)

    if format == Format.F3D:
        options = em.createFusionArchiveExportOptions(output_path_s)
    elif format == Format.STL:
        options = em.createSTLExportOptions(design.rootComponent, output_path_s)
    elif format == Format.TMF:
        options = em.createC3MFExportOptions(design.rootComponent, output_path_s)
    elif format == Format.STEP:
        options = em.createSTEPExportOptions(output_path_s)
    elif format == Format.IGES:
        options = em.createIGESExportOptions(output_path_s)
    elif format == Format.SAT:
        options = em.createSATExportOptions(output_path_s)
    elif format == Format.SMT:
        options = em.createSMTExportOptions(output_path_s)
    else:
        raise Exception(f'Got unknown design export format {format}')

    # f3d keeps hidden things anyway and we want its thumbnail untouched, so unhide after it
    if ctx.unhide_all and format != Format.F3D:
        doc.unhide_all()

    em.execute(options)
    set_mtime(output_path, doc.file.dateModified)
    log(f'Saved {output_path}')

    if format == Format.F3D:
        thumb_b64 = design.rootComponent.createThumbnail(256, 256, 'PNG').getAsBase64String()
        with zipfile.ZipFile(output_path, 'a') as zf:
            with zf.open('FusionAssetName[Active]/Previews/small.png', 'w') as fh:
                fh.write(base64.b64decode(thumb_b64))

    return Counter(saved=1)


def export_drawing(ctx: Ctx, format: Format, doc: LazyDocument) -> Counter:
    """One f2d drawing -> PDF / DXF / DWG."""
    output_path = export_filename(ctx, doc.file, format)
    if output_path_exists(output_path, doc.file):
        return Counter(skipped=1)

    doc.open()
    em = doc.drawing.exportManager

    method = DRAWING_EXPORT_METHODS[format]
    create = getattr(em, method, None)
    if create is None:
        raise Exception(
            f'This Fusion build cannot export drawings as {format.value.upper()}: '
            f'DrawingExportManager.{method} is missing (added in the September 2026 API). '
            f'Update Fusion or untick {format.value} in Export Types.'
        )

    output_path.parent.mkdir(exist_ok=True, parents=True)
    em.execute(create(str(output_path)))
    set_mtime(output_path, doc.file.dateModified)
    log(f'Saved {output_path}')
    return Counter(saved=1)


def download_file(ctx: Ctx, file: adsk.core.DataFile) -> Counter:
    """Non-design files (pdf, xlsx, ...) are downloaded as they are, latest version only."""
    output_path = export_filename(ctx, file)
    if output_path_exists(output_path, file):
        return Counter(skipped=1)
    output_path.parent.mkdir(exist_ok=True, parents=True)
    file.download(str(output_path), None)
    set_mtime(output_path, file.dateModified)
    log(f'Saved {output_path}')
    return Counter(saved=1)


def visit_file(ctx: Ctx, file: adsk.core.DataFile) -> Counter:
    log(f'Visiting file {file.name} v{file.versionNumber}.{file.fileExtension}')
    counter = Counter()
    ext = file.fileExtension

    if ext not in ('f3d', 'f2d'):
        if not ctx.export_non_design_files:
            log(f'Skipping non-design file {file.name}.{ext}')
            counter.skipped += 1
            return counter
        try:
            counter += download_file(ctx, file)
        except Exception:
            counter.errored += 1
            log(traceback.format_exc())
        return counter

    wanted = DRAWING_FORMATS if ext == 'f2d' else DESIGN_FORMATS
    formats = [f for f in wanted if f in ctx.formats]

    if ext == 'f2d' and not formats:
        counter.skipped += 1
        return counter

    with LazyDocument(ctx, file) as doc:
        if ext == 'f3d' and ctx.save_sketches:
            doc.open()
            counter += visit_sketches(ctx.extend(sanitize_filename(doc.rootComponent.name)), doc, doc.rootComponent)

        export = export_drawing if ext == 'f2d' else export_file
        for format in formats:
            try:
                counter += export(ctx, format, doc)
            except Exception:
                counter.errored += 1
                log(traceback.format_exc())

    return counter


def file_versions(file: adsk.core.DataFile, num_versions):
    """Yield the current file, then up to num_versions older ones (-1 = all).

    file.versions is a server round trip per file, so it is not touched for the default
    num_versions == 0.  Fusion returns versions sorted as strings, hence the re-sort by int.
    """
    yield file
    if num_versions == 0:
        return

    versions = sorted(file.versions, key=lambda x: x.versionNumber, reverse=True)
    if versions[0].versionNumber != file.versionNumber:
        raise Exception(f'Expected versions[0] to be current file version, but got {versions[0].versionNumber}')

    versions = versions[1:] if num_versions == -1 else versions[1:num_versions + 1]

    prev = file.versionNumber
    for v in versions:
        if prev - v.versionNumber != 1:
            raise Exception(f'Versions not contiguous! prev={prev} cur={v.versionNumber}')
        yield v
        prev = v.versionNumber


def visit_folder(ctx: Ctx, folder, recurse=True) -> Counter:
    log(f'Visiting folder {folder.name}')
    new_ctx = ctx.extend(sanitize_filename(folder.name))
    counter = Counter()

    for file in folder.dataFiles:
        try:
            for file_version in file_versions(file, ctx.num_versions):
                counter += visit_file(new_ctx, file_version)
        except Exception:
            log(f'Got exception visiting file\n{traceback.format_exc()}')
            counter.errored += 1

    if recurse:
        for sub_folder in folder.dataFolders:
            counter += visit_folder(new_ctx, sub_folder)

    return counter


def main(ctx: Ctx) -> Counter:
    init_directory(ctx.folder)
    init_logging(ctx.folder)
    log(ctx.dumps())

    counter = Counter()

    if ctx.use_active_folder:
        root_folder = ctx.app.data.activeFolder
        counter += visit_folder(ctx.extend(tree_gen(root_folder)), root_folder)
    else:
        for project_id, folder_ids in ctx.projects_folders.items():
            project = ctx.app.data.dataProjects.itemById(project_id)
            if not folder_ids:
                counter += visit_folder(ctx, project.rootFolder)
            elif set(folder_ids) == {project.rootFolder.id}:
                counter += visit_folder(ctx, project.rootFolder, recurse=False)
            else:
                for folder in project.rootFolder.dataFolders:
                    if folder.id in folder_ids:
                        counter += visit_folder(ctx, folder)

    return counter


def run_main(ctx: Ctx):
    global log_fh
    ui = ctx.app.userInterface
    started = time.perf_counter()
    try:
        counter = main(ctx)
        elapsed = time.perf_counter() - started
        summary = '\n'.join((
            f'Saved {counter.saved} files',
            f'Skipped {counter.skipped} files',
            f'Encountered {counter.errored} errors',
            f'Took {elapsed:.1f} s',
            f'Log file is at {log_file}',
        ))
        log(summary)
        ui.messageBox(summary)
    except Exception:
        tb = traceback.format_exc()
        ui.messageBox(f'Log file is at {log_file}\n{tb}')
        log(f'Got top level exception\n{tb}')
    finally:
        if log_fh is not None:
            log_fh.close()
            log_fh = None


# ----------------------------------------------------------------------------- dialog

def message_box_traceback():
    adsk.core.Application.get().userInterface.messageBox(traceback.format_exc())


class I(StrEnum):
    """UI input ids (also the keys of last_settings.json)"""
    directory = 'directory'
    file_types = 'file_types'
    use_active_folder = 'use_active_folder'
    show_folders = 'show_folders'
    projects = 'projects'
    unhide_all = 'unhide_all'
    version_count = 'version_count'
    all_versions = 'all_versions'
    save_sketches = 'save_sketches'
    version_separator_is_space = 'version_separator_is_space'
    export_non_design_files = 'export_non_design_files'
    open_hidden = 'open_hidden'


def populate_data_projects_list(dropdown, show_folders=False, selected=None):
    """Fetch the project list from the hub.  This is the slow part of opening the dialog, so it only
    runs when 'Download Open Folder' is off (see CommandCreatedHandler / InputChangedHandler)."""
    global projects_loaded
    app = adsk.core.Application.get()
    dropdown.listItems.clear()
    project_folders_d.clear()
    selected = selected or []

    for project in app.data.dataProjects:
        if show_folders:
            for folder in itertools.chain([project.rootFolder], project.rootFolder.dataFolders):
                name = f'{project.name}/{folder.name}'
                project_folders_d[name] = (project.id, folder.id)
                dropdown.listItems.add(name, name in selected)
        else:
            project_folders_d[project.name] = (project.id, None)
            dropdown.listItems.add(project.name, project.name in selected)

    projects_loaded = True


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            changed = args.input
            if changed.id == I.all_versions:
                inputs.itemById(I.version_count).isEnabled = not changed.value
            elif changed.id == I.use_active_folder:
                use_active = changed.value
                inputs.itemById(I.projects).isEnabled = not use_active
                inputs.itemById(I.show_folders).isEnabled = not use_active
                if not use_active and not projects_loaded:
                    populate_data_projects_list(inputs.itemById(I.projects),
                                                inputs.itemById(I.show_folders).value,
                                                last_selected_projects)
            elif changed.id == I.show_folders:
                populate_data_projects_list(inputs.itemById(I.projects), changed.value, last_selected_projects)
        except Exception:
            message_box_traceback()


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global projects_loaded, last_selected_projects
        try:
            cmd = args.command
            cmd.isExecutedWhenPreEmpted = False

            on_execute = ExecuteHandler()
            on_input_changed = InputChangedHandler()
            cmd.execute.add(on_execute)
            cmd.inputChanged.add(on_input_changed)
            dialog_handlers[:] = [on_execute, on_input_changed]

            inputs = cmd.commandInputs
            last = load_last_settings()
            projects_loaded = False
            last_selected_projects = last.get(I.projects)

            export_folder = last.get(I.directory, str(Path.home() / 'Desktop' / 'Fusion360Export'))
            inputs.addStringValueInput(I.directory, 'Directory', export_folder)

            drop = inputs.addDropDownCommandInput(I.file_types, 'Export Types',
                                                  adsk.core.DropDownStyles.CheckBoxDropDownStyle)
            selected_formats = last.get(I.file_types, DEFAULT_SELECTED_FORMATS)
            for format in Format:
                drop.listItems.add(format.value, format.value in selected_formats)

            use_active_folder = last.get(I.use_active_folder, True)
            inputs.addBoolValueInput(I.use_active_folder, 'Download Open Folder', True, '', use_active_folder)

            show_folders = last.get(I.show_folders, False)
            inputs.addBoolValueInput(I.show_folders, 'Show Project Folders', True, '', show_folders)
            inputs.itemById(I.show_folders).isEnabled = not use_active_folder

            drop = inputs.addDropDownCommandInput(I.projects, 'Export Projects',
                                                  adsk.core.DropDownStyles.CheckBoxDropDownStyle)
            if not use_active_folder:
                populate_data_projects_list(drop, show_folders=show_folders, selected=last_selected_projects)
            drop.isEnabled = not use_active_folder

            inputs.addBoolValueInput(I.open_hidden, 'Open Documents Hidden (faster)', True, '',
                                     last.get(I.open_hidden, False))

            inputs.addBoolValueInput(I.unhide_all, 'Unhide All Bodies', True, '', last.get(I.unhide_all, True))

            versions_group = inputs.addGroupCommandInput('group_versions', 'Versions')
            versions_group.isExpanded = False
            versions_group.children.addIntegerSpinnerCommandInput(
                I.version_count, 'Number of Previous Versions', 0, 2 ** 16 - 1, 1, last.get(I.version_count, 0))
            all_versions = last.get(I.all_versions, False)
            versions_group.children.addBoolValueInput(I.all_versions, 'Save ALL Versions', True, '', all_versions)
            inputs.itemById(I.version_count).isEnabled = not all_versions

            inputs.addBoolValueInput(I.save_sketches, 'Save Sketches as DXF', True, '',
                                     last.get(I.save_sketches, False))
            inputs.addBoolValueInput(I.version_separator_is_space, 'Version Separator is Space', True, '',
                                     last.get(I.version_separator_is_space, VERSION_SEPARATOR == ' '))
            inputs.addBoolValueInput(I.export_non_design_files, 'Export Non-Design Files', True, '',
                                     last.get(I.export_non_design_files, False))
        except Exception:
            message_box_traceback()


def selected(list_items):
    # no generators and no copies of list items: swig wants to own them
    return [it.name for it in list_items if it.isSelected]


def make_projects_folders(inputs):
    ret = defaultdict(set)
    for it in inputs.itemById(I.projects).listItems:
        if it.isSelected:
            project_id, folder_id = project_folders_d[it.name]
            if folder_id is None:
                ret[project_id] = []
            else:
                ret[project_id].add(folder_id)
    return ret


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        global VERSION_SEPARATOR
        try:
            inputs = args.command.commandInputs
            iv = lambda name: inputs.itemById(name).value
            isel = lambda name: selected(inputs.itemById(name).listItems)

            save_last_settings({
                I.directory: iv(I.directory),
                I.file_types: isel(I.file_types),
                I.use_active_folder: iv(I.use_active_folder),
                I.show_folders: iv(I.show_folders),
                I.projects: isel(I.projects) if projects_loaded else (last_selected_projects or []),
                I.open_hidden: iv(I.open_hidden),
                I.unhide_all: iv(I.unhide_all),
                I.save_sketches: iv(I.save_sketches),
                I.version_count: iv(I.version_count),
                I.all_versions: iv(I.all_versions),
                I.version_separator_is_space: iv(I.version_separator_is_space),
                I.export_non_design_files: iv(I.export_non_design_files),
            })

            VERSION_SEPARATOR = ' ' if iv(I.version_separator_is_space) else '_'

            ctx = Ctx(
                app=adsk.core.Application.get(),
                folder=Path(iv(I.directory)),
                formats=[FormatFromName[x] for x in isel(I.file_types)],
                use_active_folder=iv(I.use_active_folder),
                projects_folders=make_projects_folders(inputs) if projects_loaded else {},
                unhide_all=iv(I.unhide_all),
                save_sketches=iv(I.save_sketches),
                num_versions=-1 if iv(I.all_versions) else iv(I.version_count),
                export_non_design_files=iv(I.export_non_design_files),
                open_hidden=iv(I.open_hidden),
            )
            run_main(ctx)
        except Exception:
            message_box_traceback()
