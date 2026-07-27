# Work document schema

A work document is a single JSON file describing one self-contained task for
the local model to execute. This is the "minimum viable context" contract:
each task should carry exactly the context it needs and nothing more — a
cloud model doing the planning should scope these tightly rather than
dumping the whole repo into `instructions`.

```json
{
  "id": "task-001",
  "title": "Short human-readable description",
  "target_files": ["relative/path/to/file_to_edit.py"],
  "instructions": "Precise, self-contained instructions for what to change and why.",
  "acceptance_criteria": [
    "Concrete, checkable condition",
    "Another concrete, checkable condition"
  ],
  "context_files": ["optional/relative/path/for_reference_only.py"]
}
```

## Fields

- `id` (required) — unique task identifier; also used to sort execution order.
- `title` (required) — short description, shown in CLI progress output.
- `target_files` (required, non-empty list) — files the local model will
  rewrite in full. Paths are relative to `--repo-root`. A path that doesn't
  exist yet is treated as a new file.
- `instructions` (required) — everything the local model needs to know to
  make the change. Should be precise and self-contained; the local model has
  no access to the wider conversation or plan, only this document.
- `acceptance_criteria` (required, list) — concrete conditions the result
  should satisfy. Included in the prompt so the model has a checklist.
- `context_files` (optional, list) — files the local model should read for
  reference but must not rewrite (e.g. a shared interface or config it needs
  to stay consistent with).

## Notes for whoever authors these (e.g. a cloud model at the planning stage)

- One task = one JSON file in the work directory, e.g. `work_docs/task-001.json`.
- Keep `target_files` small — a handful of related files per task, not the
  whole codebase.
- Files listed in `context_files` are shown to the model but are never
  written to, no matter what the model outputs for them.
