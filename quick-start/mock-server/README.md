# Mock providers

Two stand-in upstreams, so the stack can be exercised end to end without
credentials for the real services.

| mock | route | stands in for | port | auth |
|---|---|---|---|---|
| `mockimd` | `/get-daily` | IMD Mausamgram NWP | 9100 | basic |
| `mockimd` | `/api/weather` | IMD city weather | 9100 | none |
| `mockagmarknet` | `/` | Agmarknet Vistaar | 9101 | token as a query parameter |

`mockimd` serves both IMD APIs from one binary. They are two APIs from one
department, addressed differently and shaped differently, and a second
container would earn nothing.

The city route exists because the real endpoint is behind an **IP allowlist** --
`city.imd.gov.in/api/cityweather.php` answers `401 IP <address> needs to be
whitelisted`. Without this route the IMD capability cannot be tested at all
until somebody's address is whitelisted, and the address that has to be is the
adapter's egress, not a developer's laptop.

## Why the sources are here

The compose file **pulls** every image and builds nothing, so these are not
built by `make up`. They are here to be built and published once, and then
pulled like everything else:

    docker build -t ghcr.io/<org>/oan-mockimd:latest       mock-server/mockimd
    docker build -t ghcr.io/<org>/oan-mockagmarknet:latest  mock-server/mockagmarknet
    docker push ghcr.io/<org>/oan-mockimd:latest
    docker push ghcr.io/<org>/oan-mockagmarknet:latest

Then set `MOCKIMD_IMAGE` and `MOCKAGMARKNET_IMAGE` in `.env`.

## What they deliberately get wrong

A mock that answered tidily would let a mapping pass here and fail against the
real service, so both reproduce the awkward parts on purpose.

`mockimd`'s `/get-daily` answers `fcstday1..N` with the count coming from
`-days`, so a mapping that hardcodes five days is caught. Forecasts derive from
the requested point, so a wrong lat/lon shows up as wrong numbers rather than
passing silently.

`mockimd`'s `/api/weather` is awkward in a different way, because the real city
endpoint is. **One flat object carries every day**, with the day number inside
the field name -- `Day_2_Max_Temp` -- so a mapping has to build the field names
rather than walk the keys. The casing is **inconsistent on every day**:
`Max_Temp` with a capital T, `Min_temp` with a small one. The **date is given
once**, so day N's date has to be derived. **Rainfall and humidity appear once
for the station**, not per day, so a mapping that repeats them is inventing
data. **Today's temperatures arrive twice**, observed and forecast, and they
differ -- so a mapping labelling its answer `Forecast` has to pick the forecast
pair. The last day carries **no description**, as a real partial day does.

Two knobs on it. `-imd-wrap` switches the envelope between `array`, `data` and
`object`, all three of which the real endpoint has been seen in. And a station
id that is **not** one of the three it knows answers with **no `Latitude` or
`Longitude`**, which the real endpoint also does -- so ask for `43382` to
exercise the coordinate echo and anything else to exercise the mapping's
fallback to the point the caller asked about.

`mockagmarknet` answers a **bare JSON array** whose records use **Title Case
keys containing spaces** — `Modal Price`, `Arrival Date` — with **prices as
strings** and dates as `dd-MM-yyyy`. It requires the token as a query
parameter and answers 401 without it, which is what proves the adapter sent
one. Its last record reports no minimum or maximum, as the real data
sometimes does, so a mapping is forced to distinguish "not reported" from
"zero". Prices derive from the requested market and commodity codes, so a
wrong code is visible in the answer.

Neither reproduces the real services' error bodies or credentials. What the
real ones do on no-data, rate limits or auth failure is still unobserved.
