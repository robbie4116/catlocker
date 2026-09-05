# Expanded Shortcut Support Design

Date: 2026-09-05

Status: Behavior approved in conversation; written spec awaiting user review.

## Purpose and scope

Address tester feedback that letters record successfully while backtick, Right Alt, and Fn combinations briefly appear and disappear. The report establishes a recording problem, not that every non-letter key fails. Current code supports some non-letter triggers, omits punctuation, rejects modifier triggers, and silently catches recording validation errors.

This task is planning only. Implementation, tests, and physical keyboard verification belong in another task. No application code changes are authorized in this task.

The change includes side-specific modifiers, standalone modifier taps, punctuation, actionable recording feedback, and recording through the existing Windows keyboard hook. It does not include arbitrary sequences, multiple non-modifier triggers, or modifier-only chords containing multiple keys. A standalone shortcut is exactly one modifier key.

## Approved user behavior

### Modifier identity and labels

- Newly recorded modifiers preserve their actual side, both alone and in combinations.
- Display labels are LCtrl, RCtrl, LAlt, RAlt, LShift, RShift, LWin, and RWin.
- Display combinations with spaces around separators: `LCtrl + K` or `RCtrl + LShift + K`.
- Persist canonical tokens without requiring display spacing, for example `LCtrl+K`.
- Use deterministic ordering: Ctrl, Alt, Shift, Win; left before right within a family; trigger last.
- `LCtrl+K` requires LCtrl and does not match RCtrl+K or both Ctrl keys plus K. Additional modifier sides disqualify an exact new combination.
- As with existing behavior, this is modifier-plus-trigger matching, not a new arbitrary chord language. Unrelated held non-modifier keys do not change ordinary runtime trigger matching. Recording continues to reject multiple non-modifier triggers in one attempt.

### Existing settings

- Existing generic shortcuts such as `Ctrl+K` retain their current either-side matching behavior and remain visibly generic (`Ctrl + K`). Do not rewrite them on load, startup, or an unrelated settings save.
- Recording a replacement always produces explicit sides.
- Keep existing function, navigation, media, number, and letter shortcuts working.
- Generic standalone modifiers such as `Ctrl` are not a migration case and are invalid: a new standalone modifier must identify its side.
- The parser may support generic and specific requirements from different modifier families together; reject a generic family combined with a specific member of the same family as ambiguous. Exact requirements within a family must match the required physical sides; generic families retain either-or-both legacy semantics.

### Standalone activation

- Pressing the configured modifier does not toggle immediately.
- Release toggles once only if the modifier was pressed with no other key already held and no other key was pressed at any point during the hold.
- Another key disqualifies the entire hold even if it is released before the configured modifier. Autorepeat of the configured modifier does not disqualify it or toggle repeatedly.
- A release without a corresponding eligible press never toggles.
- These rules apply to both locking and unlocking. While locked, blocked key events still count when deciding whether a modifier was used alone.
- Entering/exiting recording, replacing the shortcut, an explicit lock-state command, emergency unlock, or engine recovery invalidates pending standalone activation. Require a fresh press afterward.
- Existing modifier-plus-trigger shortcuts continue toggling on trigger keydown, with existing repeat protection.

### Recording interaction

1. Recording is available only while unlocked. Start a fresh recording session and focus the existing readonly shortcut display.
2. Show held keys with their actual side. During recording, normal shortcut toggling is suspended; emergency recovery remains available. The saved shortcut is unchanged and resumes after recording ends.
3. One valid non-modifier trigger completes a combination on keydown. A standalone modifier completes on its eligible release, using the same alone rules as runtime activation.
4. Completed candidates remain visible, and Save becomes available. A preview of an unfinished or rejected attempt cannot be saved.
5. Invalid attempts show an inline explanation. Wait until all keys from that attempt are released before accepting a fresh attempt, so releasing an invalid chord cannot accidentally accept its remaining modifier.
6. Keep the rejection explanation visible after release until a new attempt begins or recording ends. Examples: “This key isn't supported. Try another key.” and “Use one trigger key, or tap a single modifier.”
7. An ambiguous modifier is not labeled Left by default. Show “Couldn't identify which Ctrl key was pressed. Try again.” (with the relevant family), reject that attempt, and leave Save disabled.
8. Focus loss during recording, Cancel, or closing Settings ends capture and restores the accepted shortcut. A completed unsaved candidate survives ordinary focus changes, as it does today; Cancel/close discards it.
9. Save validates, applies, and persists the candidate using the existing rollback behavior. Failure retains the prior saved shortcut or follows existing fail-open handling if rollback fails.

### Punctuation and Fn

- Add the standard punctuation virtual-key group: backtick, minus, equals, brackets, backslash, semicolon, apostrophe, comma, period, slash, and the additional OEM-102 key when present.
- Match the Windows key identity rather than characters produced by text entry. Shifted punctuation is represented by the appropriate side-specific Shift plus that key.
- Persist stable, delimiter-safe tokens, using the Windows OEM key identity (for example `OEM_3`) rather than a literal plus sign or a layout-dependent character. Generate readable display labels separately; on a US layout OEM_3 displays as Backtick. Use a readable generic OEM label when a reliable layout label is unavailable.
- Labels must not claim the same symbol exists on every keyboard layout. Changing layouts does not rewrite persisted shortcuts.
- Fn itself is not promised as a separate modifier. If a Fn combination yields a supported key event, record and name that resulting key, such as VolumeUp. If it yields no event, keep waiting; do not pretend an invisible key was detected or rejected.
- Include a short recording hint: “Some Fn combinations may not be detected. Try another key if nothing appears.” Physical-device verification must establish behavior on the tester's keyboard.

## Architecture

### Shared key identity and shortcut rules (`hotkeys.py`)

Extend the shortcut representation to express generic legacy modifier families, exact modifier sides, and standalone modifier activation. Keep parsing, serialization, display formatting, validation, and matching separate so visible spaces and localized punctuation labels do not affect persistence.

Keep the standalone eligibility state explicit (armed versus disqualified), with ordered press/release processing. Share the eligibility rules with recording, through a small pure helper if useful. Do not independently redefine “alone” in the UI and engine.

Windows secure-shortcut validation and existing system-shortcut warnings must compare modifier families even for side-specific inputs. New labels must not bypass existing validation. Left Ctrl + Right Ctrl remains emergency unlock and cannot become a user-configured modifier-only chord.

### Native normalization and recording transport (`keyboard_hook.py`, `controller.py`)

The existing hook becomes the authoritative keyboard source for both recording and activation. Its native structure already receives virtual key, scan code, and flags; the current conversion drops scan-code and extended-key detail. Preserve the metadata needed for normalization and verification.

Use explicit side-specific virtual keys directly. Resolve generic native modifier events only when scan code and flags identify the side unambiguously. An unresolved event stays unresolved and invalidates a recording attempt; do not synthesize a left-side identity. Use one normalizer for matching and recording.

Begin recording through an acknowledged engine command. Allocate a session identifier and initialize the recorder with the engine's currently held keys. Keys held before recording starts must be released before a new attempt can complete.

Use a dedicated bounded FIFO recording-event channel exposed by the controller; do not consume the application lifecycle event queue from Settings. Include session identifiers on events. UI polling drains only the active session and discards stale records. Publish recording events only while a session is active. Do not store keyboard history or log typed input.

Hook callback work must stay bounded and nonblocking: normalize, update input state, and enqueue minimal events. Never call Tk, display an error, wait for the UI, persist settings, or perform layout-label lookups inside the callback. If the recording channel overflows, latch a session error and cancel recording through the normal UI path; never silently process an incomplete event sequence into a shortcut. Expose the latched error outside the full queue so it cannot be lost.

Recording cancellation, completion, focus loss, window disposal, engine failure, and a lock command all stop forwarding and invalidate the session. Delayed queued events cannot repopulate a closed or restarted recorder. Resume ordinary matching only with pending standalone activation cleared.

### Settings presentation (`settings_window.py`)

Replace Tk key events as the source of recorded key identity with the controller's recording stream. Tk still owns focus, window controls, readonly display, polling, and cancellation. Prevent Tk handlers from double-processing native events. Remove the generic-to-left recording fallback.

Keep record state, valid candidate, and inline explanation distinct. The display is presentation; Save operates on the canonical candidate, not a prettified label. If file size warrants it, extract a focused pure recorder module rather than moving unrelated Settings code.

## Input delivery and recovery

Preserve the existing passed-down/suppressed-down bookkeeping: a keyup must pair correctly with what applications received on keydown, including when release causes a toggle. A suppressed down while locked must not leak an unmatched up after unlocking.

When unlocked, retain normal modifier delivery so other applications can use modifier combinations. Standalone Alt/Win taps may therefore retain native menu/Start behavior; this change does not introduce buffering/replaying input or guarantee suppression of those native actions. Document and physically verify this interaction. If that proves unacceptable, report it as a product follow-up rather than silently adding input replay.

Emergency Left Ctrl + Right Ctrl has precedence. It disqualifies any pending standalone Ctrl tap, and releasing either Ctrl afterward cannot relock. Existing fail-open behavior and mouse-accessible unlock remain intact.

AltGr and synthesized modifier events require physical Windows validation: verify what the hook reports and never silently treat an ambiguous sequence as an ordinary RAlt tap. Preserve existing synthetic F24 sender support; do not solve modifier identification by globally discarding injected input. If a keyboard emits extra Ctrl events for AltGr, the ordinary alone rule may reject standalone activation; report that limitation explicitly rather than claiming universal RAlt support.

## Acceptance and verification for the implementation task

- Pure tests for all eight standalone modifiers, wrong-side combinations, both sides held, legacy matching, stable ordering, punctuation round trips, and validation of side-specific system shortcuts.
- State-machine tests for repeats, prior-held keys, an intervening key released early, keyup without keydown, lock/unlock symmetry, emergency priority, and cancellation/replacement while held.
- Native adapter tests for explicit sides, generic keys with resolvable metadata, ambiguity, system key messages, and preserved synthetic F24 behavior.
- Recorder tests for keydown completion versus standalone keyup completion, invalid-attempt release/reset, sticky explanations, no silent side fallback, Save gating, and current focus/cancel behavior.
- Integration tests for session initialization, queue overflow, stale events, repeated start/cancel, window close, engine failure, and independent lifecycle-event delivery.
- Settings tests for legacy load/save stability, new shortcut persistence/reload, and failed-save rollback.
- Run the existing complete Python suite after targeted checks. Do not claim tests or hardware validation passed in this planning task.
- Physically record, save, restart, lock, and unlock with left/right Ctrl, Alt, Shift, Win, punctuation, and Fn-result keys on Windows. Verify native Alt/Win effects, AltGr on an applicable layout, another keyboard layout, emergency recovery, and no stuck keys in another application.
- Record tester build/version and keyboard/layout with the retest. Hardware cases that cannot be exercised remain explicitly pending.

## Reference

Windows keyboard-event metadata: https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-kbdllhookstruct

This spec supersedes modifier-family collapsing, non-modifier-only completion, and silent invalid-input behavior in the earlier recorder design. Other existing readonly display, focus, persistence, and recovery behavior remains applicable.
