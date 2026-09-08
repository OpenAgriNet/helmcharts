// Command mockagmarknet stands in for Agmarknet's Vistaar API during local
// end-to-end runs.
//
// It answers the shape the real service does: a bare JSON array of records with
// Title Case keys containing spaces and prices as strings. Both of those are
// awkward, and reproducing them is the point -- a mock that returned tidy
// camelCase numbers would let a mapping pass here and fail against the real
// thing.
//
// It requires the token as a query parameter, which is how that API
// authenticates, so the adapter's query auth path is exercised rather than
// skipped. Prices are derived from the requested market and commodity codes, so
// a wrong code shows up as wrong numbers instead of passing silently.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"hash/fnv"
	"log"
	"net/http"
	"os"
	"time"
)

// record is one market's report for one day, keyed exactly as Agmarknet keys it.
type record struct {
	Grade       string `json:"Grade"`
	Group       string `json:"Group"`
	State       string `json:"State"`
	Market      string `json:"Market"`
	Variety     string `json:"Variety"`
	District    string `json:"District"`
	Commodity   string `json:"Commodity"`
	MaxPrice    string `json:"Max Price,omitempty"`
	MinPrice    string `json:"Min Price,omitempty"`
	PriceUnit   string `json:"Price Unit"`
	ModalPrice  string `json:"Modal Price"`
	ArrivalDate string `json:"Arrival Date"`
}

func main() {
	addr := flag.String("addr", ":9101", "address to listen on")
	token := flag.String("token", "local-mandi-token", "token the query must carry")
	days := flag.Int("days", 2, "how many daily records to answer with")
	flag.Parse()

	http.HandleFunc("/v1/fetch-agmarknet-vistaar", func(w http.ResponseWriter, r *http.Request) {
		query := r.URL.Query()
		log.Printf("%s %s?%s", r.Method, r.URL.Path, r.URL.RawQuery)

		if query.Get("token") != *token {
			// The real API answers 401 for a bad token. Worth reproducing: it is
			// what proves the adapter sent one at all.
			http.Error(w, `{"message":"invalid token"}`, http.StatusUnauthorized)
			return
		}
		for _, required := range []string{"statecode", "districtcode", "commoditycode", "from_date", "to_date"} {
			if query.Get(required) == "" {
				http.Error(w, fmt.Sprintf(`{"message":"missing %s"}`, required), http.StatusBadRequest)
				return
			}
		}

		from, err := time.Parse("02-01-2006", query.Get("from_date"))
		if err != nil {
			// dd-MM-yyyy, not ISO. A mapping that forgets to convert lands here.
			http.Error(w, `{"message":"from_date must be dd-MM-yyyy"}`, http.StatusBadRequest)
			return
		}

		commodity := query.Get("commoditycode")
		market := query.Get("marketcode")
		if market == "" {
			// Without a market code the real API widens to the district, so the
			// answer names the district rather than one market.
			market = "district-" + query.Get("districtcode")
		}
		base := 1500 + int(hash(market+commodity)%800)

		records := make([]record, 0, *days)
		for day := 0; day < *days; day++ {
			date := from.AddDate(0, 0, day)
			modal := base + day*25
			rec := record{
				Grade:       "Non-FAQ",
				Group:       "Cereals",
				State:       "Chattisgarh",
				Market:      "Mock APMC " + market,
				Variety:     "D.B.",
				District:    "Balodabazar",
				Commodity:   "Commodity " + commodity,
				PriceUnit:   "Rs./Qtl",
				ModalPrice:  fmt.Sprintf("%d", modal),
				ArrivalDate: date.Format("02-01-2006"),
			}
			// The last record reports no minimum or maximum, which happens in
			// the real data. It must arrive as absent, not zero.
			if day < *days-1 {
				rec.MinPrice = fmt.Sprintf("%d", modal-100)
				rec.MaxPrice = fmt.Sprintf("%d", modal+100)
			}
			records = append(records, rec)
		}

		w.Header().Set("Content-Type", "application/json")
		// A bare array, which is one of the three shapes the real API uses.
		if err := json.NewEncoder(w).Encode(records); err != nil {
			log.Printf("could not write the answer: %v", err)
		}
	})

	log.Printf("mockagmarknet listening on %s, %d records per answer", *addr, *days)
	if err := http.ListenAndServe(*addr, nil); err != nil {
		log.Printf("mockagmarknet stopped: %v", err)
		os.Exit(1)
	}
}

// hash makes the prices depend on what was asked for, so a wrong code is
// visible in the answer rather than silently tolerated.
func hash(s string) uint32 {
	h := fnv.New32a()
	_, _ = h.Write([]byte(s))
	return h.Sum32()
}
