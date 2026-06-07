# Elf Chat Graph

Memo Elf uses the same memory, retrieval, and tool nodes as page chat, but it is
not the same product surface. Page chat is an editable conversation. Elf chat is
a desktop runtime session with speech, bubbles, interruption, and recovery
state.

## Graph Split

The chat graph is split at the orchestration layer:

- `page` graph: used by `/api/conversations/{id}/chat/stream`.
- `elf` graph: used by `/api/elf/chat/stream` and elf resume endpoints.

Both graphs share the context pyramid, RAG, ReAct agent, tool execution, verify,
and persistence nodes. Only the elf graph registers the
`generate_elf_bubble_answer` node. This keeps the page graph debugger focused on
the page conversation path and removes the bubble-only node from normal chat
graph views.

## Runtime State

Elf state is persisted separately from the browser or desktop process. Locks and
SSE buffers are only process-local coordination helpers; they are not the source
of truth.

The backend owns a singleton `ElfRuntimeState` row with these state values:

- `idle`: no active elf turn.
- `thinking`: a turn is running before answer playback.
- `tool_running`: the elf turn is running tools.
- `streaming_answer`: answer or bubble tokens are being produced.
- `speaking`: answer text exists and the desktop client may still be playing it.
- `waiting_user_input`: the graph interrupted and needs a structured choice.
- `completed`: the last turn finished.
- `failed`: the last turn failed or was recovered as orphaned.
- `recovering`: the backend is reconciling stale runtime state.

The row records the active conversation, active turn, pending interrupt payload,
last message, last bubbles, and last error. Desktop refresh reads this state
first and decides how to recover the UI.

## Recovery Rules

When Memo Elf starts or refreshes, it calls `/api/elf/runtime/status`.

- `waiting_user_input`: show the choice panel again and say:
  `刚才我停在一个选择上，继续选一下我就能接着做。`
- `thinking`, `tool_running`, or `streaming_answer`: if the turn still has a live
  event buffer, the desktop client can continue polling/subscribing. If there is
  no live buffer, the backend marks the turn failed and moves the runtime state
  to `failed`.
- `speaking`: show the latest bubble/message as read-only recovery context.
- `failed`: unlock input and show the last error in a short bubble.
- `idle` or `completed`: normal idle behavior.

Only interrupted turns are genuinely resumable after a backend process restart.
Running LLM/tool work cannot be recreated after the worker process is gone; it
must be surfaced as failed instead of pretending to still be thinking.

## UI Rules

The elf conversation is hidden from the normal page conversation sidebar. Page
chat cannot send messages into the elf runtime. A page may expose a read-only
elf status/history panel for debugging, including current state, active turn,
messages, and graph views.

Deletion semantics also differ. Normal page turns can be deleted through page
chat controls. Elf runtime turns are controlled by the elf runtime state and are
not deleted through the page chat input surface.
