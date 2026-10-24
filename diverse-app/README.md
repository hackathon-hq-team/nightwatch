# Diverse educational application fixtures

This disposable application contains intentionally broken behavior for Nightwatch
scenario evaluation. It combines the original calculator and five domain fixtures
with HTTP, cross-file and receipt examples. It is not a deployed service.

Install the fixture requirements and pytest in your test environment, then run
`python -m pytest -q tests`. The original suite contains 67 passing tests; incident
inputs exercise behavior missing from those baseline tests. Expected behavior is
documented in each application function. Preserve all tests when proposing a repair.

The HTTP examples use in-process FastAPI TestClient requests. The `demo-token` value
is public fixture data, not a credential. No remote service or database is needed.

Use the commit named in the selected alert. Incidents use different fixture branches;
the repository's current checkout alone does not identify a selected scenario.
