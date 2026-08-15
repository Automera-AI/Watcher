# The API image (roadmap B3).
#
# Built from the repository root, not from this directory:
#
#     docker build -f apps/api/Dockerfile -t watcher-api .
#
# which is what `.github/workflows/cd.yml` already does — that job has been sitting behind an
# `if apps/api/Dockerfile exists` guard since it was written, and this file is what activates it.
# The path is therefore not a free choice; moving it silently disables the image build.
#
# Two stages, for one reason: `pip install` needs a compiler toolchain often enough (psycopg's
# binary wheel does not, today, and the next dependency might) and none of it belongs in the thing
# that faces the internet. The runtime stage copies a finished virtualenv and nothing else.
#
# The project is installed rather than merely copied, so the dependency set is the one declared in
# `pyproject.toml`. A Dockerfile with its own `pip install fastapi uvicorn …` line is a second
# dependency list that nobody updates, and it drifts in the direction of "works on my machine".

FROM python:3.13-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src

# Only what the install needs. Copying the whole tree here would rebuild the dependency layer on
# every documentation change.
COPY pyproject.toml README.md ./
COPY apps ./apps
COPY packages ./packages

RUN python -m venv /opt/venv && /opt/venv/bin/pip install .


FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# `PYTHONUNBUFFERED` is not cosmetic: a crash loop with a buffered stdout loses the traceback that
# explains it, which is the log line the first deploy is most likely to need.

WORKDIR /app

COPY --from=build /opt/venv /opt/venv

# The application, plus the migrations. Alembic is not part of the running process — nothing
# imports it — but `alembic upgrade head` has to be runnable *as this image*, against the same
# `DATABASE_URL`, or the schema is applied by a laptop with a different driver installed. That is
# how the pgbouncer prepared-statement problem in db/engine.py finds a deploy.
COPY apps ./apps
COPY packages ./packages
COPY alembic ./alembic
COPY alembic.ini ./

# Run as nobody. The process writes no files: `PYTHONDONTWRITEBYTECODE` above means it does not
# even write .pyc, so an unwritable filesystem costs nothing and removes a whole class of surprise.
USER 65534:65534

EXPOSE 8000

# `--factory` because `apps/api/main.py` deliberately has no module-level `app`: importing it must
# not read the environment or open an engine (see that module's docstring). Shell form, because
# `$PORT` is injected by the platform and has to be expanded at runtime — Render sets it, and a
# container that ignores it is a container the load balancer never reaches.
CMD exec uvicorn apps.api.main:create_application --factory --host 0.0.0.0 --port "${PORT:-8000}"
