# generator/

Seeds the Meridian source database. Runs in a container via `make seed`; you
never need Python on the host.

| Module | Does |
|---|---|
| `config.py` | every rate, price and knob in one place |
| `simulate.py` | customer and subscription lifecycles → app + billing tables |
| `usage.py` | ~10M usage events, generated month by month in arrival order |
| `marketing.py` | daily spend by campaign, sized from the simulated conversions |
| `load.py` | schema creation and `COPY` into Postgres |
| `schema.sql` | DDL for the four source schemas |

Run it directly against any Postgres:

```bash
pip install -r requirements.txt
MERIDIAN_DSN=postgresql://meridian:meridian@localhost:5433/meridian python -m meridian_gen --quick
python -m meridian_gen --help
```

Same flags, same database: the simulation is fully determined by `--seed`.
