"""Toolchain identity and public asset staging, without invoking real compilers."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_packager',ROOT/'scripts/package_app.py')
packager=importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)

class PackagingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.toolchain=self.root/'tools'
        files={}
        for relative in ('bin/ffmpeg','bin/ffprobe','bin/tsmuxer','bin/mkisofs','bin/reexport-m2ts',
                         'deps/bin/dvdauthor','deps/bin/spumux','deps/lib/libbluray.2.dylib'):
            path=self.toolchain/relative;path.parent.mkdir(parents=True,exist_ok=True)
            payload=('synthetic test '+relative).encode();path.write_bytes(payload)
            files[relative]={'source':'generated unit-test fixture','sha256':hashlib.sha256(payload).hexdigest()}
        (self.toolchain/'provenance.json').write_text(json.dumps({'files':files,'player_files':{'lib/libvlc.dylib':hashlib.sha256(b'synthetic fixture').hexdigest()}}))
        (self.toolchain/'licenses').mkdir()
        (self.toolchain/'licenses/NOTICE').write_text('Synthetic test fixtures only')
        player=self.toolchain/'deps/player-runtime/lib/libvlc.dylib'
        player.parent.mkdir(parents=True);player.write_bytes(b'synthetic fixture')

    def test_missing_toolchain_does_not_guess_local_user_binary(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(RuntimeError,'DISCAUTHOR_TOOLCHAIN_DIR'):
            packager.toolchain_root()

    def test_changed_executable_is_rejected_before_packaging(self):
        (self.toolchain/'bin/ffmpeg').write_bytes(b'changed')
        with patch.dict(os.environ,{'DISCAUTHOR_TOOLCHAIN_DIR':str(self.toolchain)}):
            with self.assertRaisesRegex(RuntimeError,'hash mismatch'):
                packager.toolchain_root()

    def test_changed_player_library_is_rejected(self):
        (self.toolchain/'deps/player-runtime/lib/libvlc.dylib').write_bytes(b'changed')
        with patch.dict(os.environ,{'DISCAUTHOR_TOOLCHAIN_DIR':str(self.toolchain)}):
            with self.assertRaisesRegex(RuntimeError,'VLC runtime hash mismatch'):
                packager.toolchain_root()

    def test_staging_has_only_generic_resources_and_its_toolchain_notices(self):
        with patch.dict(os.environ,{'DISCAUTHOR_TOOLCHAIN_DIR':str(self.toolchain)}),patch.object(packager.subprocess,'run'):
            source=packager.prepare_source(self.root/'stage')
        assets={str(p.relative_to(source/'assets')) for p in (source/'assets').rglob('*') if p.is_file() and not p.name.startswith('._')}
        self.assertEqual(assets,{'default-project.json','fonts/MochiyPopP-Regular.otf','templates/general-preview.png'})
        self.assertTrue((source/'licenses/toolchain/NOTICE').is_file())
        self.assertTrue((source/'licenses/toolchain-provenance.json').is_file())
        self.assertFalse((source/'projects').exists())

if __name__=='__main__':unittest.main()
