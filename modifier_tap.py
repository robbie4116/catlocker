from __future__ import annotations


class ModifierTapTracker:
    """Tracks whether one configured modifier was used without any other key."""

    def __init__(self, modifier_vk: int | None) -> None:
        self._modifier_vk: int | None = None
        self._active = False
        self._eligible = False
        self._disqualified = False
        self.set_modifier(modifier_vk)

    @property
    def modifier_vk(self) -> int | None:
        return self._modifier_vk

    def set_modifier(self, modifier_vk: int | None) -> None:
        modifier_vk = None if modifier_vk is None else int(modifier_vk)
        if modifier_vk != self._modifier_vk:
            self._modifier_vk = modifier_vk
            self.reset()

    def reset(self) -> None:
        self._active = False
        self._eligible = False
        self._disqualified = False

    def observe_down(
        self,
        vk: int,
        held_before: set[int],
        *,
        repeat: bool,
    ) -> bool:
        if self._modifier_vk is None:
            return False
        vk = int(vk)
        if repeat:
            return False
        if vk == self._modifier_vk:
            if self._active:
                return False
            self._active = True
            self._eligible = not held_before
            self._disqualified = bool(held_before)
        elif self._active:
            self._disqualified = True
        return False

    def observe_up(self, vk: int) -> bool:
        if self._modifier_vk is None or int(vk) != self._modifier_vk:
            return False
        eligible = self._active and self._eligible and not self._disqualified
        self.reset()
        return eligible
