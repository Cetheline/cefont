"""Conservative TAR extraction independent of Python's changing defaults."""

import os
from pathlib import PurePosixPath
import shutil


def extract_tar(archive, destination):
    """Extract regular files/directories into a private, empty staging directory.

    Preflight the entire member list before writing anything. Reject links,
    special/sparse files, absolute/parent paths, duplicate files and conflicts.
    Do not restore ownership, permission bits or timestamps from untrusted input.
    No extractall/filter API is used, so this also works on Python 3.8.
    """
    entries = []
    kinds = {}
    for member in archive.getmembers():
        name = member.name
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name
                or '\x00' in name or (path.parts and ':' in path.parts[0])):
            raise ValueError("Unsafe TAR member path: %r" % name)
        if not (member.isdir() or member.isfile()) or member.issparse():
            raise ValueError("Unsupported TAR member type: %r" % name)
        parts = path.parts
        if not parts:
            if member.isdir():
                continue  # tar.add(..., arcname='') writes a root directory.
            raise ValueError("Empty TAR file path")
        key = '/'.join(parts)
        kind = 'directory' if member.isdir() else 'file'
        if key in kinds and not (kind == kinds[key] == 'directory'):
            raise ValueError("Conflicting TAR member: %r" % name)
        kinds[key] = kind
        entries.append((member, parts))
    for _, parts in entries:
        for end in range(1, len(parts)):
            if kinds.get('/'.join(parts[:end])) == 'file':
                raise ValueError("TAR file is also used as a directory")

    for member, parts in entries:
        target = os.path.join(destination, *parts)
        if member.isdir():
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Missing TAR member data")
            with source, open(target, 'xb') as output:
                shutil.copyfileobj(source, output)
