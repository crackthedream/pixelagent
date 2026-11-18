"""Collect notices from the exact installed distributions used by PyInstaller.

Run after building, with the same Python environment. Reads metadata, not imported
packages, and does not contact the network. License text is copied without edits.
"""
import ast
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect(repo):
    repo = Path(repo).resolve()
    toc_path = repo / 'build' / 'PixelAgent' / 'Analysis-00.toc'
    exe = repo / 'dist' / 'PixelAgent.exe'
    toc = ast.literal_eval(toc_path.read_text(encoding='utf-8'))
    entries = [tuple(entry) for index in (13, 14, 15, 18) for entry in toc[index]]
    sources = {_norm(entry[1]): entry for entry in entries}
    # Guard against SDK optional audio libraries leaking back into the release.
    if any(entry[0].lower().startswith(('sounddevice', '_sounddevice_data')) or 'portaudio' in entry[0].lower()
           for entry in entries):
        raise ValueError('Unexpected optional audio dependency in Windows release')
    unmatched = set(sources)
    packages = []
    copies = []
    destination = repo / 'dist' / 'THIRD-PARTY-NOTICES'
    destination.mkdir(parents=True, exist_ok=True)
    # release_zip includes only files in the new manifest, so a repeated build cannot ship stale notices.
    for dist in metadata.distributions():
        files = dist.files or []
        matches = [file for file in files if _norm(dist.locate_file(file)) in sources]
        if not matches:
            continue
        unmatched.difference_update(_norm(dist.locate_file(file)) for file in files)
        name, version = dist.metadata['Name'], dist.version
        slug = re.sub(r'[^a-zA-Z0-9._-]', '_', name + '-' + version)
        license_files = [file for file in files if re.search(
            r'^(licen[cs]e|copying|notice|authors|copyright|ofl)([._-]|$)', Path(str(file)).name, re.I)]
        if not license_files:
            raise ValueError(f'No installed license text found for {name} {version}')
        notice_files = []
        for file in license_files:
            relative = Path(str(file))
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Unsafe installed license path in {name}')
            source = Path(dist.locate_file(file))
            target = destination / 'packages' / slug / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            relative_target = target.relative_to(destination).as_posix()
            notice_files.append(relative_target)
            copies.append({'file': relative_target, 'sha256': _digest(target)})
        declared_license = dist.metadata.get('License')
        packages.append({
            'name': name, 'version': version,
            'license_expression': dist.metadata.get('License-Expression'),
            'license_metadata': declared_license if len(declared_license or '') < 256 else 'See copied license text',
            'license_classifiers': [item for item in dist.metadata.get_all('Classifier', [])
                                    if item.startswith('License ::')],
            'project_urls': dist.metadata.get_all('Project-URL', []),
            'source_distribution': f'https://pypi.org/project/{name}/{version}/#files',
            'bundled_entries': sorted(sources[_norm(dist.locate_file(file))][0] for file in matches),
            'notice_files': notice_files,
        })
    unmatched_site = sorted(sources[path][0] for path in unmatched if 'site-packages' in path)
    if unmatched_site:
        raise ValueError('Unmapped installed runtime files: ' + ', '.join(unmatched_site))
    runtime = Path(sys.base_prefix)
    for source, name in ((runtime / 'LICENSE.txt', 'PYTHON-LICENSE.txt'),
                         (runtime / 'Doc' / 'html' / 'license.html', 'PYTHON-INCORPORATED-LICENSES.html')):
        if not source.is_file():
            raise ValueError(f'Python distribution license document is missing: {source.name}')
        target = destination / name
        shutil.copyfile(source, target)
        copies.append({'file': name, 'sha256': _digest(target)})
    manifest = {
        'format_version': 1, 'executable_sha256': _digest(exe),
        'python_version': sys.version.split()[0],
        'packages': sorted(packages, key=lambda package: package['name'].lower()),
        'license_files': sorted(copies, key=lambda copy: copy['file']),
        'native_binary_names': sorted(entry[0] for entry in toc[15]),
        'optional_audio_excluded': True,
        'scope': 'Actual build table matched to installed distribution metadata; no license is assigned to PixelAgent or its drawing engine.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    rows = ['# Third-party software notices', '',
            'This Windows bundle includes the third-party components listed below. Their original license texts are copied under `packages/`; `manifest.json` records versions, file hashes and the executable to which this inventory applies.', '',
            'These notices do not choose or grant a license for PixelAgent, the separately maintained drawing engine, user reference images, or generated artwork.', '',
            'The Python interpreter is bundled without source modifications, with a selected set of standard-library modules. `PYTHON-LICENSE.txt` includes the Windows runtime redistribution terms; `PYTHON-INCORPORATED-LICENSES.html` preserves the installed Python documentation for incorporated software, including OpenSSL and libffi. Some sections in those upstream documents concern components not present in this bundle.', '',
            'Pillow\'s included license file also contains its native library notices. This software uses the FreeType Project (https://freetype.org), under the FreeType License included in Pillow\'s notice file. This software is based in part on the work of the Independent JPEG Group.', '',
            'NumPy\'s notice file includes OpenBLAS, LAPACK, and the GCC runtime library exception. PyInstaller\'s COPYING file is included for reference; its distribution exception applies to the application bundle. Optional sounddevice and PortAudio/ASIO libraries are excluded.', '',
            'Unmodified source releases for the installed packages are available at the version-specific links below, including certifi\'s MPL-covered certificate data. CPython source: https://github.com/python/cpython/tree/v' + sys.version.split()[0] + '.', '',
            '| Component | Version | Source distribution |', '| --- | --- | --- |']
    for package in manifest['packages']:
        rows.append(f"| {package['name']} | {package['version']} | [source]({package['source_distribution']}) |")
    (destination / 'README.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return destination, manifest


if __name__ == '__main__':
    path, manifest = collect(Path(__file__).resolve().parents[1])
    print(json.dumps({'directory': str(path), 'packages': len(manifest['packages']),
                      'license_files': len(manifest['license_files']),
                      'executable_sha256': manifest['executable_sha256']}))
