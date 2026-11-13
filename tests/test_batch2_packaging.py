"""Build/install only into a disposable environment; never use user settings."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cefont-package-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name)
        cls.source = cls.base / 'source'
        cls.source.mkdir()
        for filename in ('pyproject.toml', 'README.md'):
            shutil.copy2(ROOT / filename, cls.source / filename)
        for directory in ('cefont', 'data'):
            shutil.copytree(ROOT / directory, cls.source / directory,
                            ignore=shutil.ignore_patterns('__pycache__'))
        cls.env = os.environ.copy()
        cls.env.update(PYTHONDONTWRITEBYTECODE='1', PIP_NO_INDEX='1',
                       PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_CACHE_DIR='1',
                       XDG_DATA_HOME=str(cls.base / 'xdg-data'),
                       XDG_CONFIG_HOME=str(cls.base / 'xdg-config'),
                       XDG_CACHE_HOME=str(cls.base / 'xdg-cache'))
        cls.env.pop('PYTHONPATH', None)
        cls.python = cls.base / 'build-env' / 'bin' / 'python'
        venv.EnvBuilder(system_site_packages=True, with_pip=False, symlinks=True).create(cls.base / 'build-env')
        cls.run_command([str(cls.python), '-B', '-c',
                         "import setuptools.build_meta as b; b.build_wheel('dist'); b.build_sdist('dist')"],
                        cwd=cls.source)
        cls.wheel = next((cls.source / 'dist').glob('*.whl'))
        cls.target = cls.base / 'installed'
        cls.run_command([str(cls.python), '-B', '-m', 'pip', 'install', '--no-deps',
                         '--no-index', '--target', str(cls.target), str(cls.wheel)])

    @classmethod
    def run_command(cls, args, cwd=None):
        result = subprocess.run(args, cwd=cwd or cls.base, env=cls.env,
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError('Command failed: %r\n%s\n%s' % (args, result.stdout, result.stderr))
        return result.stdout

    def resource_counts(self, location):
        code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                "from cefont.core import data;"
                "print(json.dumps([len(data.load_orthographies()),"
                "len(data.load_vendors_and_notices()[0]),len(data.load_licenses())]))")
        return json.loads(self.run_command([str(self.python), '-I', '-B', '-c', code, str(location)]))

    def test_wheel_contains_all_reference_assets_unchanged(self):
        with zipfile.ZipFile(str(self.wheel)) as archive:
            assets = [path for path in (ROOT / 'data').rglob('*')
                      if path.is_file() and path.suffix != '.py' and '__pycache__' not in path.parts]
            expected = {'cefont/resources/' + path.relative_to(ROOT / 'data').as_posix()
                        for path in assets}
            self.assertEqual(expected - set(archive.namelist()), set(), 'Missing wheel assets')
            for path in assets:
                name = 'cefont/resources/' + path.relative_to(ROOT / 'data').as_posix()
                with self.subTest(asset=name):
                    self.assertIn(name, archive.namelist())
                    self.assertEqual(hashlib.sha256(archive.read(name)).digest(),
                                     hashlib.sha256(path.read_bytes()).digest())

    def test_installed_resources_load_outside_repository(self):
        self.assertEqual(self.resource_counts(self.target), [127, 1517, 23])

    def test_zip_import_resources_load_without_filesystem_assumptions(self):
        self.assertEqual(self.resource_counts(self.wheel), [127, 1517, 23])

    def test_source_layout_loads_without_installation(self):
        self.assertEqual(self.resource_counts(self.source), [127, 1517, 23])

    def test_editable_install_loads_resources_outside_source(self):
        self.run_command([str(self.python), '-B', '-m', 'pip', 'install', '--no-deps',
                          '--no-build-isolation', '--editable', str(self.source)])
        # No source/target path insertion: only the editable environment mapping.
        self.assertEqual(self.resource_counts(self.base / 'unused'), [127, 1517, 23])

    def test_sdist_rebuild_retains_resources(self):
        sdist = next((self.source / 'dist').glob('*.tar.gz'))
        unpacked = self.base / 'sdist'
        unpacked.mkdir()
        # This archive was generated above from this test's own source copy.
        shutil.unpack_archive(str(sdist), str(unpacked))
        rebuilt = next(unpacked.iterdir())
        self.run_command([str(self.python), '-B', '-c',
                          "import setuptools.build_meta as b; b.build_wheel('wheel')"], cwd=rebuilt)
        self.assertEqual(self.resource_counts(next((rebuilt / 'wheel').glob('*.whl'))),
                         [127, 1517, 23])

    def test_python38_resource_api_fallback_loads_packaged_data(self):
        code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                "from importlib import resources;del resources.files;"
                "from cefont.core import data;"
                "print(json.dumps([len(data.load_orthographies()),"
                "len(data.load_vendors_and_notices()[0]),len(data.load_licenses())]))")
        counts = json.loads(self.run_command([str(self.python), '-I', '-B', '-c', code, str(self.wheel)]))
        self.assertEqual(counts, [127, 1517, 23])
