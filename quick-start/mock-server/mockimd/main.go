// Command mockimd stands in for IMD's Mausamgram NWP API during local
// end-to-end runs.
//
// It answers the shape the real service does -- fcstday1..N carrying date,
// rain, tmin, tmax, rhmin, rhmax, wspd and a warning -- and requires the same
// basic auth, so the adapter's credential path is exercised rather than
// skipped. Forecasts are derived from the requested point so a wrong lat/lon
// shows up as wrong numbers instead of passing silently.
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
	days := flag.Int("days", 3, "forecast days to return (1-5)")
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
