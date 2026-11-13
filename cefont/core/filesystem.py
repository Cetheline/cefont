"""Rooted deletion helpers for user-managed files (Linux/POSIX).

Roots are trusted configuration. Descendants must be strictly beneath the root;
symlink components are rejected. Destructive traversal uses directory descriptors
and O_NOFOLLOW so replacing a checked component cannot redirect it through a link.
"""

from contextlib import contextmanager
import os
import stat


def contained_parts(root, path):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    if os.path.commonpath([root, path]) != root or path == root:
        raise ValueError("Path must be strictly inside the managed directory")
    return os.path.relpath(path, root).split(os.sep)


@contextmanager
def opened_parent(root, path):
    parts = contained_parts(root, path)
    descriptors = []
    try:
        # Refuse a symlink at the configured root as well as below it.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open(root, flags))
        for part in parts[:-1]:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        yield descriptors, parts
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def validate_regular_file(root, path):
    with opened_parent(root, path) as (fds, parts):
        mode = os.stat(parts[-1], dir_fd=fds[-1], follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("Only regular files may be removed")


def remove_regular_file(root, path):
    with opened_parent(root, path) as (fds, parts):
        mode = os.stat(parts[-1], dir_fd=fds[-1], follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("Only regular files may be removed")
        os.unlink(parts[-1], dir_fd=fds[-1])
        # Empty parents only; never remove the managed root or follow a link.
        for index in range(len(parts) - 2, -1, -1):
            try:
                os.rmdir(parts[index], dir_fd=fds[index])
            except OSError:
                break


def remove_directory(root, path):
    """Delete a contained directory; internal links are unlinked, never followed."""
    def empty(descriptor):
        for name in os.listdir(descriptor):
            mode = os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                try:
                    empty(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=descriptor)
            else:
                os.unlink(name, dir_fd=descriptor)

    with opened_parent(root, path) as (fds, parts):
        child = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=fds[-1])
        try:
            empty(child)
        finally:
            os.close(child)
        os.rmdir(parts[-1], dir_fd=fds[-1])
