from hotkeys import VK_LCONTROL, VK_LSHIFT
from modifier_tap import ModifierTapTracker


def test_clean_modifier_tap_is_eligible_on_release_only():
    tracker = ModifierTapTracker(VK_LCONTROL)

    assert tracker.observe_down(VK_LCONTROL, set(), repeat=False) is False
    assert tracker.observe_up(VK_LCONTROL) is True


def test_modifier_repeat_does_not_toggle_or_disqualify_tap():
    tracker = ModifierTapTracker(VK_LCONTROL)
    tracker.observe_down(VK_LCONTROL, set(), repeat=False)

    tracker.observe_down(VK_LCONTROL, {VK_LCONTROL}, repeat=True)

    assert tracker.observe_up(VK_LCONTROL) is True


def test_prior_held_key_disqualifies_modifier_tap():
    tracker = ModifierTapTracker(VK_LCONTROL)

    tracker.observe_down(VK_LCONTROL, {0x41}, repeat=False)

    assert tracker.observe_up(VK_LCONTROL) is False


def test_intervening_key_disqualifies_tap_even_if_released_first():
    tracker = ModifierTapTracker(VK_LCONTROL)
    tracker.observe_down(VK_LCONTROL, set(), repeat=False)
    tracker.observe_down(0x41, {VK_LCONTROL}, repeat=False)

    tracker.observe_up(0x41)

    assert tracker.observe_up(VK_LCONTROL) is False


def test_unmatched_release_and_reset_while_held_are_not_eligible():
    tracker = ModifierTapTracker(VK_LCONTROL)

    assert tracker.observe_up(VK_LCONTROL) is False
    tracker.observe_down(VK_LCONTROL, set(), repeat=False)
    tracker.reset()

    assert tracker.observe_up(VK_LCONTROL) is False


def test_switching_modifier_invalidates_pending_tap():
    tracker = ModifierTapTracker(VK_LCONTROL)
    tracker.observe_down(VK_LCONTROL, set(), repeat=False)

    tracker.set_modifier(VK_LSHIFT)

    assert tracker.observe_up(VK_LCONTROL) is False
