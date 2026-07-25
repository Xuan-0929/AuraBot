from __future__ import annotations

import os
import stat
from pathlib import Path


def _prepare_sqlite_file(path: Path) -> tuple[int, int]:
    flags = os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    flags |= nofollow_flag

    while True:
        try:
            descriptor = os.open(
                path,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            break
        except FileExistsError:
            try:
                path_stat = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ValueError(
                    f"SQLite database could not be inspected safely: {error}"
                ) from error

            if stat.S_ISLNK(path_stat.st_mode):
                raise ValueError("SQLite database must not be a symlink")
            if not stat.S_ISREG(path_stat.st_mode):
                raise ValueError("SQLite database must be a regular file")
            try:
                descriptor = os.open(path, flags)
                break
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ValueError(
                    f"SQLite database could not be opened safely: {error}"
                ) from error
        except OSError as error:
            raise ValueError(
                f"SQLite database could not be reserved safely: {error}"
            ) from error

    try:
        path_stat = path.lstat()
        descriptor_stat = os.fstat(descriptor)
        _validate_regular_file_identity(path_stat, descriptor_stat)
        os.fchmod(descriptor, 0o600)
        final_path_stat = path.lstat()
        final_descriptor_stat = os.fstat(descriptor)
        _validate_regular_file_identity(
            final_path_stat,
            final_descriptor_stat,
        )
        return (final_descriptor_stat.st_dev, final_descriptor_stat.st_ino)
    except OSError as error:
        raise ValueError(
            f"SQLite database identity could not be validated: {error}"
        ) from error
    finally:
        os.close(descriptor)


def _validate_sqlite_file_identity(
    path: Path,
    *,
    expected_identity: tuple[int, int],
) -> None:
    try:
        path_stat = path.lstat()
    except OSError as error:
        raise ValueError(
            f"SQLite database could not be revalidated safely: {error}"
        ) from error
    if stat.S_ISLNK(path_stat.st_mode):
        raise ValueError("SQLite database must not be a symlink")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("SQLite database must be a regular file")
    identity = (path_stat.st_dev, path_stat.st_ino)
    if identity != expected_identity:
        raise ValueError("SQLite database identity changed while opening")
    if stat.S_IMODE(path_stat.st_mode) != 0o600:
        raise ValueError("SQLite database permissions changed while opening")


def _validate_regular_file_identity(
    path_stat: os.stat_result,
    descriptor_stat: os.stat_result,
) -> None:
    if stat.S_ISLNK(path_stat.st_mode):
        raise ValueError("SQLite database must not be a symlink")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("SQLite database must be a regular file")
    if not stat.S_ISREG(descriptor_stat.st_mode):
        raise ValueError("SQLite database descriptor must be a regular file")
    if (
        path_stat.st_dev,
        path_stat.st_ino,
    ) != (
        descriptor_stat.st_dev,
        descriptor_stat.st_ino,
    ):
        raise ValueError("SQLite database identity changed while opening")
