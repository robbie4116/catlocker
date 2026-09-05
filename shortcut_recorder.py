from __future__ import annotations

from hotkeys import (
    KeyEvent,
    Shortcut,
    ShortcutError,
    VK_TO_NAME,
    modifier_family_for_vk,
    shortcut_from_pressed_vks,
    format_pressed_vks,
)
from modifier_tap import ModifierTapTracker


UNSUPPORTED_KEY_MESSAGE = "This key isn't supported. Try another key."
MODIFIER_ONLY_MESSAGE = "Use one trigger key, or tap a single modifier."


class ShortcutRecorder:
    """Pure recording-attempt state machine driven by normalized key events."""

    def __init__(self, session_id: object, *, held_keys=()) -> None:
        self.session_id = session_id
        self._held: set[int] = {int(vk) for vk in held_keys}
        self._candidate: Shortcut | None = None
        self._explanation: str | None = None
        self._invalid = False
        self._attempt_started = False
        self._awaiting_release = bool(self._held)
        self._tap_tracker: ModifierTapTracker | None = None

    @property
    def held_keys(self) -> frozenset[int]:
        return frozenset(self._held)

    @property
    def held_preview(self) -> str:
        return format_pressed_vks(self._held, preserve_modifier_sides=True)

    @property
    def candidate(self) -> Shortcut | None:
        return self._candidate

    @property
    def explanation(self) -> str | None:
        return self._explanation

    @property
    def awaiting_release(self) -> bool:
        return self._awaiting_release

    @property
    def terminal(self) -> bool:
        return self._candidate is not None

    def consume(self, event: KeyEvent) -> None:
        if self._candidate is not None:
            self._update_held(event)
            return

        if event.is_keydown:
            self._consume_down(event)
        else:
            self._consume_up(event)

    def _update_held(self, event: KeyEvent) -> None:
        if event.is_keydown:
            self._held.add(int(event.vk))
        else:
            self._held.discard(int(event.vk))

    def _consume_down(self, event: KeyEvent) -> None:
        vk = int(event.vk)
        if self._invalid and not self._awaiting_release and not self._held:
            self._invalid = False
            self._attempt_started = False
            self._explanation = None
            self._tap_tracker = None
        repeat = vk in self._held
        held_before = set(self._held)
        if not repeat:
            self._held.add(vk)

        if self._awaiting_release:
            return
        if repeat:
            if self._tap_tracker is not None:
                self._tap_tracker.observe_down(vk, held_before, repeat=True)
            return

        if not self._attempt_started:
            self._attempt_started = True
            self._invalid = False
            self._explanation = None
            self._tap_tracker = None

        family = modifier_family_for_vk(vk)
        if family is not None:
            if not event.is_resolved_modifier:
                self._reject_ambiguous_modifier(family.value)
                return
            if self._tap_tracker is None:
                self._tap_tracker = ModifierTapTracker(vk)
            self._tap_tracker.observe_down(vk, held_before, repeat=False)
            return

        try:
            self._candidate = shortcut_from_pressed_vks(
                self._held,
                trigger_vk=vk,
            )
        except ShortcutError as error:
            if vk not in VK_TO_NAME:
                self._reject(UNSUPPORTED_KEY_MESSAGE)
            else:
                self._reject(MODIFIER_ONLY_MESSAGE if "modifier" in str(error).casefold() else str(error))

    def _consume_up(self, event: KeyEvent) -> None:
        vk = int(event.vk)
        was_held = vk in self._held
        if was_held:
            self._held.remove(vk)

        if self._candidate is not None or self._awaiting_release:
            if self._awaiting_release and not self._held:
                self._awaiting_release = False
            return
        if not self._attempt_started or self._invalid:
            return

        eligible = False
        if self._tap_tracker is not None:
            eligible = self._tap_tracker.observe_up(vk)
        if eligible and event.is_resolved_modifier and not self._held:
            try:
                self._candidate = shortcut_from_pressed_vks({vk}, trigger_vk=vk)
            except ShortcutError as error:
                self._reject(str(error))
            return

        if not self._held and self._tap_tracker is not None:
            self._explanation = MODIFIER_ONLY_MESSAGE
            self._attempt_started = False

    def _reject_ambiguous_modifier(self, family: str) -> None:
        self._reject(
            f"Couldn't identify which {family} key was pressed. Try again."
        )

    def _reject(self, explanation: str) -> None:
        self._candidate = None
        self._invalid = True
        self._awaiting_release = True
        self._explanation = explanation

    def reset(self) -> None:
        self._held.clear()
        self._candidate = None
        self._explanation = None
        self._invalid = False
        self._attempt_started = False
        self._awaiting_release = False
        self._tap_tracker = None
