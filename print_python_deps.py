#!/usr/bin/env vpython3
# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Prints all non-system dependencies for the given module.

The primary use-case for this script is to generate the list of python modules
required for .isolate files.
"""

import argparse
import os
import pipes
import sys

# Don't use any helper modules, or else they will end up in the results.


_SRC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def ComputePythonDependencies():
  """Gets the paths of imported non-system python modules.

  A path is assumed to be a "system" import if it is outside of chromium's
  src/. The paths will be relative to the current directory.
  """
  module_paths = (m.__file__ for m in sys.modules.values()
                  if m and hasattr(m, '__file__') and m.__file__)

  src_paths = set()
  for path in module_paths:
    if path == __file__:
      continue
    path = os.path.abspath(path)
    if not path.startswith(_SRC_ROOT):
      continue

    if (path.endswith('.pyc')
        or (path.endswith('c') and not os.path.splitext(path)[1])):
      path = path[:-1]
    src_paths.add(path)

  return src_paths


def quote(string):
  if string.count(' ') > 0:
    return '"%s"' % string
  else:
    return string


def _NormalizeCommandLine(options):
  """Returns a string that when run from SRC_ROOT replicates the command."""
  args = ['build/print_python_deps.py']
  root = os.path.relpath(options.root, _SRC_ROOT)
  if root != '.':
    args.extend(('--root', root))
  if options.output:
    args.extend(('--output', os.path.relpath(options.output, _SRC_ROOT)))
  if options.gn_paths:
    args.extend(('--gn-paths',))
  for allowlist in sorted(options.allowlists):
    args.extend(('--allowlist', os.path.relpath(allowlist, _SRC_ROOT)))
  args.append(os.path.relpath(options.module, _SRC_ROOT))
  if os.name == 'nt':
    return ' '.join(quote(x) for x in args).replace('\\', '/')
  else:
    return ' '.join(pipes.quote(x) for x in args)


def _FindPythonInDirectory(directory, allow_test):
  """Returns an iterable of all non-test python files in the given directory."""
  for root, _dirnames, filenames in os.walk(directory):
    for filename in filenames:
      if filename.endswith('.py') and (allow_test
                                       or not filename.endswith('_test.py')):
        yield os.path.join(root, filename)


def _ImportModuleByPath(module_path):
  """Imports a module by its source file."""
  # Replace the path entry for print_python_deps.py with the one for the given
  # module.
  sys.path[0] = os.path.dirname(module_path)

  # https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly
  module_name = os.path.splitext(os.path.basename(module_path))[0]
  import importlib.util  # Python 3 only, since it's unavailable in Python 2.
  spec = importlib.util.spec_from_file_location(module_name, module_path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[module_name] = module
  spec.loader.exec_module(module)


def main():
  parser = argparse.ArgumentParser(
      description='Prints all non-system dependencies for the given module.')
  parser.add_argument('module',
                      help='The python module to analyze.')
  parser.add_argument('--root', default='.',
                      help='Directory to make paths relative to.')
  parser.add_argument('--output',
                      help='Write output to a file rather than stdout.')
  parser.add_argument('--inplace', action='store_true',
                      help='Write output to a file with the same path as the '
                      'module, but with a .pydeps extension. Also sets the '
                      'root to the module\'s directory.')
  parser.add_argument('--no-header', action='store_true',
                      help='Do not write the "# Generated by" header.')
  parser.add_argument('--gn-paths', action='store_true',
                      help='Write paths as //foo/bar/baz.py')
  parser.add_argument('--did-relaunch', action='store_true',
                      help=argparse.SUPPRESS)
  parser.add_argument('--allowlist',
                      default=[],
                      action='append',
                      dest='allowlists',
                      help='Recursively include all non-test python files '
                      'within this directory. May be specified multiple times.')
  options = parser.parse_args()

  if options.inplace:
    if options.output:
      parser.error('Cannot use --inplace and --output at the same time!')
    if not options.module.endswith('.py'):
      parser.error('Input module path should end with .py suffix!')
    options.output = options.module + 'deps'
    options.root = os.path.dirname(options.module)

  modules = [options.module]
  if os.path.isdir(options.module):
    modules = list(_FindPythonInDirectory(options.module, allow_test=True))
  if not modules:
    parser.error('Input directory does not contain any python files!')

  is_vpython = 'vpython' in sys.executable
  if not is_vpython:
    # Prevent infinite relaunch if something goes awry.
    assert not options.did_relaunch
    # Re-launch using vpython will cause us to pick up modules specified in
    # //.vpython, but does not cause it to pick up modules defined inline via
    # [VPYTHON:BEGIN] ... [VPYTHON:END] comments.
    # TODO(agrieve): Add support for this if the need ever arises.
    os.execvp('vpython3', ['vpython3'] + sys.argv + ['--did-relaunch'])

  # Work-around for protobuf library not being loadable via importlib
  # This is needed due to compile_resources.py.
  import importlib._bootstrap_external
  importlib._bootstrap_external._NamespacePath.sort = lambda self, **_: 0

  paths_set = set()
  try:
    for module in modules:
      _ImportModuleByPath(module)
      paths_set.update(ComputePythonDependencies())
  except Exception:
    # Output extra diagnostics when loading the script fails.
    sys.stderr.write('Error running print_python_deps.py.\n')
    sys.stderr.write('is_vpython={}\n'.format(is_vpython))
    sys.stderr.write('did_relanuch={}\n'.format(options.did_relaunch))
    sys.stderr.write('python={}\n'.format(sys.executable))
    raise

  for path in options.allowlists:
    paths_set.update(
        os.path.abspath(p)
        for p in _FindPythonInDirectory(path, allow_test=False))

  paths = [os.path.relpath(p, options.root) for p in paths_set]

  normalized_cmdline = _NormalizeCommandLine(options)
  out = open(options.output, 'w', newline='') if options.output else sys.stdout
  with out:
    if not options.no_header:
      out.write('# Generated by running:\n')
      out.write('#   %s\n' % normalized_cmdline)
    prefix = '//' if options.gn_paths else ''
    for path in sorted(paths):
      out.write(prefix + path.replace('\\', '/') + '\n')


if __name__ == '__main__':
  sys.exit(main())
