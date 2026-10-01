# Built with GitHub Copilot

## How Copilot was used

The Git history contains two implementation commits with a `Co-authored-by:
Copilot App` trailer. The commits and current code verify the delivered
features, but do not establish whether the work used agent mode, the coding
agent, or code review.

- **Agent mode:** TBD; the commit history does not identify the interaction
  mode.
- **Coding agent:** TBD; co-authorship alone does not establish which agent
  workflow was used.
- **Code review:** TBD; no review activity is recorded in the checked commit
  history.
- **Custom instructions:** `.github/copilot-instructions.md` contains
  repository-specific Python, citation, missing-data, deterministic-eligibility,
  and testing rules. Whether they were active for each historical commit is
  TBD.

## Prompts used

Exact prompts are not stored in the git history. The summaries below distinguish
verifiable task evidence from unknown prompt text.

| Step | Prompt summary | Result |
| --- | --- | --- |
| Initial implementation (`8f8910e`, “Build SchemeScout startup eligibility assistant”) | TBD; the exact prompt is not in the repository history. | The current code includes a Streamlit app, Pydantic models, citation-verified PDF rule extraction, deterministic eligibility evaluation, planning, drafts, document analysis, translation, sample profiles, and tests. |
| Incubator matching (`3df036d`, “Add incubator matching”) | TBD; the exact prompt is not in the repository history. | Added the incubator kind, deterministic weighted fit scoring and ranking, incubator UI, and tests; the data directory remains empty until official programme sources are supplied. |
| Documentation and repository metadata (current task) | Fix README metadata and roadmap; document Copilot prompts; add MIT license; verify Copilot instructions; do not change `src/`, `app/`, or `tests/`. | README, `PROMPTS.md`, and `LICENSE` updated/added; existing Copilot instructions checked; pytest result recorded as 182/182. |

## Lessons learned

- Citation verification checks that a quoted clause exists on the cited PDF
  page before a rule can support an eligibility result.
- Deterministic rule evaluation can return `unknown` when the profile lacks
  required data or a citation is unavailable; missing information must not be
  guessed.
- Incubator ranking can be tested against the three sample profiles without
  representing synthetic test clauses as official programme requirements.
- No official incubator programme PDFs or definitions are present, so the
  matching UI must report the empty state rather than imply real-world
  programme matches.
- Exact prompts, Copilot interaction modes, code-review activity, and
  time-saved/acceptance metrics are not captured in the repository and remain
  TBD.
