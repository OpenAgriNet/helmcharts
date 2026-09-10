// Command mockimd stands in for IMD's two weather APIs during local end-to-end
// runs. One binary, two routes, because they are two APIs from one department
// and a second container would earn nothing.
//
//	/get-daily     Mausamgram NWP. fcstday1..N carrying date, rain, tmin,
//	               tmax, rhmin, rhmax, wspd and a warning, behind the same
//	               basic auth, so the adapter's credential path is exercised
//	               rather than skipped. Forecasts derive from the requested
//	               point, so a wrong lat/lon shows up as wrong numbers.
//
//	/api/weather   IMD city weather. ONE FLAT OBJECT for every day, with the
//	               day number inside the field name, addressed by station id
//	               and open -- no credential, as the real endpoint is. Readings
//	               derive from the station, so a wrong id shows up as wrong
//	               numbers.
//
// The real city endpoint is behind an IP allowlist, which is exactly why this
// route exists: without it the IMD capability cannot be tested at all until
// somebody's address is whitelisted.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"strconv"
	"time"
)

type forecast struct {
	Date           string   `json:"date"`
	Rain           float64  `json:"rain"`
	TMin           float64  `json:"tmin"`
	TMax           float64  `json:"tmax"`
	RHMin          int      `json:"rhmin"`
	RHMax          int      `json:"rhmax"`
	WSpd           float64  `json:"wspd"`
	Wind           []string `json:"wind,omitempty"`
	WeatherWarning string   `json:"weather_warning,omitempty"`
	CloudMessage   string   `json:"cloud_message,omitempty"`
}

func main() {
	addr := flag.String("addr", ":9100", "listen address")
	user := flag.String("user", "", "basic auth username; empty with -pass means no auth")
	pass := flag.String("pass", "", "basic auth password; empty with -user means no auth")
	days := flag.Int("days", 3, "forecast days to return (/get-daily 1-5, /api/weather 1-7)")
	// The real city endpoint has been seen wrapped all three ways depending on
	// which deployment answers, and the mapping handles all three -- so this is
	// the knob that proves it does.
	wrapping := flag.String("imd-wrap", "array", "/api/weather envelope: array, data or object")
	flag.Parse()

	// No credential configured means none demanded. That is how this runs in the
	// local stack: the registry publishes auth.scheme "none" for this upstream,
	// which is what lets its baseUrl be plaintext http, and a mock that still
	// demanded a password would contradict the record the adapter reads.
	requireAuth := *user != "" || *pass != ""

	http.HandleFunc("/get-daily", func(w http.ResponseWriter, r *http.Request) {
		if requireAuth {
			gotUser, gotPass, ok := r.BasicAuth()
			if !ok || gotUser != *user || gotPass != *pass {
				log.Printf("401 %s %s -- basic auth missing or wrong", r.Method, r.URL.RequestURI())
				w.Header().Set("WWW-Authenticate", `Basic realm="mausamgram"`)
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
		}

		lat, lon, err := point(r)
		if err != nil {
			log.Printf("400 %s -- %v", r.URL.RequestURI(), err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		// The whole query, not just the two fields this mock parses: what the
		// adapter sent is decided by a mapping file, so a log that prints only
		// the fields already known here cannot show a mapping change at all.
		log.Printf("200 %s?%s (lat=%v lon=%v)", r.URL.Path, r.URL.RawQuery, lat, lon)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(body(lat, lon, *days))
	})

	// IMD city weather, addressed by station id. NO AUTH, whatever -user and
	// -pass say: the real endpoint demands no credential, and the registry
	// publishes authScheme none for it. A mock that asked for a password would
	// contradict the record the adapter reads.
	http.HandleFunc("/api/weather", func(w http.ResponseWriter, r *http.Request) {
		station := r.URL.Query().Get("id")
		if station == "" {
			log.Printf("400 %s -- id is required, got %q", r.URL.Path, r.URL.RawQuery)
			http.Error(w, "id is required", http.StatusBadRequest)
			return
		}

		log.Printf("200 %s?%s (station=%s)", r.URL.Path, r.URL.RawQuery, station)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(wrap(cityBody(station, *days), *wrapping))
	})

	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "ok")
	})

	auth := "no auth"
	if requireAuth {
		auth = fmt.Sprintf("basic auth %s/%s", *user, *pass)
	}
	log.Printf("mock IMD listening on http://%s (%s, %d day forecast)", *addr, auth, *days)
	if err := http.ListenAndServe(*addr, nil); err != nil {
		log.Println(err)
		os.Exit(1)
	}
}

// point reads the coordinates the adapter sent, which is what proves the
// request mapping produced them.
func point(r *http.Request) (float64, float64, error) {
	latRaw, lonRaw := r.URL.Query().Get("lat"), r.URL.Query().Get("lon")
	if latRaw == "" || lonRaw == "" {
		return 0, 0, fmt.Errorf("lat and lon are required, got %q", r.URL.RawQuery)
	}
	lat, err := strconv.ParseFloat(latRaw, 64)
	if err != nil {
		return 0, 0, fmt.Errorf("lat %q is not a number", latRaw)
	}
	lon, err := strconv.ParseFloat(lonRaw, 64)
	if err != nil {
		return 0, 0, fmt.Errorf("lon %q is not a number", lonRaw)
	}
	return lat, lon, nil
}

// body derives a forecast from the point, so a wrong coordinate produces wrong
// numbers rather than passing unnoticed. The last day is deliberately partial:
// a provider that reports some readings and not others is the ordinary case,
// and the mapping has to omit what was not measured.
func body(lat, lon float64, days int) map[string]any {
	if days < 1 {
		days = 1
	}
	if days > 5 {
		days = 5
	}

	out := map[string]any{"location": map[string]float64{"lat": lat, "lon": lon}}
	base := math.Abs(lat) + math.Abs(lon)

	for day := 1; day <= days; day++ {
		date := time.Now().AddDate(0, 0, day-1).Format("2006-01-02")
		f := forecast{
			Date: date,
			TMin: round(20 + math.Mod(base, 5) + float64(day)*0.4),
			TMax: round(30 + math.Mod(base, 4) + float64(day)*0.3),
		}
		if day < days {
			f.Rain = round(math.Mod(base*float64(day), 20))
			f.RHMin = 50 + day
			f.RHMax = 88 + day
			f.WSpd = round(3 + math.Mod(base, 3))
			f.Wind = []string{"NW", "North Westerly"}
			if f.Rain > 10 {
				f.WeatherWarning = "Heavy rainfall warning"
			} else {
				f.CloudMessage = "Partly cloudy"
			}
		}
		out[fmt.Sprintf("fcstday%d", day)] = f
	}
	return out
}

func round(v float64) float64 { return math.Round(v*10) / 10 }

// stations are the coordinates the city endpoint echoes, for the handful of
// real stations worth naming. A station that is NOT here answers without
// Latitude and Longitude -- which the real endpoint also does, and which makes
// the mapping fall back to the point the caller asked about. Both paths matter,
// so both are reachable: ask for 43382 to get the echo, anything else to get
// the fallback.
var stations = map[string]struct {
	name     string
	lat, lon float64
}{
	"43382": {"NANCOWRY", 7.98333, 93.55},
	"42182": {"NEW DELHI SAFDARJUNG", 28.5833, 77.2},
	"43003": {"MUMBAI SANTACRUZ", 19.1167, 72.85},
}

// cityBody builds IMD's city-weather answer for one station.
//
// THE AWKWARD PARTS ARE DELIBERATE, because a tidy mock lets a mapping pass
// here and fail against the real service:
//
//   - One flat object for every day, with the day number in the FIELD NAME.
//     A mapping cannot walk the keys; it has to build them.
//   - Max_Temp has a capital T and Min_temp a small one. That is IMD's, and a
//     mapping that normalises the casing reads nothing.
//   - The date is given ONCE. Day N's date has to be derived.
//   - Rainfall and humidity appear once, for the station, not per day -- so a
//     mapping that repeats them on every day is inventing data.
//   - Today's temperatures arrive TWICE, observed and forecast, and they
//     differ. A mapping labelling its answer Forecast has to pick the forecast
//     pair.
//   - The last day carries no description, as a real partial day does, so a
//     mapping has to omit what was not reported rather than emit it empty.
func cityBody(station string, days int) map[string]any {
	if days < 1 {
		days = 1
	}
	if days > 7 {
		days = 7
	}

	// Derived from the station id, so asking for the wrong station produces
	// visibly wrong numbers instead of passing unnoticed.
	seed := 0.0
	for _, digit := range station {
		seed += float64(digit - '0')
	}

	out := map[string]any{
		"Date":         time.Now().Format("2006-01-02"),
		"Station_Code": station,

		// Reported once, for the station: a past-24-hour total and two
		// fixed-hour readings. Not per day, and not labelled as extremes.
		"Past_24_hrs_Rainfall":      round(math.Mod(seed, 20)),
		"Relative_Humidity_at_0830": 60 + int(math.Mod(seed, 30)),
		"Relative_Humidity_at_1730": 70 + int(math.Mod(seed, 25)),

		// Today, twice over. The observed pair is what was measured; the
		// forecast pair is what was predicted. They differ on purpose.
		"Today_Max_temp":           round(31 + math.Mod(seed, 4)),
		"Today_Min_temp":           round(22 + math.Mod(seed, 3)),
		"Todays_Forecast_Max_Temp": round(30 + math.Mod(seed, 4)),
		"Todays_Forecast_Min_temp": round(21 + math.Mod(seed, 3)),
		"Todays_Forecast":          "Generally cloudy sky with Light rain",
	}

	if known, ok := stations[station]; ok {
		out["Station_Name"] = known.name
		out["Latitude"] = known.lat
		out["Longitude"] = known.lon
	}

	for day := 2; day <= days; day++ {
		out[fmt.Sprintf("Day_%d_Max_Temp", day)] = round(30 + math.Mod(seed, 4) + float64(day)*0.3)
		out[fmt.Sprintf("Day_%d_Min_temp", day)] = round(21 + math.Mod(seed, 3) + float64(day)*0.2)
		// The last day reports no description, as a real partial day does.
		if day < days {
			out[fmt.Sprintf("Day_%d_Forecast", day)] = "Partly cloudy sky"
		}
	}
	return out
}

// wrap puts the answer in whichever envelope was asked for. The real endpoint
// has been seen in all three, so the mapping handles all three and this is what
// proves it.
func wrap(body map[string]any, envelope string) any {
	switch envelope {
	case "object":
		return body
	case "data":
		return map[string]any{"data": []map[string]any{body}}
	default:
		return []map[string]any{body}
	}
}
