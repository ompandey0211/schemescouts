# SchemeScout

SchemeScout is a starter app for evaluating startup profiles against deterministic, citation-backed government-scheme rules. Eligibility checks are code-based; missing profile data or missing citations produce an `unknown` result.

## Run locally

Use Python 3.11, create a virtual environment, install dependencies, and launch Streamlit:

```text
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run app/streamlit_app.py
```

Run the test suite with `python -m pytest`.

## Data

Three example startup profiles are provided in `data/profiles`. The `data/schemes` directory intentionally has no scheme definitions. Add a scheme only after reviewing its official source; each rule must include a citation with the source document, page number, and exact clause. Do not infer eligibility requirements or fill missing profile values.

Incubator and accelerator programme definitions belong in `data/incubators`.
That directory intentionally contains no fabricated programme documents.
When official material is available, add `<id>.pdf` and `<id>.json`; the JSON
uses the same targeting fields as schemes and must set `"kind": "incubator"`.
The app reuses the citation-verified PDF extraction and deterministic rule
evaluation pipeline, and shows no matches until definitions and source PDFs
are supplied. Fit scores are deterministic: `src.matching.FIT_SCORE_WEIGHTS`
assigns 20 points each to sector, stage, and location matches, and 40 points
to the share of extracted eligibility rules met. Missing profile values do
not match a restricted target; with no extracted rules, the eligibility
component receives no points. Every displayed eligibility result retains its
verified source clause and page.

## Extract rules from a scheme PDF

Configure an OpenAI-compatible chat-completions provider before calling
`src.extract.extract_rules`:

```powershell
$env:SCHEMESCOUT_LLM_BASE_URL = "https://provider.example/v1"
$env:SCHEMESCOUT_LLM_MODEL = "your-model"
$env:SCHEMESCOUT_LLM_API_KEY = "your-api-key"
```

The API key is optional for providers that do not require authentication.
Verified extractions are cached as `data/schemes/<scheme-id>.rules.json` and
refreshed when the source page text changes. Rules without an exact quoted
clause found on the cited page are discarded; conditions that cannot map to a
profile field are returned for manual review.

## Application planning

`src.plan.find_gaps` ranks unmet and unknown requirements, while
`src.plan.build_checklist` maps cited profile-field rules to supporting
documents and where to obtain them. `src.plan.make_plan` creates dated,
prioritized actions that retain their rule IDs and source clauses. When the
LLM provider above is configured, it is used only to rephrase each action;
dates, priorities, requirements, and citations remain code-controlled. An
optional `llm_call` argument can be passed for testing or custom phrasing.

## Run the agent pipeline

Add a scheme definition as `data/schemes/<scheme-id>.json` with `id`, `name`,
and optional `sectors`, `stages`, and `states` targeting lists. Put its official
source PDF at `data/schemes/<scheme-id>.pdf`. Empty targeting lists mean the
scheme is unrestricted for that dimension. Profiles may include a `stage`;
when a scheme targets stages and the profile stage is missing, it is not
selected. The agent extracts citation-verified rules into the adjacent
`<scheme-id>.rules.json` cache and returns a structured report:

```python
from src.agent import run_agent

report = run_agent("startup-001")
print(report.model_dump_json(indent=2))
```

Pipeline tool events are logged and included in `report.events`. Each
recoverable step is attempted at most three times; a scheme failure is
reported without hiding successful results for other schemes.

## Application draft

`src.draft.generate_draft(profile, scheme, results)` produces a Markdown
application draft using only fields supplied in the profile. Missing values
are shown as `[NEEDS INPUT]`; each eligibility rule includes its result and
source citation. The Streamlit scheme view displays the draft and provides a
Markdown download. Review all draft content before submitting; it is generated
guidance, not an official application.

## Hindi output

Choose **हिन्दी** from the Streamlit output-language selector to translate
verdict summaries, gap explanations, checklist instructions, and action steps.
This uses the configured OpenAI-compatible chat-completions provider described
above. If it is not configured or a translation fails, the app reports that
clearly and shows the original English. Citations, quoted clauses, document
and scheme names, page numbers, dates, numeric values, and official terms such
as DPIIT are protected from translation. UI text uses a Devanagari-capable
system-font fallback. Hindi output includes: “यह केवल मार्गदर्शन है, कानूनी या
वित्तीय सलाह नहीं।” The Markdown download preserves Hindi; because the built-in
PDF font is Latin-only, the PDF download remains English.

## Autofill from uploaded documents

The Streamlit profile view accepts PDF uploads for a DPIIT recognition
certificate, certificate of incorporation, and one-page financial summary.
After extraction, review and edit the values, source pages, and confidence in
the table; values are applied only when their **Confirm** box is selected.
Conflicting assertions from different documents are flagged and require
selecting at most one source. Fields that are not found remain empty. Uploaded
PDFs are processed using temporary files that are removed after analysis; the
app does not persist uploaded files after the session. Document extraction
requires the configured LLM provider above and is guidance for review, not
verification of the uploaded document.