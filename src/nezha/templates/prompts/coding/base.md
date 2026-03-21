## YOUR ROLE - {{role_name}}

You are working in workspace: {{workspace}}
Project: {{project_name}}

{{input_files}}

### TARGET TASK

Read `.dag_context.json` first. Work on the assigned task only.

The `.dag_context.json` contains:
- `target_feature` — task to implement (id, description, acceptance criteria)
- `target_feature.is_rework` — if true, this is a rework/fix task
- `target_feature.rework_note` — what went wrong (for rework tasks)
- `dag_status` — current state of all tasks
