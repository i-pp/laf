# Execution Policies

These policies govern permission and escalation decisions. The operating principles are maintained in [`orchestration.md`](orchestration.md); agent responsibilities are maintained in the [agent role guide](https://github.com/i-pp/ai-orchestration/blob/main/agents/README.md).

## Permission boundaries

- Follow applicable system and platform restrictions, explicit user scope, and repository instructions. Lower-level policies do not grant permissions or expand authorization.
- Use tools only for their documented capabilities. If a required capability or permission is unavailable, report the limitation; do not route around it.
- Validate generated output before integration as specified in [`orchestration.md`](orchestration.md#work-flow).

## Risk classes

Classify an action by its likely impact, reversibility, target, and data exposure. Use the highest applicable class.

| Class | Examples | Action |
| --- | --- | --- |
| Routine | Read-only inspection; bounded, reversible local edits | Proceed within scope and available permissions; verify the result. |
| Controlled | Bounded external side effects or access to sensitive data | Confirm target and authorization. Proceed only when scope and authorization are explicit; verify the outcome. |
| High impact | Security or privacy changes; destructive or irreversible actions; production changes, publication, credential changes, or material cost | Stop before acting. Present the exact action, target, impact, and recovery options; obtain explicit authorization and satisfy any platform approval gate. |

An explicit request authorizes only the stated action and scope. Do not ask again for authorization already given; seek clarification when the target, impact, or allowed extent remains ambiguous.

## Escalation

Escalate when an action is high impact, required authorization is missing, a policy conflict affects the task, or a blocker prevents safe progress. State the decision needed, evidence, expected impact, and safe options. Apply the blocker and non-blocking issue handling in [`orchestration.md`](orchestration.md#operating-principles).
