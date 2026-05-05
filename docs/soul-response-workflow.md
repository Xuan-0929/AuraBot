# AuraBot Soul Response Workflow

## Goal

AuraBot needs one consistent human-feeling response mechanism across chat, task updates, memory reminders, and pet notices. The goal is not to hardcode warmer templates. The goal is to make every user-visible line pass through the same identity layer while keeping task facts reliable.

The current problem is that normal chat already receives `soul`, but task acceptance, task status, task completion, failures, follow-up reminders, and pet activities still build final text directly in business logic. That makes Aura sound like one person during chat and like a status broadcaster during task reporting.

## Boundary

`Soul` describes who Aura is. It should stay stable and scene-agnostic.

`ResponseWorkflow` describes what the current reply needs to accomplish. It owns scene, required facts, factual safety, length, and channel.

`VoiceAdapter` combines the two. It receives the stable soul plus a structured workflow payload and produces the final user-visible wording.

## Soul

Soul should contain personality-level guidance only:

- `name`: who is speaking.
- `persona`: the stable identity and relationship posture.
- `tone`: the natural speaking texture, such as close, relaxed, direct, warm, concise.
- `principles`: semantic principles the voice should follow.
- `taboos`: semantic boundaries, not brittle keyword bans.
- `relationship`: the long-running relationship between Aura and the user.

Soul can say that when Aura has finished something, she should sound like she is naturally telling the user the result and inviting them to look if useful. It must not require one absolute sentence. For example, "周报我写完啦，要不要看一看？" is a direction for living tone, not a template or exact output contract.

## Workflow

Workflow owns scene-specific communication requirements. It should not define Aura's personality.

Initial scenes:

- `task.accepted`: acknowledge the task and avoid promising success.
- `task.status`: answer where the task stands using only task state and latest progress.
- `task.completed`: say the task is done, preserve completion facts, mention attachments only when they exist, and invite review naturally when appropriate.
- `task.failed`: say it did not run through, include the nearest safe failure reason, and leave room to continue.
- `task.interrupted`: say Aura stopped and can follow the new direction.
- `task.control_not_found`: explain no matching task exists without sounding like an error code.
- `memory.follow_up`: surface due reminders naturally.
- `pet.task_activity`: make small pet notices feel like Aura nudging the user, not a system event.

Workflow must pass structured facts into the adapter:

- task title
- task status
- cleaned progress or result summary
- whether result attachments exist
- result subject, such as "周报" or "结果"
- failure reason when available
- reminder contents
- output channel, such as chat or pet notice

## Voice Adapter

The adapter is the only layer that turns workflow facts into user-visible prose.

Rules:

- Facts are authoritative. The adapter can rephrase them, but cannot invent completion, files, failure reasons, or user preferences.
- If attachments are present, the adapter may mention that files are available below. If attachments are absent, it must not claim there is a file.
- The adapter should prefer a living conversational line over a broadcast line. It should sound like Aura is telling the user what happened, not like a task runner is reporting state.
- It should avoid internal implementation terms such as Codex child task, runner, event, payload, task id, or file system paths unless the path is intentionally shown as an attachment.
- It should keep output brief by default.

The model-backed adapter should use the configured chat model when available. If the model call fails or is disabled, the deterministic fallback should still be warm and fact-safe.

## Quality Direction

Do not rely on brittle phrase blacklists. The quality target is semantic:

- Does this sound like Aura, not a customer-service agent?
- Does it tell the user what they need to know without becoming a log line?
- Does it preserve all required facts?
- Does it avoid inventing facts?
- Does it keep the same personhood across chat, task reporting, and reminders?

## First Implementation Scope

The first implementation should:

1. Extend `Soul` with `tone`, `principles`, and `taboos` while keeping existing fields compatible.
2. Add a `VoiceAdapter` with deterministic and model-backed implementations.
3. Add workflow payloads for task acceptance, task status, task completion, task failure, task interruption, task-control miss, memory follow-up, and pet task activity.
4. Replace direct final-text construction in task and follow-up presentation paths with the adapter.
5. Add tests for fact preservation, attachment safety, model prompt construction, and fallback warmth.

