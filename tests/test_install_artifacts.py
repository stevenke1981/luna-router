"""Installer must not copy checkout metadata or local secrets into runtime."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import install_router


class InstallArtifactsTests(unittest.TestCase):
    def test_checkout_and_local_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            files = ['router.py', 'luna.py', 'credentials.py', 'luna_mcp.py',
                     'config.json', 'skills/gpt-luna-decision/SKILL.md']
            manifest = []
            for name in files:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture\n', encoding='utf-8')
                manifest.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + name)
            (source / 'MANIFEST.sha256').write_text('\n'.join(manifest) + '\n', encoding='utf-8')
            excluded = ['.git/config', '.tmp/scratch', '.venv/marker', '.env', '.env.local']
            for name in excluded:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture-not-a-secret', encoding='utf-8')
            result = install_router.install(source, root / 'codex-home')
            runtime = Path(result['runtime'])
            self.assertTrue((runtime / 'luna.py').is_file())
            for name in excluded:
                self.assertFalse((runtime / name).exists(), name)
