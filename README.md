# SchemeScout

SchemeScout helps startups review government schemes and incubator programmes
using deterministic eligibility rules, source citations, and applicant-provided
profile data.

**Hackathon:** TBD

**Team:** Om Pandey — role TBD (name taken from the repository commit history)

**LLM provider / model:** TBD

## Repository

Clone the repository:

`git clone <copilot-ref kind="repo" target-id="https://github.com/ompandey0211/schemescouts" label="ompandey0211/schemescouts" />`

```text
cd schemescouts
```

## Run locally

Use Python 3.11, create a virtual environment, install dependencies, and launch
Streamlit:

```text
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run app/streamlit_app.py
```

Run the tests with `python -m pytest`.

## Deploy on Render

1. Push this repository to GitHub and create a new **Blueprint** in Render,
   selecting the repository and branch to deploy.
2. Render reads `render.yaml` and creates the free Python web service. The
   service installs the pinned dependencies and starts Streamlit on Render's
   assigned `$PORT`.
3. In the service environment settings, provide `LLM_PROVIDER` as the base
   URL of an OpenAI-compatible chat-completions API, `LLM_MODEL` as the model
   identifier, and `LLM_API_KEY` as a secret. These values are not stored in
   `render.yaml`. The corresponding legacy `SCHEMESCOUT_LLM_*` variables are
   also supported. Without provider and model settings, the app still starts;
   optional extraction and translation report that configuration is needed.
4. Redeploy after changing environment settings.

An optional local `.env` file is read when present and is ignored by Git.
Uploaded documents and extracted-rule caches are written only to temporary
directories; temporary caches may be lost when Render restarts the service.
Persistent programme documents and profile data must be committed to the
repository. This starter repository includes sample profiles, but no official
scheme PDFs, scheme definitions, or cached rule files, so it currently has no
scheme matches to show.

## Built with GitHub Copilot

The repository history records Copilot App as a co-author on the two
implementation commits. The exact Copilot mode and usage metrics are not
recorded in the repository.

| Metric | Result |
| --- | --- |
| Pytest passed / total | 182 / 182 (100%) |
| Estimated time saved | TBD |
| Copilot suggestions accepted / reviewed | TBD |

## Features and roadmap

- [x] Load and validate startup profiles and JSON scheme definitions.
- [x] Extract eligibility rules from scheme PDFs and retain only rules whose
  exact quoted clause is present on the cited page.
- [x] Evaluate cited eligibility rules deterministically; missing profile
  values or citations result in `unknown`.
- [x] Filter and rank schemes by sector, stage, and state targeting.
- [x] Show incubator and accelerator matches with deterministic 0–100 fit
  scores, reasons, eligibility results, and verified source clauses.
- [x] Analyze uploaded profile PDFs and let the user review and confirm
  extracted profile fields.
- [x] Show unmet and unknown requirements, supporting-document checklists, and
  dated action plans.
- [x] Download application drafts and plans as Markdown; download plans as PDF.
- [x] Offer optional Hindi translations through the configured LLM provider.
- [ ] Add official incubator and accelerator programme PDFs and definitions.
- [ ] Add reviewed official scheme definitions and source PDFs.

The checked matching and evaluation features are implemented, but no official
scheme or incubator programme documents are currently included. Until those
sources and their reviewed definitions are supplied, the app reports no
available matches rather than inventing programmes, eligibility rules, or
citations.

## Data and source requirements

Three example startup profiles are provided in `data/profiles`. Scheme source
PDFs and their JSON definitions belong in `data/schemes`. Incubator and
accelerator source PDFs and definitions belong in `data/incubators`. Each
definition uses `id`, `name`, and optional `sectors`, `stages`, and `states`
targeting lists. Incubator definitions must also set `"kind": "incubator"`;
existing scheme definitions default to `"kind": "scheme"`.

Add programmes only after reviewing their official source. Every eligibility
rule must cite its source document, page number, and exact clause. Do not infer
requirements or fill missing profile values. The deterministic incubator
score weights are defined in `src/matching.py`: sector, stage, and location
matches each contribute 20 points, and the share of extracted eligibility
rules met contributes up to 40 points. Missing profile values do not match a
restricted target; no extracted rules contribute no eligibility points.

## Configure an LLM provider

The app uses an OpenAI-compatible chat-completions provider for extracting
rules from PDF text and optional Hindi translations. The provider and model
used for this project have not been identified and are marked TBD above. Set
the following environment variables in a local `.env` file (or configure the
Render variables above) before using these features:

```text
SCHEMESCOUT_LLM_BASE_URL=TBD
SCHEMESCOUT_LLM_MODEL=TBD
SCHEMESCOUT_LLM_API_KEY=TBD
```

The API key is optional for providers that do not require authentication. Do
not commit real API keys. Verified extraction caches are written under the
system temporary directory and refreshed when source page text changes.
Conditions that cannot be mapped to a profile field are retained for manual
review with their source citations.

## Application planning and drafts

The planning pipeline ranks unmet and unknown requirements, maps cited
profile-field rules to supporting documents, and creates dated, prioritized
actions that retain their rule IDs and source clauses. If an LLM provider is
configured, it may rephrase action text; dates, priorities, requirements, and
citations remain code-controlled. Application drafts use only supplied profile
values and mark missing information for review.

## Hindi output

Choose **हिन्दी** from the output-language selector to translate verdict
summaries, gap explanations, checklist instructions, and action steps. Hindi
translation requires the configured LLM provider. If configuration is absent
or a translation fails, the app reports the problem and displays the original
English. Citations, quoted clauses, document and scheme names, page numbers,
dates, numeric values, and protected official terms are not translated. The
Markdown download preserves Hindi; the built-in PDF font is Latin-only, so
PDF plan downloads are English.

## Architecture

```text
.
├── .github/
│   ├── agents/                 # Copilot agent profile
│   ├── copilot-instructions.md # Repository-specific coding instructions
│   └── workflows/              # GitHub Actions checks
├── .gitignore
├── app/
│   └── streamlit_app.py        # Streamlit user interface
├── data/
│   ├── incubators/
│   │   └── .gitkeep            # Official programme data not yet supplied
│   ├── profiles/               # Three example startup profiles
│   └── schemes/
│       └── .gitkeep            # Official scheme data not yet supplied
├── scripts/
│   └── .gitkeep                # Reserved; no helper scripts committed yet
├── src/
│   ├── __init__.py
│   ├── agent.py                # End-to-end scheme processing pipeline
│   ├── doc_analysis.py         # Uploaded-PDF profile extraction and review
│   ├── draft.py                # Application draft generation
│   ├── evaluate.py             # Deterministic, citation-backed rule evaluation
│   ├── extract.py              # PDF text rule extraction and citation verification
│   ├── ingest.py               # Profile, definition, and PDF loading
│   ├── matching.py             # Deterministic incubator fit scores and ranking
│   ├── models.py               # Pydantic data models
│   ├── plan.py                 # Gaps, checklist, and dated action planning
│   └── translate.py            # Optional translation with protected citations
├── tests/                      # Pytest unit and application tests
│   └── __init__.py
├── LICENSE
├── PROMPTS.md
├── README.md
└── requirements.txt
```

## Disclaimer

SchemeScout provides guidance only, not legal or financial advice. Review
official programme documents and consult the relevant authority before acting.
