#!/usr/bin/env bash
# The cv-skills CLI, for the drafting agents (ids, build, page-check).
# `cv-skills install-workflow` writes the plugin root in.
exec uv run --project @CV_SKILLS_ROOT@ --frozen --quiet cv-skills "$@"
