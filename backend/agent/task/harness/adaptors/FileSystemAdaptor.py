"""Adapt FileSystem tool paths to one Task working directory."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

from ....tools.core.ToolCallEntity import ResolvedToolCall
from .Adaptor import Adaptor, AdaptorRouteError


class PathBuildError(ValueError):
    """Raised when a model-produced path cannot be built safely."""


class PathOutOfSandboxError(PathBuildError):
    """Raised when a path resolves outside the current working directory."""

    def __init__(self, path: Path, current_directory: Path):
        self.path = str(path)
        self.current_directory = str(current_directory)
        super().__init__(
            f"path {self.path!r} is outside current_directory "
            f"{self.current_directory!r}"
        )


class FileSystemAdaptor(Adaptor):
    """Build absolute, sandboxed paths for native FileSystem tools."""

    PATH_FIELDS = {
        "list_directory": "path",
        "read_file": "path",
        "stat": "path",
        "glob": "root",
        "search_files": "root",
        "create_file": "url",
        "edit_file": "path",
        "bash": "PATH",
    }

    def __init__(self, current_directory: str | None):
        self._raw_current_directory = current_directory
        self._current_directory: Path | None = None

    @property
    def current_directory(self) -> str:
        """Return the normalized absolute current working directory."""

        return str(self._get_current_directory())

    def adapt(self, entity: ResolvedToolCall) -> ResolvedToolCall:
        """Return a copied call with its routed path normalized."""

        if not isinstance(entity, ResolvedToolCall):
            raise TypeError("entity must be a ResolvedToolCall")

        try:
            path_field = self.PATH_FIELDS[entity.tool.name]
        except KeyError as exc:
            raise AdaptorRouteError(
                f"FileSystemAdaptor is not configured for tool "
                f"{entity.tool.name!r}"
            ) from exc

        arguments = dict(entity.arguments)
        if path_field not in arguments:
            raise PathBuildError(
                f"resolved {entity.tool.name!r} call arguments must contain "
                f"{path_field!r}"
            )

        path_value = arguments[path_field]
        if entity.tool.name == "create_file":
            path_value = self._build_create_file_url(path_value)
        else:
            path_value = self.build_path(path_value)
        arguments[path_field] = path_value

        return ResolvedToolCall(
            tool=entity.tool,
            arguments=arguments,
            call_id=entity.call_id,
        )

    def build_path(self, path: str) -> str:
        """Build a filesystem path and enforce Task-directory containment."""

        if not isinstance(path, str) or not path:
            raise PathBuildError("path must be a non-empty string")

        current_directory = self._get_current_directory()
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = current_directory / candidate
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PathBuildError(f"failed to build path {path!r}") from exc

        try:
            resolved.relative_to(current_directory)
        except ValueError as exc:
            raise PathOutOfSandboxError(
                resolved,
                current_directory,
            ) from exc

        return str(resolved)

    def _build_create_file_url(self, value: object) -> str:
        if not isinstance(value, str) or not value:
            raise PathBuildError("url must be a non-empty string")

        parsed = urlsplit(value)
        if not parsed.scheme:
            return self.build_path(value)
        if parsed.scheme.casefold() != "file":
            raise PathBuildError("url must be a local file URL or path")
        if parsed.netloc.casefold() not in ("", "localhost"):
            raise PathBuildError("file URL authority must be empty or localhost")
        if parsed.query or parsed.fragment:
            raise PathBuildError("file URL must not contain query or fragment")
        if not parsed.path.startswith("/"):
            raise PathBuildError("file URL path must be absolute")

        decoded_path = unquote(parsed.path)
        if not decoded_path:
            raise PathBuildError("file URL path must be non-empty")
        return self.build_path(decoded_path)

    def _get_current_directory(self) -> Path:
        if self._current_directory is None:
            self._current_directory = self._build_current_directory(
                self._raw_current_directory
            )
        return self._current_directory

    @staticmethod
    def _build_current_directory(current_directory: str | None) -> Path:
        if not isinstance(current_directory, str) or not current_directory:
            raise PathBuildError(
                "current_directory must be a non-empty absolute path"
            )

        directory = Path(current_directory)
        if not directory.is_absolute():
            raise PathBuildError("current_directory must be an absolute path")

        try:
            return directory.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PathBuildError(
                f"failed to build current_directory {current_directory!r}"
            ) from exc


__all__ = [
    "FileSystemAdaptor",
    "PathBuildError",
    "PathOutOfSandboxError",
]
