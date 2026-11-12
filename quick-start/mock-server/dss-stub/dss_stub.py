"""A stand-in DSS: answers POST /v1/turns with a weather forecast fetched through the DSS adapter.

    DSS_NETWORK_SENDER_ID=<your DSS_SUBSCRIBER_ID> DSS_NETWORK_HOST_ID=<your BECKN_NETWORK_ID> \\
        python3 dss_stub.py

It reads the settings the real DSS reads: DSS_DISCOVERY_BASE_URL and
DSS_INVOCATION_BASE_URL (both http://localhost:9204/consumer when unset), plus
DSS_STUB_PORT (8077). Each turn runs one discover and one select for weather.
"""

import datetime, json, logging, os, sys, urllib.error, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

log = logging.getLogger("dss-stub")

DEFAULT_NETWORK_URL = "http://localhost:9204/consumer"
DEFAULT_PORT = 8077
NETWORK_TIMEOUT_SECONDS = 60
WEATHER_SCHEMA = ("https://openagrinet.github.io/network-specs/schema/WeatherObservation/v0.1/"
                  "context.jsonld#openagrinet:WeatherObservation")
FARM_POINT = [74.4, 18.4]
OFFER_ID = "offer:weather-observation:mausamgram:open-data"
SELECT_ATTRIBUTE_KEYS = ("@context", "@type", "informationMode", "subjectCategories",
                         "supportedObservationTypes", "supportedParameters", "geographicGranularities")
UNIT_LABELS = {"Cel": "°C"}
WEATHER_FILTER = ('$.catalogs[*].resources[*] ? (@.resourceAttributes.subjectCategories[*] == "Weather"'
                  ' && @.resourceAttributes.informationMode == "OnDemand")')


class Settings(NamedTuple):
    discovery_url: str
    invocation_url: str
    sender_id: str
    host_id: str


class NetworkFailure(Exception):
    def __init__(self, outcome, cause, detail):
        super().__init__(detail)
        self.outcome, self.cause, self.detail = outcome, cause, detail


def need(env, name, meaning):
    if not env.get(name):
        sys.exit(f"{name} is not set: use {meaning}")
    return env[name]


def settings_from_env(env):
    return Settings(
        discovery_url=env.get("DSS_DISCOVERY_BASE_URL", DEFAULT_NETWORK_URL),
        invocation_url=env.get("DSS_INVOCATION_BASE_URL", DEFAULT_NETWORK_URL),
        sender_id=need(env, "DSS_NETWORK_SENDER_ID", "your DSS_SUBSCRIBER_ID"),
        host_id=need(env, "DSS_NETWORK_HOST_ID", "your BECKN_NETWORK_ID"))


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def network_context(settings, action, transaction_id):
    return {"domain": "agriculture", "action": action, "version": "2.0.0",
            "transactionId": transaction_id, "messageId": str(uuid.uuid4()), "timestamp": now(),
            "schemaContext": [WEATHER_SCHEMA], "networkId": settings.host_id,
            "senderId": settings.sender_id, "receiverId": settings.host_id}


def discover_request(settings, transaction_id):
    point = {"op": "S_INTERSECTS", "quantifier": "ANY",
             "targets": "$.catalogs[*].resources[*].resourceAttributes.coverageAreas[*]",
             "geometry": {"type": "Point", "coordinates": FARM_POINT}}
    return {"context": network_context(settings, "discover", transaction_id),
            "message": {"intent": {"filters": {"type": "jsonpath", "expression": WEATHER_FILTER},
                                   "spatial": [point]}}}


def select_request(settings, catalog, transaction_id):
    resource, provider = catalog["resources"][0], catalog["provider"]
    attributes = {key: resource["resourceAttributes"][key]
                  for key in SELECT_ATTRIBUTE_KEYS if key in resource["resourceAttributes"]}
    attributes["location"] = {"geo": {"type": "Point", "coordinates": FARM_POINT}}
    commitment = {"status": {"descriptor": {"code": "ACTIVE", "name": "active"}},
                  "resources": [{"id": resource["id"], "quantity": {"count": 1}, "resourceAttributes": attributes}],
                  "offer": {"id": OFFER_ID, "resourceIds": [resource["id"]], "provider": provider}}
    context = dict(network_context(settings, "select", transaction_id), bppUri=catalog["bppUri"])
    return {"context": context,
            "message": {"contract": {"status": {"code": "ACTIVE", "name": "active"}, "commitments": [commitment]}}}


def post_network(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as refusal:
        raise NetworkFailure("unavailable", "provider_unavailable", f"{url} answered HTTP {refusal.code}")
    except (OSError, ValueError) as broken:
        raise NetworkFailure("unavailable", "provider_unavailable", f"{url} failed: {broken}")
    if answer.get("message", {}).get("status") == "NACK":
        raise NetworkFailure("unavailable", "provider_unavailable", f"{url} refused: {answer['message']['error']}")
    return answer


def find_catalog(settings, transaction_id):
    answer = post_network(f"{settings.discovery_url}/discover", discover_request(settings, transaction_id))
    catalogs = answer["message"]["catalogs"]
    log.info("turn %s: discover found %d catalogue(s)", transaction_id, len(catalogs))
    if not catalogs:
        raise NetworkFailure("no_match", "domain_unmapped", "discover found no catalogue")
    return catalogs[0]


def ask_forecast(settings, transaction_id):
    catalog = find_catalog(settings, transaction_id)
    request = select_request(settings, catalog, transaction_id)
    answer = post_network(f"{settings.invocation_url}/select", request)
    try:
        resources = answer["message"]["contract"]["commitments"][0]["resources"]
    except (KeyError, IndexError, TypeError):
        raise NetworkFailure("unavailable", "provider_unavailable", "select answered without a forecast")
    log.info("turn %s: select returned %d day(s) from %s", transaction_id, len(resources), catalog["bppUri"])
    return resources


def reading(resource, name):
    parameter = next(p for p in resource["resourceAttributes"]["parameters"] if p["parameter"] == name)
    return parameter["values"], UNIT_LABELS.get(parameter["unit"], parameter["unit"])


def day_line(resource):
    date = resource["resourceAttributes"]["validity"]["startsAt"][:10]
    temperature, t_unit = reading(resource, "Temperature")
    rain, r_unit = reading(resource, "Rainfall")
    humidity, h_unit = reading(resource, "Humidity")
    wind, w_unit = reading(resource, "WindSpeed")
    alert, _ = reading(resource, "Alert")
    return (f"{date}: {temperature['minimum']} to {temperature['maximum']} {t_unit}, rain {rain['sum']} {r_unit}, "
            f"humidity {humidity['minimum']} to {humidity['maximum']} {h_unit}, wind {wind['mean']} {w_unit}, "
            f"{alert['instantaneous']}")


def forecast_text(resources):
    return "Weather forecast from IMD Mausamgram:\n" + "\n".join(day_line(r) for r in resources)


def response_context(turn):
    context = turn.get("context", {})
    return {"id": "api.dss.turn", "version": "stub", "timestamp": now(), "messageId": str(uuid.uuid4()),
            "sessionId": context.get("sessionId", ""), "traceId": context.get("transactionId", "")}


def answered(turn, text):
    return {"context": response_context(turn),
            "message": {"outcome": {"status": "answered", "cause": None, "confidence": 90},
                        "content": [{"type": "text", "text": text}],
                        "sources": [{"id": "src_1", "name": "IMD Mausamgram NWP", "kind": "provider"}]}}


def unanswered(turn, failure):
    if failure.outcome == "no_match":
        text, confidence, extra = "I could not find weather information for that request.", 88, {}
    else:
        text, confidence = "The weather service is unavailable. Please try again shortly.", 0
        extra = {"error": {"code": failure.cause, "message": "The weather provider is unavailable.",
                           "retryable": True, "retryAfterSeconds": 30}}
    outcome = {"status": failure.outcome, "cause": failure.cause, "confidence": confidence}
    return {"context": response_context(turn),
            "message": dict({"outcome": outcome, "content": [{"type": "text", "text": text}]}, **extra)}


def answer_turn(settings, turn):
    transaction_id = turn.get("context", {}).get("transactionId") or str(uuid.uuid4())
    try:
        return answered(turn, forecast_text(ask_forecast(settings, transaction_id)))
    except NetworkFailure as failure:
        log.warning("turn %s: %s, so the outcome is %s", transaction_id, failure.detail, failure.outcome)
        return unanswered(turn, failure)


def sse_event(name, answer, sequence, message):
    context = dict(answer["context"], sequenceNumber=sequence)
    return f"event: {name}\ndata: {json.dumps({'context': context, 'message': message})}\n\n"


def event_stream(answer):
    message, events = answer["message"], []
    if message["outcome"]["status"] == "answered":
        events.append(sse_event("claim.completed", answer, 1, {"content": message["content"]}))
    events.append(sse_event("turn.completed", answer, len(events) + 1, message))
    return "".join(events).encode()


def make_server(settings, port=DEFAULT_PORT):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/v1/turns":
                return self.reply(404, "text/plain", b"not found")
            try:
                turn = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except ValueError:
                return self.reply(400, "text/plain", b"the body is not JSON")
            answer = answer_turn(settings, turn)
            if "text/event-stream" in self.headers.get("Accept", ""):
                return self.reply(200, "text/event-stream", event_stream(answer))
            self.reply(200, "application/json", json.dumps(answer).encode())

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main(env):
    settings = settings_from_env(env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = make_server(settings, int(env.get("DSS_STUB_PORT", DEFAULT_PORT)))
    log.info("stand-in DSS on :%d, calling the network as %s through %s",
             server.server_port, settings.sender_id, settings.discovery_url)
    server.serve_forever()


if __name__ == "__main__":
    main(os.environ)
