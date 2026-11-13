"""Declared-version syntax and runtime annotation contracts."""
import ast
import importlib
import inspect
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import os
import typing
import unittest


class CompatibilityTests(unittest.TestCase):
    def test_all_application_annotations_resolve(self):
        for path in Path('cefont').rglob('*.py'):
            module_name = '.'.join(path.with_suffix('').parts)
            if path.name == '__init__.py':
                module_name = module_name.rsplit('.', 1)[0]
            module = importlib.import_module(module_name)
            for name, value in vars(module).items():
                if getattr(value, '__module__', None) != module.__name__:
                    continue
                if inspect.isfunction(value):
                    with self.subTest(module=module_name, function=name):
                        typing.get_type_hints(value)
                elif inspect.isclass(value):
                    for method, function in vars(value).items():
                        if inspect.isfunction(function):
                            with self.subTest(module=module_name, method=name + '.' + method):
                                typing.get_type_hints(function)

    def test_application_parses_with_python38_grammar(self):
        for path in Path('cefont').rglob('*.py'):
            with self.subTest(path=str(path)):
                ast.parse(path.read_text(), filename=str(path), feature_version=(3, 8))

    def test_cli_imports_on_available_supported_interpreters(self):
        interpreters = {sys.executable}
        for minor in range(8, 15):
            executable = shutil.which('python3.%d' % minor)
            if executable:
                interpreters.add(os.path.realpath(executable))
        with tempfile.TemporaryDirectory(prefix='cefont-compat-') as tmp:
            env = os.environ.copy()
            env.update(PYTHONDONTWRITEBYTECODE='1', XDG_DATA_HOME=tmp+'/data',
                       XDG_CONFIG_HOME=tmp+'/config', XDG_CACHE_HOME=tmp+'/cache')
            for executable in interpreters:
                with self.subTest(interpreter=executable):
                    result = subprocess.run([executable, '-B', 'bin/cefont', '--help'],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
