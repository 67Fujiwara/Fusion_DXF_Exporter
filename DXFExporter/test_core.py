"""Plain-Python checks for DXFExporter (no Fusion needed):  python3 test_core.py

adsk is mocked, so this only covers the export loop (which files get opened, which export
methods get called, version handling), not the dialog.
"""
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent))
for name in ('adsk', 'adsk.core', 'adsk.fusion', 'adsk.drawing'):
    sys.modules[name] = Mock()

import DXFExporter as core  # noqa: E402

core.set_mtime = lambda path, t: None
core.application_gone = lambda: False
Path.mkdir = lambda *a, **k: None


class FakeExportManager:
    """Records create*/execute calls; only the listed factories exist."""

    def __init__(self, factories):
        self.created = []
        self.executed = []
        for f in factories:
            setattr(self, f, lambda path, *_rest, _f=f: self.created.append((_f, path)) or (_f, path))

    def execute(self, options):
        self.executed.append(options)


@dataclass
class FakeFile:
    name: str
    fileExtension: str
    versionNumber: int = 1
    dateModified: int = 0
    versions_list: list = field(default_factory=list)

    @property
    def versions(self):
        if self.versions_list is None:
            raise AssertionError('file.versions must not be touched')
        return self.versions_list


@dataclass
class FakeFolder:
    name: str
    dataFiles: List[FakeFile]
    dataFolders: list = field(default_factory=list)
    id: str = ''
    parentFolder: object = None


class FakeDocument:
    def __init__(self, file):
        self.file = file
        self.activated = False
        self.closed = False

    def activate(self):
        self.activated = True

    def close(self, save):
        self.closed = True


class FakeDocuments:
    def __init__(self):
        self.opened = []   # (file, visible)

    def open(self, file, visible=True):
        self.opened.append((file, visible))
        return FakeDocument(file)


def make_ctx(formats, **kw):
    app = SimpleNamespace(documents=FakeDocuments())
    args = dict(app=app, folder=Path('/out'), formats=formats, use_active_folder=True, project_id='', folder_ids=[],
                unhide_all=False, save_sketches=False, num_versions=0, export_non_design_files=False,
                include_subfolders=True)
    args.update(kw)
    return core.Ctx(**args)


class ExportLoop(unittest.TestCase):
    def setUp(self):
        self.existing = set()
        core.output_path_exists = lambda path, file: str(path) in self.existing
        self.drawing_em = FakeExportManager(['createPDFExportOptions', 'createDXFExportOptions'])
        self.design_em = FakeExportManager(['createSTEPExportOptions', 'createFusionArchiveExportOptions'])
        root = SimpleNamespace(name='root', sketches=[], occurrences=[], bRepBodies=[], meshBodies=[])
        core.adsk.drawing.DrawingDocument.cast = lambda d: SimpleNamespace(
            drawing=SimpleNamespace(exportManager=self.drawing_em))
        core.adsk.fusion.FusionDocument.cast = lambda d: SimpleNamespace(
            design=SimpleNamespace(exportManager=self.design_em, rootComponent=root))

    def test_f2d_exports_dxf_and_pdf_opening_once(self):
        ctx = make_ctx([core.Format.DXF, core.Format.PDF])
        folder = FakeFolder('proj', [FakeFile('plate', 'f2d', versions_list=None)])
        c = core.visit_folder(ctx, folder)
        self.assertEqual((c.saved, c.skipped, c.errored), (2, 0, 0))
        self.assertEqual(len(ctx.app.documents.opened), 1)
        self.assertEqual([f for f, _ in self.drawing_em.created],
                         ['createPDFExportOptions', 'createDXFExportOptions'])
        self.assertEqual(self.drawing_em.created[1][1], str(Path('/out/proj/plate_v1.dxf')))
        self.assertEqual(len(self.drawing_em.executed), 2)

    def test_dwg_missing_api_is_reported_not_fatal(self):
        ctx = make_ctx([core.Format.DWG, core.Format.DXF])
        folder = FakeFolder('proj', [FakeFile('plate', 'f2d')])
        c = core.visit_folder(ctx, folder)
        self.assertEqual((c.saved, c.errored), (1, 1))

    def test_existing_outputs_never_open_document(self):
        ctx = make_ctx([core.Format.DXF])
        self.existing.add(str(Path('/out/proj/plate_v3.dxf')))
        folder = FakeFolder('proj', [FakeFile('plate', 'f2d', versionNumber=3, versions_list=None)])
        c = core.visit_folder(ctx, folder)
        self.assertEqual((c.saved, c.skipped), (0, 1))
        self.assertEqual(ctx.app.documents.opened, [])

    def test_f3d_ignores_drawing_formats_and_f2d_ignores_design_formats(self):
        ctx = make_ctx([core.Format.DXF, core.Format.STEP])
        folder = FakeFolder('proj', [FakeFile('box', 'f3d'), FakeFile('box dwg', 'f2d')])
        c = core.visit_folder(ctx, folder)
        self.assertEqual((c.saved, c.errored), (2, 0))
        self.assertEqual([f for f, _ in self.design_em.created], ['createSTEPExportOptions'])
        self.assertEqual([f for f, _ in self.drawing_em.created], ['createDXFExportOptions'])

    def test_f2d_with_no_drawing_format_is_skipped_without_opening(self):
        ctx = make_ctx([core.Format.STEP])
        c = core.visit_folder(ctx, FakeFolder('proj', [FakeFile('plate', 'f2d')]))
        self.assertEqual((c.saved, c.skipped), (0, 1))
        self.assertEqual(ctx.app.documents.opened, [])

    def test_old_versions_only_fetched_when_asked(self):
        v2 = FakeFile('plate', 'f2d', versionNumber=2)
        v1 = FakeFile('plate', 'f2d', versionNumber=1)
        v3 = FakeFile('plate', 'f2d', versionNumber=3)
        v3.versions_list = [v1, v3, v2]   # Fusion hands these back in string order, not newest first
        ctx = make_ctx([core.Format.DXF], num_versions=1)
        c = core.visit_folder(ctx, FakeFolder('proj', [v3]))
        self.assertEqual(c.saved, 2)
        self.assertEqual([f.versionNumber for f, _ in ctx.app.documents.opened], [3, 2])

    def test_filename_sanitized_with_hash(self):
        self.assertEqual(core.sanitize_filename('plain'), 'plain')
        self.assertRegex(core.sanitize_filename('a/b'), r'^a b_[0-9a-f]{8}$')

    def test_cancel_stops_before_next_file(self):
        ctx = make_ctx([core.Format.DXF])
        files = [FakeFile('a', 'f2d'), FakeFile('b', 'f2d'), FakeFile('c', 'f2d')]
        cancel_after = 1
        c = core.visit_folder(ctx, FakeFolder('proj', files), progress=lambda i, n, f: i >= cancel_after)
        self.assertTrue(c.cancelled)
        self.assertEqual(c.saved, 1)
        self.assertEqual([f.name for f, _ in ctx.app.documents.opened], ['a'])

    def test_datafile_properties_read_once_per_file(self):
        class CountingFile(FakeFile):
            reads = 0

            @property
            def versionNumber(self):
                CountingFile.reads += 1
                return 7

            @versionNumber.setter
            def versionNumber(self, value):
                pass

        ctx = make_ctx([core.Format.DXF, core.Format.PDF])
        core.visit_folder(ctx, FakeFolder('proj', [CountingFile('plate', 'f2d')]))
        self.assertEqual(CountingFile.reads, 1)
        self.assertEqual(self.drawing_em.created[0][1], str(Path('/out/proj/plate_v7.pdf')))

    def test_loop_stops_when_fusion_is_quitting(self):
        ctx = make_ctx([core.Format.DXF])
        files = [FakeFile('a', 'f2d'), FakeFile('b', 'f2d'), FakeFile('c', 'f2d')]
        gone = {'now': False}
        core.application_gone = lambda: gone['now']

        def failing_open(file, visible=True):
            gone['now'] = True
            raise RuntimeError('Fusion closed')
        ctx.app.documents.open = failing_open
        try:
            c = core.visit_folder(ctx, FakeFolder('proj', files))
        finally:
            core.application_gone = lambda: False
        self.assertTrue(c.cancelled)
        self.assertEqual(c.errored, 1)   # the second and third file were not even tried

    def test_dxf_pdf_step_on_a_mixed_folder(self):
        """The screenshot folder: a drawing and a model per part, Export Types = dxf + pdf + step."""
        ctx = make_ctx([core.Format.DXF, core.Format.PDF, core.Format.STEP], unhide_all=True)
        files = [FakeFile('VSLP0002205-guide', 'f2d'), FakeFile('VSLP0002205-guide', 'f3d'),
                 FakeFile('VSLP0002207-angle', 'f2d'), FakeFile('VSLP0002207-angle', 'f3d')]
        c = core.visit_folder(ctx, FakeFolder('DXF+STEP+PDF', files))
        self.assertEqual((c.saved, c.skipped, c.errored), (6, 0, 0))
        # each document is opened exactly once, whatever the number of formats
        self.assertEqual(len(ctx.app.documents.opened), 4)
        self.assertEqual([f for f, _ in self.drawing_em.created],
                         ['createPDFExportOptions', 'createDXFExportOptions'] * 2)
        self.assertEqual([f for f, _ in self.design_em.created], ['createSTEPExportOptions'] * 2)
        names = sorted(Path(p).name for _, p in self.drawing_em.created + self.design_em.created)
        self.assertEqual(names, sorted(['VSLP0002205-guide_v1.pdf', 'VSLP0002205-guide_v1.dxf',
                                        'VSLP0002205-guide_v1.step', 'VSLP0002207-angle_v1.pdf',
                                        'VSLP0002207-angle_v1.dxf', 'VSLP0002207-angle_v1.step']))

    def test_subfolders_only_when_asked(self):
        ctx = make_ctx([core.Format.DXF])
        sub = FakeFolder('sub', [FakeFile('inner', 'f2d')])
        top = FakeFolder('top', [FakeFile('outer', 'f2d')], dataFolders=[sub])
        with_sub = [f.name for _, f in core.collect_files(ctx, top, True)]
        without = [f.name for _, f in core.collect_files(ctx, top, False)]
        self.assertEqual(with_sub, ['outer', 'inner'])
        self.assertEqual(without, ['outer'])

    def test_describe_folder_reads_like_the_breadcrumb(self):
        project = SimpleNamespace(name='予備枠')
        root = SimpleNamespace(name='予備枠', parentFolder=None, parentProject=project)   # root = project name
        a = SimpleNamespace(name='ウリセス', parentFolder=root, parentProject=project)
        b = SimpleNamespace(name='DXF+STEP+PDF', parentFolder=a, parentProject=project)
        self.assertEqual(core.describe_folder(b), '予備枠 / ウリセス / DXF+STEP+PDF')

    def test_resolve_folder_walks_the_saved_id_path(self):
        leaf = FakeFolder('DXF+STEP+PDF', [], id='f3')
        mid = FakeFolder('ウリセス', [], dataFolders=[FakeFolder('other', [], id='f9'), leaf], id='f2')
        root = FakeFolder('root', [], dataFolders=[mid], id='f1')
        project = SimpleNamespace(id='p1', rootFolder=root)
        app = SimpleNamespace(data=SimpleNamespace(dataProjects=SimpleNamespace(
            itemById=lambda pid: project if pid == 'p1' else None)))
        self.assertIs(core.resolve_folder(app, 'p1', ['f2', 'f3']), leaf)
        self.assertIs(core.resolve_folder(app, 'p1', []), root)
        with self.assertRaises(Exception):
            core.resolve_folder(app, 'p1', ['f2', 'missing'])
        with self.assertRaises(Exception):
            core.resolve_folder(app, 'nope', [])

    def test_progress_guard_survives_a_broken_panel(self):
        calls = []

        class Broken:
            def __call__(self, *a):
                raise RuntimeError('palette gone')

            def status(self, *a):
                raise RuntimeError('palette gone')

            def hide(self):
                calls.append('hide-broken')

        class Plain:
            def __call__(self, index, total, file):
                calls.append(('call', index))
                return False

            def status(self, phase, label):
                calls.append(('status', phase))

            def done(self, *a):
                calls.append('done')

            def hide(self):
                calls.append('hide')

        guard = core.ProgressGuard.__new__(core.ProgressGuard)
        guard._backends = [lambda: Broken(), lambda: Plain()]
        guard._impl = None
        guard._switch()
        self.assertFalse(guard(0, 3, FakeFile('a', 'f2d')))   # Broken raised -> swapped to Plain
        guard.status('opening', 'a')
        guard.done('ok')
        self.assertEqual(calls, ['hide-broken', ('call', 0), ('status', 'opening'), 'done'])

    def test_tree_gen_builds_relative_path(self):
        a = SimpleNamespace(name='A', parentFolder=None)
        b = SimpleNamespace(name='B', parentFolder=a)
        c = SimpleNamespace(name='C', parentFolder=b)
        self.assertEqual(core.tree_gen(c), Path('A') / 'B')
        self.assertEqual(core.tree_gen(a), Path())


if __name__ == '__main__':
    unittest.main(verbosity=2)
