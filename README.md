# Greenhouse AI Auto Job Applier 

This project automates job applications on **MyGreenhouse/Greenhouse-hosted boards** using Playwright.

It can:
- Search jobs by keyword
- Apply required filters (Date Posted, Work Type, Employment Type)
- Open job pages and click `Apply` / `Autofill with MyGreenhouse`
- Fill known profile fields from your config
- Auto-answer many custom questions from your answer bank
- Pause for manual review before final submit (optional)

You can use it in `--dry-run` mode to test end-to-end without submitting.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m job_bot.main --dry-run
```

## 📽️ See It In Action

Demo video: [Watch here](https://youtu.be/ziqVJKnnpzw)


## ✨ Contents

- [Introduction]
- [Quick Start]
- [See It In Action]
- [Features]
- [Project Structure]
- [Installation]
- [Configuration]
- [Usage]
- [Data Outputs]
- [Troubleshooting]
- [Contributor Notes]
- [Future Improvements]
- [Disclaimer]

## 🚀 Features

- Greenhouse dashboard login + security code prompt
- Search + location entry + filter flow
- Job card discovery and duplicate protection
- New-tab/same-tab application flow handling
- Resume + cover letter upload support
- Custom answer matching with memory (`custom_answer_memory.json`)
- Dry run and live run modes
- CSV logs and run summary dashboard

## 🗂️ Project Structure

job_bot_greenhouse/
├── core/
│   ├── browser.py
│   ├── form_filler.py
│   ├── logger.py
│   └── rate_limiter.py
├── platforms/
│   ├── base_platform.py
│   └── greenhouse.py
├── services/
│   ├── application_service.py
│   └── dashboard_service.py
├── job_bot/
│   ├── main.py
│   ├── requirements.txt
│   └── .env            # create this locally
├── data/
│   ├── user_data.json
│   ├── custom_answers.json
│   ├── custom_answer_memory.json
│   ├── jobs.csv
│   └── logs.csv
├── logs/
│   └── job_bot.log
└── screenshots/
```

## ⚙️ Installation

### 1) Prerequisites

- Python 3.10+
- Google Chrome installed
- macOS/Windows/Linux terminal access

### 2) Clone and enter project

```bash
git clone <your-repo-url>
cd job_bot_greenhouse
```

### 3) Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r job_bot/requirements.txt
```

### 4) Install Playwright browser

```bash
python -m playwright install chromium
```

## 🔧 Configuration

### A) `job_bot/.env` (secrets + file paths)

Create `job_bot/.env`:

```env
GREENHOUSE_EMAIL=you@example.com
RESUME_PATH=/absolute/path/to/resume.pdf
COVER_LETTER_PATH=/absolute/path/to/coverletter.pdf
```

### B) `data/user_data.json` (profile + run settings)

Create your private file from the template first:

```bash
cp data/user_data.example.json data/user_data.json
```

Fill personal data and run preferences:

- Name, email, phone
- LinkedIn/GitHub/portfolio
- City/state/zip
- Work authorization and sponsorship
- `search_keywords`
- `application_limit_per_run`
- Education block

### C) `data/custom_answers.json` (custom question bank)

Create your private file from the template first:

```bash
cp data/custom_answers.example.json data/custom_answers.json
```

Store question/answer mappings for application forms.

Best practice:
- Use explicit question-like keys
- Keep answers identical to dropdown option text when possible
- Add variants for recurring wording differences

### D) Optional memory file

`data/custom_answer_memory.json` stores learned answers from manual prompts.

## ▶️ Usage

### Dry run (recommended first)

```bash
python -m job_bot.main --dry-run
```

### Live run (real submits)

```bash
python -m job_bot.main
```

### Headless mode

```bash
python -m job_bot.main --headless
```

## 📁 Data Outputs

- `data/jobs.csv`: applied/simulated job history
- `data/logs.csv`: event-level logs
- `logs/job_bot.log`: detailed runtime logs
- `screenshots/`: pre/post submit snapshots

## 🛠️ Troubleshooting

### Module not found
Run from project root:

```bash
python -m job_bot.main --dry-run
```

### `No module named loguru`

```bash
pip install -r job_bot/requirements.txt
```

### Browser opens but actions fail
- Confirm selectors changed on site
- Re-run in non-headless mode
- Check `logs/job_bot.log`
- Add missing question mappings to `custom_answers.json`

### Resume/Cover letter not uploading
- Verify `RESUME_PATH` and `COVER_LETTER_PATH` are correct absolute paths
- Confirm file exists and is readable

## 🔮 Future Improvements

- Add support for additional job application platforms (for example LinkedIn Easy Apply, Lever, Workday, and company ATS pages).
- Integrate AI-powered resume tailoring per job posting so the resume is automatically optimized to the role before submission.
- Integrate AI-powered cover letter generation tailored to job description, required skills, and company context.
- Add optional ChatGPT API (or other LLM provider) support to answer custom application questions dynamically when no exact answer exists in `custom_answers.json`.
- Add answer-review safeguards so generated answers can be auto-approved in full automation mode or manually approved in review mode.
- Expand job-content extraction (description, requirements, company signals) to improve answer quality and relevance.

## ⚠️ Disclaimer

This project is for educational/personal automation use.

You are responsible for:
- Following Greenhouse and employer terms of service
- Respecting platform rate limits and anti-abuse policies
- Verifying all submitted information

Use at your own risk.
