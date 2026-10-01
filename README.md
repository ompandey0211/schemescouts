# 🧭 SchemeScout

**An AI agent that matches startups to official government schemes, checks eligibility clause by clause, and generates an actionable application plan.**

> Built with GitHub Copilot for the [Hackathon Name] · Track: AI Agent for Startup Schemes

[Demo Video](#) · [Live App](#) · [Slides](#)

---

## 🎯 The Problem

Indian startups have access to dozens of government schemes, but:
- Scheme documents are long, scattered, and written in legal language.
- Founders can't easily tell which schemes fit them or why they were rejected.
- Missing one document or condition can delay an application by months.

## 💡 The Solution

SchemeScout takes a startup profile and turns official scheme documents into a clear answer to three questions:

1. **Which schemes are relevant to me?**
2. **Am I eligible, and what is the proof?**
3. **What exactly should I do next?**

## ✨ Key Features

| Feature | Description |
|---|---|
| 🔍 Scheme matching | Shortlists relevant schemes by sector, stage, and state |
| ✅ Eligibility reasoning | Each rule is marked **met / not met / unknown** |
| 📌 Clause-level citations | Every decision shows the exact clause and page number from the official document |
| ⚠️ Gap detection | Lists missing requirements, ordered by how blocking they are |
| 📄 Document checklist | Shows what to prepare and where to get it |
| 🗓️ Action plan | A dated, prioritized plan to apply |

**Bonus features:** application draft generation · Hindi output · document upload to auto-fill the profile · incubator matching · multiple scheme sources

## 🧠 How It Works

```
Startup Profile
      ↓
Find Relevant Schemes
      ↓
Extract Rules from Official Documents (LLM + citations)
      ↓
Check Eligibility (deterministic Python)
      ↓
Detect Missing Requirements
      ↓
Document Checklist
      ↓
Action Plan
```

### 🔑 Key Design Principle: no hallucinated eligibility

Most tools paste a PDF into an LLM and ask "am I eligible?". SchemeScout does not.

| Step | Done by | Why |
|---|---|---|
| Read documents and extract rules | LLM | Good at understanding text |
| Verify each quote exists on the cited page | Python | Rejects fabricated clauses |
| Decide met / not met / unknown | **Python (deterministic)** | Same input always gives the same answer |
| Explain results and write the plan | LLM | Good at clear language, using only extracted clauses |

If profile data is missing, the answer is **unknown**, never a guess.

## 🏗️ Architecture

```
schemescout/
├── .github/
│   ├── copilot-instructions.md   # Rules that guide GitHub Copilot
│   ├── agents/schemescout.agent.md
│   └── workflows/ci.yml
├── data/
│   ├── schemes/                  # Official scheme documents
│   └── profiles/                 # Sample startup profiles
├── src/
│   ├── models.py                 # Profile, Rule, Citation, RuleResult, Scheme
│   ├── ingest.py                 # PDF to text, keeping page numbers
│   ├── extract.py                # LLM rule extraction + quote verification
│   ├── evaluate.py               # Deterministic eligibility engine
│   ├── plan.py                   # Gaps, checklist, action plan
│   └── agent.py                  # Orchestrates the full pipeline
├── app/
│   └── streamlit_app.py          # User interface
├── tests/
├── requirements.txt
└── README.md
```

## 🛠️ Tech Stack

- **Language:** Python 3.11
- **Data validation:** Pydantic v2
- **UI:** Streamlit
- **PDF parsing:** pypdf
- **Testing:** pytest
- **AI:** [Your LLM provider / model]
- **Development:** GitHub Copilot (agent mode, coding agent, code review)

## 🚀 Getting Started

### 1. Clone and install

```bash
git clone https://github.com/[your-username]/schemescout.git
cd schemescout
python -m venv .venv

# Windows: .venv\Scripts\Activate.ps1
# Mac/Linux: source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file:

```
LLM_PROVIDER=[your provider]
LLM_API_KEY=[your key]
```

### 3. Add official scheme documents

Download scheme PDFs from official sources (for example Startup India or SIDBI) into `data/schemes/`.

### 4. Run the app

```bash
streamlit run app/streamlit_app.py
```

### 5. Run the tests

```bash
pytest
```

## 📖 Usage

1. Choose a sample startup profile or fill in your own.
2. View the ranked list of relevant schemes.
3. Open a scheme to see each eligibility rule with ✅ / ❌ / ❓ and the quoted clause with page number.
4. Review missing requirements and the document checklist.
5. Download your dated action plan (and application draft).

## 🎬 Example

**Profile:** Early-stage agritech startup, Haryana, DPIIT recognized, incorporated 2 years ago.

| Scheme | Verdict | Missing |
|---|---|---|
| [Scheme A] | ✅ Eligible | None |
| [Scheme B] | ⚠️ Needs info | Turnover details |
| [Scheme C] | ❌ Not eligible | Incorporation age limit exceeded (Clause X, p. Y) |

## 🤖 Built with GitHub Copilot

GitHub Copilot was the development team for this project:

- **Agent mode (VS Code):** scaffolded the project and implemented the modules.
- **Copilot coding agent:** built features from GitHub issues and opened PRs.
- **Copilot code review:** reviewed every pull request.
- **Custom instructions:** `.github/copilot-instructions.md` enforced our rules (citations required, no guessing, deterministic eligibility).

**Prompts used and results:** see [`PROMPTS.md`](PROMPTS.md).

| Metric | Result |
|---|---|
| Code written with Copilot | [__%] |
| Time saved (estimate) | [__ hours] |
| Tests passing | [__ / __] |

## 🔒 Responsible AI

- Every eligibility conclusion is backed by a quoted clause and page number.
- Quotes are programmatically verified against the source text.
- Missing data produces "unknown", never an assumption.
- Uploaded documents are not stored after the session.
- Eligibility decisions are made by deterministic code, so they are reproducible and testable.

## 🗺️ Roadmap

- [x] Startup profile and scheme matching
- [x] Eligibility engine with citations
- [x] Gap detection, checklist, action plan
- [ ] Application draft generation
- [ ] Hindi and other Indic languages
- [ ] Document upload to auto-fill the profile
- [ ] Incubator matching
- [ ] State-level scheme coverage

## ⚠️ Disclaimer

SchemeScout provides guidance only. It is not legal or financial advice. Always verify eligibility and requirements with the official scheme authority before applying. Scheme terms change, so check the last verified date for each document.

## 👥 Team

| Name | Role |
|---|---|
| [Your Name] | [Role] |

## 📄 License

MIT License. See [LICENSE](LICENSE).
