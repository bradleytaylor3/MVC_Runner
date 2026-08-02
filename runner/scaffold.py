"""Mechanically expand a compact scaffold spec into a full work_docs/
batch -- no model, no LLM call, not even a splice: this only ever writes
work doc JSON files that a later `run` will apply deterministically via
exact_code.

Point of this: the real medtimingtracker/goblin_i18n corpora showed that
most of the manual effort in authoring a batch wasn't deciding *what* to
write (Claude already knew that), it was mechanically retyping the same
JSON shape a dozen times for near-identical edits (a DAO method per query,
an entity per field, ...). A scaffold spec factors that out: one shared
task_template plus a short list of per-item variables, expanded here into
N validated work docs instead of N hand-authored ones.

Placeholders use {{double_braces}}, not str.format's {single_braces} --
a template field commonly *is* source code, and Kotlin/Java/JS/etc. all use
single braces for real block syntax that str.format would misread as a
substitution."""

import argparse
import json
import re
import sys
from pathlib import Path

from runner.work_doc import InitTask, WorkDocError, WorkTask

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class ScaffoldError(ValueError):
    pass


def _render(value, item: dict, item_label: str):
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in item:
                raise ScaffoldError(f"{item_label}: template references {{{{{key}}}}}, but this item has no {key!r}")
            return str(item[key])
        return _PLACEHOLDER_RE.sub(sub, value)
    if isinstance(value, list):
        return [_render(v, item, item_label) for v in value]
    return value


def expand(spec: dict) -> tuple[dict, list[dict]]:
    """Returns (init_doc_dict, [work_doc_dict, ...]), fully rendered but not
    yet validated against WorkTask.from_dict (see write_batch)."""
    missing = [f for f in ("batch_id", "repo_root", "language", "task_template", "items") if f not in spec]
    if missing:
        raise ScaffoldError(f"scaffold spec missing required field(s): {', '.join(missing)}")
    if not isinstance(spec["items"], list) or not spec["items"]:
        raise ScaffoldError("'items' must be a non-empty list")

    init_doc = {
        "kind": "init",
        "batch_id": spec["batch_id"],
        "repo_root": spec["repo_root"],
        "language": spec["language"],
        "conventions": spec.get("conventions", []),
    }

    template = spec["task_template"]
    id_prefix = spec.get("id_prefix", "task")
    work_docs = []
    for i, item in enumerate(spec["items"], start=1):
        if not isinstance(item, dict):
            raise ScaffoldError(f"items[{i - 1}] must be an object of template variables, got {item!r}")
        task_id = f"{id_prefix}-{i:03d}"
        item_label = f"items[{i - 1}] ({task_id})"
        doc = {"kind": "work", "id": task_id}
        for key, value in template.items():
            doc[key] = _render(value, item, item_label)
        work_docs.append(doc)

    return init_doc, work_docs


def write_batch(spec: dict, work_dir: Path, force: bool = False) -> list[Path]:
    existing = list(work_dir.glob("*.json")) if work_dir.is_dir() else []
    if existing and not force:
        raise ScaffoldError(
            f"{work_dir} already has {len(existing)} *.json file(s) — pass force=True/--force to overwrite, "
            "or point --work-dir at an empty directory"
        )

    init_doc, work_docs = expand(spec)

    init_path = work_dir / "000-init.json"
    InitTask.from_dict(init_doc, init_path)  # validate everything before writing anything

    rendered: list[tuple[Path, dict]] = []
    for doc in work_docs:
        path = work_dir / f"{doc['id']}.json"
        try:
            WorkTask.from_dict(doc, path)
        except WorkDocError as e:
            raise ScaffoldError(f"item {doc['id']} rendered an invalid task: {e}") from e
        rendered.append((path, doc))

    # force replaces the whole batch, not just same-named files -- a stray
    # leftover task-004.json from a larger previous batch would otherwise
    # still get picked up by a later `run` alongside this one.
    for stale in existing:
        stale.unlink()

    work_dir.mkdir(parents=True, exist_ok=True)
    written = [init_path]
    init_path.write_text(json.dumps(init_doc, indent=2) + "\n", encoding="utf-8")
    for path, doc in rendered:
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        written.append(path)

    return written


def cmd_scaffold(args: argparse.Namespace) -> int:
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: could not read spec {args.spec}: {e}", file=sys.stderr)
        return 2

    try:
        written = write_batch(spec, args.work_dir.resolve(), force=args.force)
    except (ScaffoldError, WorkDocError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    for path in written:
        print(f"Wrote {path}")
    print(f"\n{len(written) - 1} task(s) expanded from {args.spec}. "
          f"Run with: python -m runner.cli run --work-dir {args.work_dir}")
    return 0


def add_scaffold_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "scaffold",
        help="Mechanically expand a compact spec (shared template + item list) into a work_docs/ batch, no model call",
    )
    p.add_argument("spec", type=Path, help="Path to the scaffold spec JSON file")
    p.add_argument("--work-dir", type=Path, default=Path("work_docs"),
                    help="Directory to write the expanded batch to (default: work_docs/)")
    p.add_argument("--force", action="store_true",
                    help="Overwrite *.json files already present in --work-dir")
    p.set_defaults(func=cmd_scaffold)
