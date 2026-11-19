# Catalog browser tests

`catalog.e2e.ts` runs the V-cases of the catalog test plan (see
`backend/catalog/tests/tests.py` for the backend cases) in a real Chromium
against a running backend and frontend. Each case prints `PASS` or `FAIL`, and
a screenshot of the page it ends on is saved as `V<n>.png`.

These tests are not part of `pnpm test` and add no dependency to the app.

## 1. Backend

Any database the backend can use. From `backend/`, with the backend's
environment (database, `SECRET_KEY`, and so on) set:

```sh
uv run python manage.py migrate
uv run python manage.py runserver 0.0.0.0:8000
```

Imports run in a Celery worker, so one must run beside the server, against the
same broker:

```sh
uv run celery -A crm worker --pool=solo --loglevel=info
```

The email cases read the emails the backend writes to files, and without Redis
the broker has to live elsewhere. Both are settings; the simplest is a small
settings module of your own that imports `crm.settings`:

```python
from crm.settings import *  # noqa: F403

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.filebased.EmailBackend",
                       "OPTIONS": {"file_path": "/tmp/e2e-mail"}}}
# Only without Redis: a broker on the file system, shared by server and worker.
CELERY_BROKER_URL = "filesystem://"
CELERY_BROKER_TRANSPORT_OPTIONS = {"data_folder_in": "/tmp/celery-fs/out",
                                   "data_folder_out": "/tmp/celery-fs/out",
                                   "processed_folder": "/tmp/celery-fs/processed",
                                   "control_folder": "/tmp/celery-fs/control"}
CELERY_RESULT_BACKEND = "cache+memory://"
```

and start the server with `--settings=<module>` (`manage.py` sets
`DJANGO_SETTINGS_MODULE` itself) and the worker with `DJANGO_SETTINGS_MODULE`
naming it, its folder on `PYTHONPATH` for both. Set `FRONTEND_URL` to the frontend's address,
so the emails link to it.

Then seed the test org. The seed imports the Mercado Libre report in
`backend/catalog/tests/mlInfo`, creates an admin and a member, and puts one
variant on an invoice. Running it again starts the org over.

```sh
uv run python manage.py shell < ../frontend/e2e/catalog-seed.py | tail -1 > /tmp/seed.json
```

## 2. Frontend

A production build, served over plain http. `PROTOCOL_HEADER` lets the server
accept the form posts the browser sends (the tests send the header a proxy
would), and `BODY_SIZE_LIMIT=45M` is the published image's, so the report's two
files fit in one upload:

```sh
pnpm install
PUBLIC_DJANGO_API_URL=http://localhost:8000 pnpm build
PORT=5181 PROTOCOL_HEADER=x-forwarded-proto BODY_SIZE_LIMIT=45M \
  PUBLIC_DJANGO_API_URL=http://localhost:8000 node build
```

## 3. Browser

`puppeteer-core` drives the browser. Install it outside the app:

```sh
mkdir -p /tmp/browser && cd /tmp/browser
npm init -y && npm install puppeteer-core @sparticuz/chromium
```

With a local Chrome or Chromium, set `CHROME_PATH` to it and
`@sparticuz/chromium` is not needed.

## 4. Run

From `frontend/` (Node 22.18 or later runs the TypeScript file directly; from
22.6 to 22.17 add `--experimental-strip-types`):

```sh
E2E_DEPS=/tmp/browser node e2e/catalog.e2e.ts /tmp/seed.json \
  --base http://localhost:5181 --api http://localhost:8000 --shots /tmp/catalog-shots
```

The Import cases upload the report from `backend/catalog/tests/mlInfo/`
(`--report <folder>/` to use another) and read emails from `/tmp/e2e-mail`
(`--mail-dir <folder>`). The run ends with `N passed, M failed`
and exits with 1 if any case failed. The tests change the seeded data (prices,
a deleted product, new categories and channels, an invoice), so seed again
before the next run.
