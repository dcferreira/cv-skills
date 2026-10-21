#!/usr/bin/env bash
# The tailor workflow's step adapters (python -m cv_skills.steps): every check and every pawl
# outcome the workflow routes on. `cv-skills install-workflow` writes the plugin root in.
exec uv run --project @CV_SKILLS_ROOT@ --frozen --quiet python -m cv_skills.steps "$@"
