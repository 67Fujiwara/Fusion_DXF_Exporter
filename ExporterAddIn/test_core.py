"""Plain-Python checks for exporter_core (no Fusion needed):  python3 test_core.py

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

import exporter_core as core  # noqa: E402

core.set_mtime = lambda path, t: None
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
    args = dict(app=app, folder=Path('/out'), formats=formats, projects_folders={}, use_active_folder=True,
                unhide_all=False, save_sketches=False, num_versions=0, export_non_design_files=False,
                open_hidden=False)
    args.update(kw)
    return core.Ctx(**args)


class ExportLoop(unittest.TestCase):
    def setUp(self):
        self.existing = set()
        core.output_path_exists = lambda path, file: str(path) in self.existing
        self.drawing_em = FakeExportManager(['createPDFExportOptions', 'createDXFExportOptions'])
        self.design_em = FakeExportManager(['createSTEPExportOptions', 'createFusionArchiveExportOptions'])
        root = SimpleNamespace(name='root', sketches=[], occurrences=[])
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

    def test_hidden_open_passes_visible_false_and_skips_activate(self):
        ctx = make_ctx([core.Format.DXF], open_hidden=True)
        core.visit_folder(ctx, FakeFolder('proj', [FakeFile('plate', 'f2d')]))
        self.assertEqual(ctx.app.documents.opened[0][1], False)

    def test_filename_sanitized_with_hash(self):
        self.assertEqual(core.sanitize_filename('plain'), 'plain')
        self.assertRegex(core.sanitize_filename('a/b'), r'^a b_[0-9a-f]{8}$')

    def test_tree_gen_builds_relative_path(self):
        a = SimpleNamespace(name='A', parentFolder=None)
        b = SimpleNamespace(name='B', parentFolder=a)
        c = SimpleNamespace(name='C', parentFolder=b)
        self.assertEqual(core.tree_gen(c), Path('A') / 'B')
        self.assertEqual(core.tree_gen(a), Path())


if __name__ == '__main__':
    unittest.main(verbosity=2)
