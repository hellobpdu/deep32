# Shortcuts for the test and lint commands in docs/BUILDING.md, and for a coverage run.
#
#   make test                     every test suite, through tests/run_all.py
#   make test ARGS='--only relay' with options for tests/run_all.py (--list, --only, --exclude, --without, --timeout)
#   make lint                     Ruff and Bandit, the way the Tests workflow runs them
#   make check                    the same as make test, by the usual name
#   make coverage                 every suite under coverage.py, with the reports in coverage-report/
#   make help                     this list
#
# This needs GNU make: the system's on Linux; on Windows, a GNU make run from Git for Windows' bash (Git for Windows
# does not include one). Variables, set on the command line, for example `make test PYTHON=python3.13`:
#
#   PYTHON            Python 3.12 or newer for the suites. Default: python3, or python on Windows. On Windows, give
#                     the full path if `python` opens the Microsoft Store.
#   RUFF, BANDIT      the linters. Default: ruff and bandit on PATH. .github/requirements-lint.txt pins both.
#   ARGS              extra options for tests/run_all.py, for test and coverage
#   D32_COVERAGE_DIR  where coverage writes its data and reports. Default: coverage-report
#
# coverage needs coverage.py for the same Python:
#   $(PYTHON) -m pip install --require-hashes -r .github/requirements-coverage.txt

ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif
RUFF ?= ruff
BANDIT ?= bandit
ARGS ?=
D32_COVERAGE_DIR ?= coverage-report
export D32_COVERAGE_DIR

COVERAGE = $(PYTHON) -I -B -m coverage
COVERAGE_RC = --rcfile=.github/coverage.rc

.DEFAULT_GOAL := help
.PHONY: help test lint check coverage

help:
	@echo 'make test       every test suite (tests/run_all.py); options with ARGS, for example ARGS="--only relay"'
	@echo 'make lint       Ruff and Bandit, as in the Tests workflow'
	@echo 'make check      the same as make test'
	@echo 'make coverage   every suite under coverage.py; reports in $(D32_COVERAGE_DIR)/'
	@echo 'Variables: PYTHON=$(PYTHON) RUFF=$(RUFF) BANDIT=$(BANDIT) ARGS=$(ARGS) D32_COVERAGE_DIR=$(D32_COVERAGE_DIR)'

test:
	$(PYTHON) -I -B tests/run_all.py $(ARGS)

# Bandit's baseline lists paths the way Bandit prints them on Linux and macOS (./src/...). On Windows Bandit prints
# .\src\..., so there it reads a copy of the baseline with the separators changed, and still reports only new findings.
lint:
	$(RUFF) check --no-cache .
ifeq ($(OS),Windows_NT)
	baseline=$$(mktemp) && \
	$(PYTHON) -I -c "import json, sys; d = json.load(open(sys.argv[1], encoding='utf-8')); [r.update(filename=r['filename'].replace(chr(47), chr(92))) for r in d['results']]; json.dump(d, open(sys.argv[2], 'w', encoding='utf-8'))" bandit-baseline.json "$$baseline" && \
	$(BANDIT) -c bandit.yaml -r . -b "$$baseline"; status=$$?; rm -f "$$baseline"; exit $$status
else
	$(BANDIT) -c bandit.yaml -r . -b bandit-baseline.json
endif

check: test

# Never fails on the percentage. It fails when a suite fails, after writing the reports.
coverage:
	@$(COVERAGE) --version >/dev/null 2>&1 || { echo 'coverage.py is missing for $(PYTHON): $(PYTHON) -m pip install --require-hashes -r .github/requirements-coverage.txt'; exit 2; }
	$(COVERAGE) erase $(COVERAGE_RC)
	$(COVERAGE) run $(COVERAGE_RC) tests/run_all.py --timeout 3600 $(ARGS); status=$$?; \
	$(COVERAGE) combine -q $(COVERAGE_RC) && \
	$(COVERAGE) html -q $(COVERAGE_RC) && \
	$(COVERAGE) xml -q $(COVERAGE_RC) && \
	$(COVERAGE) json -q $(COVERAGE_RC) && \
	$(PYTHON) -I -c "import json, os; t = json.load(open(os.path.join(os.environ['D32_COVERAGE_DIR'], 'coverage.json'), encoding='utf-8'))['totals']; print(f'Statements: {t['covered_lines']:,} of {t['num_statements']:,} ({t['percent_statements_covered']:.1f}%)'); print(f'Branches:   {t['covered_branches']:,} of {t['num_branches']:,} ({t['percent_branches_covered']:.1f}%)'); print(f'Together, as coverage.py counts them: {t['percent_covered']:.1f}%')" && \
	echo 'Reports: $(D32_COVERAGE_DIR)/html/index.html, coverage.xml and coverage.json' || status=$$?; \
	exit $$status
