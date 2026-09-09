# X-GU.RU — Content & SEO Generator

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

Production content generation and SEO optimization suite for [x-gu.ru](https://x-gu.ru) marketplace platform.

## Features

- **Content Generator** — Dynamic HTML page generation using Jinja2 templates
- **SEO Health Check** — Site-wide SEO audit and validation
- **In-Place SEO Fixes** — Automated on-the-fly fixes for common SEO issues
- **Sitemap Generation** — Dynamic XML sitemaps for search engine indexing
- **Server Optimization** — Production deployment and optimization scripts

## Project Structure

```
.
├── content_generator.py          # Main content generation engine
├── seo_healthcheck.py            # SEO audit and health report
├── seo_inplace_fix.py            # Automated SEO fixes
├── seo_rebuild_broken.py         # Rebuild broken pages
├── seo_title_extend.py           # SEO title optimization
├── *_master.html.j2              # Jinja2 HTML templates
└── server-opt/                   # Server optimization & deployment
    ├── seo_report.py             # Detailed SEO reporting
    └── ...
```

## Architecture Note

⚠️ Several scripts (`content_generator.py`, `server-opt/seo_report.py`, etc.) import from an `app.*` package (`app.core.config`, `app.models.*`, `app.services.*`) that is the private backend application for x-gu.ru and is **not included in this repository**. This repo contains the content-generation and SEO tooling layer that runs alongside that backend — it is not a standalone, runnable-out-of-the-box project. It's shared for reference, portfolio, and collaboration purposes.

## Setup

### Prerequisites

- Python 3.9+
- PostgreSQL (or configured database)
- SQLAlchemy ORM

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/tashev11/x-gu-ru.git
   cd x-gu-ru
   ```

2. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your actual values
   ```

5. Prepare data files:
   ```bash
   # Place these in data/ directory:
   # - keywords_all.csv
   # - keywords_wave_2.csv  
   # - ru_cities_with_population.csv
   ```

## Usage

### Generate Content
```bash
python content_generator.py
```

### Run SEO Health Check
```bash
python seo_healthcheck.py
```

### Apply SEO Fixes
```bash
python seo_inplace_fix.py
```

### Rebuild Broken Pages
```bash
python seo_rebuild_broken.py
```

## Environment Variables

See `.env.example` for required configuration:
- `DATABASE_URL` — PostgreSQL connection string
- `API_KEY_*` — Third-party service credentials
- `SERVER_HOST`, `SERVER_PORT` — Deployment settings

## Data Files

The following CSV files are required in the `data/` directory:
- `keywords_all.csv` — Complete keyword list
- `keywords_wave_2.csv` — Secondary keyword batch
- `keywords_100.csv` — Top 100 keywords
- `ru_cities_with_population.csv` — Russian city reference data

## Security

⚠️ **Important**: Never commit `.env` files or sensitive data. Use `.env.example` as a template.

## Contributing

Issues and pull requests are welcome. For significant changes, please open an issue first to discuss what you'd like to change.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Author

Created for the [x-gu.ru](https://x-gu.ru) marketplace platform.
