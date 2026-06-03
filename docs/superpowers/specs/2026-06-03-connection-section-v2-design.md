# Connection Section v2 — Design

**Date:** 2026-06-03
**Status:** Approved (pending user review of this written spec)
**Scope:** GUI Settings dialog (Connection section), Config schema, related backends

## Problem

The current Connection section has three modes (`API key`, `Custom endpoint`, `Subscription`) that look like the same thing in different skins. The user has to make two decisions (mode + provider) when one would do. The Models field is a plain `QLineEdit` in the Behaviour section, so the user can type any string — no discovery of what's actually available at their endpoint. The Subscription panel has a binary path field that is almost never needed.

The user has asked for:
1. A single "Connect with…" choice.
2. A model list that appears after a successful connection, with a custom-add option.
3. Removal of the parts of Behaviour we don't need.
4. The binary path field removed; auto-detect only.

## Goals

- **One decision up front.** User picks how they want to connect, the form adapts.
- **Model discovery after connect.** A list of available models replaces the free-text input.
- **Less surface area.** Drop `Auto-run` and the entire `Advanced` collapsible from the dialog.
- **Auto-detect binaries.** No more path field. `_resolve_binary` already does the work.

## Non-goals

- Persisting user-added custom model names across sessions (YAGNI).
- Exposing a `/v1/models`-equivalent for the CLI option (most CLIs don't expose one; the CLI's default model is the only one we surface).
- Replacing the `QStackedWidget` infrastructure in the rest of the dialog (it's only used in this one section).
- Migrating the auto-run / timeout configs to a separate "Advanced…" menu item (deferred).

## User-facing structure

```
┌─ Connection ────────────────────────────────────────┐
│ Connect with: [ Custom ▾ ]                          │  ← single dropdown, 4 options
│                                                     │
│ ── form panel (swaps fields by choice) ──          │
│   Custom:     [Base URL]   [Wire format ▾]         │
│               [API key]                            │
│   API key:    [Provider ▾]   [API key]              │
│   CLI:        [Agent ▾]   [✓ Logged in] [Login]    │
│   Browser:    [Provider ▾]   [Login with Browser]  │
│                                                     │
│ ── Models (appears once connected) ──              │
│   [claude-opus-4-8] [claude-sonnet-4-5] [...]      │  ← buttons
│   [+ Add custom model]                              │
│   When clicked: [____________]   [✓]                │
│                                                     │
│ Selected model: claude-opus-4-8                     │
└─────────────────────────────────────────────────────┘
┌─ Behaviour (trimmed) ──────────────────────────────┐
│ System prompt: [____________________________]       │
│ Model: claude-opus-4-8 (read-only echo)             │
└─────────────────────────────────────────────────────┘
```

**Connect with options** (in this exact order):
1. **Custom** — base URL + API key + OpenAI-compatible / Anthropic-compatible.
2. **API key** — built-in provider dropdown (Anthropic, OpenAI, Groq, …) + key.
3. **Installed CLI** — agent dropdown (Claude Code, OpenCode, Codex, Gemini CLI) + login status + browser login button. No binary path field.
4. **Browser login** — provider dropdown + Login button. Opens the provider's hosted login/key page via `QDesktopServices.openUrl()`. After returning, status shows `Logged in` or `Not logged in`; if not logged in, a `Paste API key` field appears so the user can fall back to pasting a key.

The "connection is established" trigger differs per option (see Data flow) but the effect is the same: a Models section fades in below the form.

**SDK compatibility** in Custom is a 2-option dropdown (OpenAI-compatible | Anthropic-compatible) — kept from today's `custom_format` field.

**Behaviour section after this change**: keeps System prompt + a read-only `Model` echo (`QLineEdit.setReadOnly(True)`) that mirrors whatever the user picked in the Connection section. The only place to *change* the model is the Connection section's Models list; the Behaviour echo is purely informational.

## Data flow

Each "Connect with" option has its own validity signal. The Models section becomes visible when the current option's signal is green.

| Option | Validity signal | How it's checked |
|---|---|---|
| **API key** | `provider_combo` ≠ empty AND `api_key_edit` is non-empty (except `ollama`) | local form check, debounced 250 ms after typing stops |
| **Custom** | `custom_url_edit` is non-empty (URL-shaped) AND `custom_key_edit` is non-empty (or local endpoint) | same local check |
| **Installed CLI** | `cli_agent_combo` ≠ empty AND `_resolve_binary(tool)` returns a path AND `backend.check_login()` returns `True` | already implemented in `backends/cli_backend.py:96-113`; reused |
| **Browser login** | `provider_combo` ≠ empty AND `QDesktopServices.openUrl(provider_key_url)` returns `True` AND the returned `check_login()` (or key-paste fallback) is non-empty | new: open URL → start a 2 s `QTimer.singleShot` poll loop, capped at 60 s |

**State machine for the dialog** (single source of truth):
```
CONNECTED ◀── trigger fires ── IDLE
CONNECTED ── trigger clears ── IDLE
IDLE      ── user picks a different option ── IDLE (with the new option's form)
```

A `_ConnectionController` lives next to the dialog and emits `state_changed(bool connected)`. The Models section listens.

**Model fetch trigger**: when state goes `IDLE → CONNECTED`, kick off `_fetch_models()` on a `QRunnable`+`QThreadPool`. The fetch uses:
```python
req = urllib.request.Request(
    f"{base.rstrip('/')}/v1/models",
    headers={"Authorization": f"Bearer {key}"},
)
```
with a 10 s timeout. The thread result is fed back via a `pyqtSignal`.

For **CLI** the model list is the CLI's *default* model only — show one button labeled with that default + an `+ Add custom model` button. (Known limitation; most CLIs don't expose `/v1/models`.)

## Models section UI

**Layout**:
```
Models
─────────────────────────────────────────────
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ claude-opus  │  │ claude-sonnet│  │ claude-haiku │
│   4-8        │  │   4-5        │  │   4-5        │
└──────────────┘  └──────────────┘  └──────────────┘
[ + Add custom model ]
```

Each button is a small card: model name on one line. The selected button has a 1 px accent border + accent text color. Clicking a different button switches the selection (radio behavior).

`+ Add custom model` button: when clicked, a `QLineEdit` + a small `✓` confirm button slide in below. On confirm: validate (non-empty, ≤ 80 chars, no spaces at edges), create a new card inline, mark it as the selection. Custom model names stay ephemeral for this session.

**Fetching display**:
- Idle: section hidden.
- Fetching: "Loading models…" label next to the section header.
- Empty result: header shows "Models (none found)" and the row is replaced by `No models found at this endpoint. Add one manually below.`
- Error (network/HTTP): header shows "Models (failed)" + the error text + a `Retry` button.

**Persistence**: the chosen model ID is written to `config.set("model", ...)` on Save. On reopen, we don't auto-fetch; we show the saved model as the pre-selected card and disable fetching until the user changes the connection.

## Error handling

Three classes of failure, three different treatments:

1. **Validation failures** (user typed something wrong but didn't try to save):
   - Empty base URL in Custom: no Models section, small `Enter a base URL` hint under the URL field. No popup.
   - Empty key in API key / Custom: same — no popup, just an inline hint.
   - Binary not found for chosen CLI: status reads `Binary not found` in red, Login button disabled, Models section stays hidden.
   - No provider picked in Browser login: Login button disabled.

2. **Action-time failures** (user clicked something and it failed):
   - Browser login failed to open URL: `QMessageBox.warning` "Could not open browser. Copy this URL and open it manually: {url}". Falls back to a `QLineEdit` populated with the URL.
   - CLI `check_login()` raises (broken install, sandboxed FS): status reads `Could not detect login state` in red, no crash.
   - Model fetch failed: section header shows `Models (failed)` + the error text + a `Retry` button. We don't pop up a message box.
   - Model fetch timed out (>10 s): same as above with a "timed out" label.

3. **Save failures** (rare — QSettings issues):
   - `QSettings.setValue` throws: `QMessageBox.critical` "Could not save settings: {error}", keep dialog open.

**Async-safety rules** (model fetch runs on a worker thread):
- Worker thread only touches `urllib` + a `QObject` via `pyqtSignal`. Never touch widgets directly.
- The QObject bridge lives on the main thread; its signal connects to a slot that updates widgets.
- A pending fetch is cancelled if the connection option changes mid-fetch (track a `_fetch_token` int, increment it; the worker checks the token before emitting, and drops stale results).
- Cancellable for the user: clicking a different connection option mid-fetch is the cancel.

**No silent failures**: every error state is shown somewhere visible. We never fail-open (showing a stale model list when the connection is bad).

## Configuration changes

**Config keys that change**:
- `connection_mode` is *replaced* by `connect_with` (values: `api_key`, `custom`, `cli`, `browser`).
- Migration in `Config.__init__()`: if `connect_with` is missing, read the old `connection_mode` and write the new key. Mapping: `api_key` → `api_key`, `custom` → `custom`, `subscription` → `cli`. The old key is left in place (QSettings is forgiving).
- `cli_path` is *removed* from the UI but kept as a config key for power users (set via env `AGENTICGIS_CLI_PATH` at startup, documented in README changelog). If `AGENTICGIS_CLI_PATH` is set, `_resolve_binary` consults it first.

**What gets removed from the dialog**:
- `auto_run_cb` (SettingsDialog `settings_dialog.py:378-380`) and its row.
- `_CollapsibleSection("Advanced", ...)` block (`settings_dialog.py:386-407`) including `timeout_edit`, `proc_timeout_edit`, `poll_interval_edit`.
- `mode_combo` + `QStackedWidget` (`settings_dialog.py:344-356`) — replaced by the new `connect_with_combo` + dynamic form panel.
- `cli_path_edit` + Browse button + `path_row` (`settings_dialog.py:530-540`) — auto-detect only.
- The duplicate `custom_model_edit` field (`settings_dialog.py:496-498`) — its job is now done by the Models section.

**What stays in `config.py`**: `main_thread_timeout`, `processing_timeout`, `mcp_poll_interval`, `auto_run`. They're still valid defaults, just no longer exposed in the dialog. We can either leave them at hard-coded defaults or add a tiny `Advanced…` menu item later — out of scope for this change.

## File layout

```
gui/
  settings_dialog.py            ← rewrite the Connection section
  connection_controller.py      ← new: state machine + signal
  model_fetcher.py              ← new: QRunnable + urllib fetch
  model_card.py                 ← new: clickable model card widget
backends/
  login_urls.py                 ← new: provider_id → key URL map
  cli_backend.py                ← unchanged, reused
  providers.py                  ← add `login_url` field per provider
config.py                       ← add migration + connect_with key
tests/
  test_connection_controller.py ← new
  test_model_fetcher.py         ← new
  test_provider_registry.py     ← new
  test_config_migration.py      ← new
```

## Testing

**Unit tests**:

- `test_connection_controller.py`
  - `test_controller_state_idle_to_connected` — picking API key with a non-empty key transitions to `connected=True`.
  - `test_controller_state_clear` — clearing the key transitions back to `connected=False`.
  - `test_controller_ollama_no_key` — Ollama provider with empty key still reports `connected`.
  - `test_controller_cli_login_required` — CLI option with `check_login` returning `False` keeps `connected=False`.
  - `test_controller_switch_option_resets` — switching from API key to CLI resets the state.
  - `test_fetch_token_cancels_stale` — out-of-order fetch results are dropped.

- `test_model_fetcher.py`
  - `test_fetch_openai_format` — mocks `urllib` response, returns list of model IDs.
  - `test_fetch_handles_404` — endpoint doesn't support `/v1/models` → returns empty list (not error).
  - `test_fetch_handles_401` — bad key → returns error string.
  - `test_fetch_timeout` — hangs → raises after 10 s.
  - `test_fetch_normalizes_anthropic` — Anthropic returns `{"data": [{"id": "..."}]}`; ensure parser handles it.

- `test_provider_registry.py`
  - `test_login_url_per_provider` — each built-in provider has a non-empty `login_url`.
  - `test_login_url_https_only` — every login_url starts with `https://`.

- `test_config_migration.py`
  - `test_old_api_key_migrates` — `connection_mode=api_key` + `connect_with` missing → `connect_with=api_key`.
  - `test_old_subscription_migrates` — `connection_mode=subscription` → `connect_with=cli`.
  - `test_old_custom_migrates` — `connection_mode=custom` → `connect_with=custom`.
  - `test_no_op_when_connect_with_set` — if `connect_with` is already set, the migration is a no-op.

**Coverage target**: 90%+ on the new code (`gui/connection_controller.py`, `gui/model_fetcher.py`, `backends/login_urls.py`, `config.py` migration). Existing tests must still pass.

**Manual QA checklist**:
- [ ] Open dialog → pick Custom → paste URL + key → Models section appears, model list loads.
- [ ] Open dialog → pick API key → paste Anthropic key → Models section appears.
- [ ] Open dialog → pick CLI → not logged in → red status, Models section hidden, click Login → green status, Models section appears with one model.
- [ ] Open dialog → pick Browser login → click Login → browser opens, wait → status updates, Models section appears.
- [ ] Switch options mid-fetch → no stale models.
- [ ] Save → reopen → saved model is pre-selected, no fetch fires.
- [ ] Add a custom model → it appears, selecting it, save, reopen → re-selects (within session only — YAGNI on persistence).
- [ ] `auto_run` and timeouts still work at their default values (no GUI toggle).

## Open questions

None at this point. The CLI "default model only" limitation is a known, accepted constraint; everything else has been decided in conversation.
