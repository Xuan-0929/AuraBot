"""Lazy lifecycle manager for shared ToolGroup objects."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import RLock

from ..integration.sql.tool_group import ToolGroupSQL
from .tool_group import ToolGroup

ToolGroupLoader = Callable[[str], ToolGroup | None]
ToolGroupFactory = Callable[[], ToolGroup]


@dataclass(slots=True)
class CapabilityGeneration:
    value: ToolGroup
    reference_count: int = 0


class ToolGroupManager:
    """Load ToolGroups on first use and destroy them at zero references."""

    def __init__(
        self,
        *,
        loader: ToolGroupLoader | None = None,
        native_factories: Mapping[str, ToolGroupFactory] | None = None,
    ):
        """构造一个进程级、按需加载的 ToolGroup 生命周期管理器。

        ``loader`` 用于数据库或其他外部来源；``native_factories`` 用于 Plan 等
        项目原生 ToolGroup，并优先于 loader。构造阶段不会加载任何 ToolGroup。
        Manager 应由 ``BackendRuntime`` 持有，不应作为隐式全局单例使用。
        """

        self._loader = loader or ToolGroupSQL.get_tool_group
        self._native_factories = dict(native_factories or {})
        self._generations: dict[str, CapabilityGeneration] = {}
        self._closed = False
        self._lock = RLock()

    def acquire(self, names: Sequence[str]) -> tuple[ToolGroup, ...]:
        """按输入顺序取得 ToolGroup，并为每个名称增加一次引用。

        首次取得某名称时才调用 native factory 或 loader；后续调用复用同一 generation。
        整个 batch 具有回滚语义：任一名称不存在或加载失败时，本次已经增加的引用都会
        撤销。名称必须唯一、非空且无首尾空白；Manager 关闭后拒绝新的 acquire。
        """

        requested = _normalize_names(names)
        acquired_names: list[str] = []
        acquired: list[ToolGroup] = []
        with self._lock:
            if self._closed:
                raise RuntimeError("ToolGroupManager is closed")
            try:
                for name in requested:
                    generation = self._generations.get(name)
                    if generation is None:
                        value = self._load(name)
                        if value is None:
                            raise LookupError(
                                f"ToolGroup does not exist: {name}"
                            )
                        if value.name != name:
                            raise ValueError(
                                "ToolGroup loader returned a mismatched name"
                            )
                        generation = CapabilityGeneration(value)
                        self._generations[name] = generation
                    generation.reference_count += 1
                    acquired_names.append(name)
                    acquired.append(generation.value)
            except BaseException:
                self._release_names_locked(acquired_names)
                raise
        return tuple(acquired)

    def release(self, groups: Sequence[ToolGroup]) -> None:
        """归还由本 Manager acquire 的精确 ToolGroup 对象。

        方法同时验证名称和对象身份，防止释放其他 Manager 或其他 generation 的对象。
        引用归零后 generation 会立即移出 cache。Manager 已 close 后仍允许 outstanding
        lease 完成 release。
        """

        if isinstance(groups, (str, bytes)) or not isinstance(groups, Sequence):
            raise TypeError("groups must be a sequence")
        if any(not isinstance(group, ToolGroup) for group in groups):
            raise TypeError("groups must contain ToolGroup values")
        names = tuple(group.name for group in groups)
        with self._lock:
            for name, group in zip(names, groups):
                generation = self._generations.get(name)
                if generation is None or generation.value is not group:
                    raise ValueError(
                        f"ToolGroup is not acquired from this manager: {name}"
                    )
            self._release_names_locked(names)

    def get_cached(self, name: str) -> ToolGroup | None:
        """只读查询当前缓存 generation，不触发加载或增加引用。"""

        with self._lock:
            generation = self._generations.get(name)
            return generation.value if generation is not None else None

    def reference_count(self, name: str) -> int:
        """返回当前 generation 的引用数；名称未缓存时返回 ``0``。"""

        with self._lock:
            generation = self._generations.get(name)
            return generation.reference_count if generation is not None else 0

    def list_cached(self) -> tuple[ToolGroup, ...]:
        """返回当前所有已加载 ToolGroup 的不可变容器快照。

        该操作不增加引用；调用方不得把结果当作 acquire lease 并传给 release。
        """

        with self._lock:
            return tuple(
                generation.value
                for generation in self._generations.values()
            )

    def close(self) -> None:
        """关闭 Manager，拒绝后续 acquire，并清理无引用 generation。

        close 是幂等的。仍被 runtime lease 持有的 generation 会暂时保留，允许这些
        lease 在 shutdown 后续阶段安全 release，归零时再从 cache 移除。
        """

        with self._lock:
            self._closed = True
            for name, generation in tuple(self._generations.items()):
                if generation.reference_count <= 0:
                    self._generations.pop(name, None)

    def _load(self, name: str) -> ToolGroup | None:
        factory = self._native_factories.get(name)
        return factory() if factory is not None else self._loader(name)

    def _release_names_locked(self, names: Sequence[str]) -> None:
        for name in reversed(names):
            generation = self._generations.get(name)
            if generation is None:
                continue
            generation.reference_count -= 1
            if generation.reference_count <= 0:
                self._generations.pop(name, None)


def _normalize_names(names: Sequence[str]) -> tuple[str, ...]:
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise TypeError("ToolGroup names must be a sequence")
    snapshot = tuple(names)
    if any(
        not isinstance(name, str)
        or not name
        or name != name.strip()
        for name in snapshot
    ):
        raise ValueError("ToolGroup names must be non-empty trimmed strings")
    if len(set(snapshot)) != len(snapshot):
        raise ValueError("ToolGroup names must not contain duplicates")
    return snapshot


__all__ = [
    "CapabilityGeneration",
    "ToolGroupFactory",
    "ToolGroupLoader",
    "ToolGroupManager",
]
