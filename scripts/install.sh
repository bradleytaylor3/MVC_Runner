#!/usr/bin/env bash
# Registers the MVC_Runner Claude Code skills (code-edit, adb-test) globally
# by linking them into ~/.claude/skills/, and points MVC_RUNNER_HOME at this
# checkout so the skills can find work_docs/, logs/, etc. from any project.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skills_dir="$HOME/.claude/skills"

mkdir -p "$skills_dir"

for skill in code-edit adb-test; do
  link_path="$skills_dir/$skill"
  target="$repo_root/.claude/skills/$skill"

  if [ -e "$link_path" ] || [ -L "$link_path" ]; then
    if [ -L "$link_path" ] && [ "$(readlink "$link_path")" = "$target" ]; then
      echo "Skipping $skill (already linked to this repo)"
    else
      echo "Warning: $link_path already exists and isn't a link to this repo -- remove it manually and re-run if you want it replaced." >&2
    fi
    continue
  fi

  ln -s "$target" "$link_path"
  echo "Linked $skill -> $target"
done

case "${SHELL:-}" in
  */zsh) profile_file="$HOME/.zshrc" ;;
  */bash) profile_file="$HOME/.bashrc" ;;
  *) profile_file="$HOME/.profile" ;;
esac

export_line="export MVC_RUNNER_HOME=\"$repo_root\""
if ! grep -qF "$export_line" "$profile_file" 2>/dev/null; then
  printf '\n%s\n' "$export_line" >> "$profile_file"
  echo "Added MVC_RUNNER_HOME to $profile_file."
else
  echo "MVC_RUNNER_HOME already set in $profile_file."
fi
echo "Restart your terminal / Claude Code session for the skills and env var to take effect."
