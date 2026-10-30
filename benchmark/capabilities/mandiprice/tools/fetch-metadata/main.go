// Fetch Agmarknet reference data and write it in the shape the generators read.
//
// Run once, not per benchmark. The output is committed, so a benchmark needs no
// credentials, no network and no third party to be up -- this exists to refresh
// that file, not to be part of a run.
//
//	export AGMARKNET_MASTER_URL='http://<host>:<port>/v1/fetch-agmarknet-master-data'
//	export AGMARKNET_TOKEN='...'
//	make get-mandi-metadata
//
// Both values come from the environment and NOWHERE else. Not flags, which land
// in shell history; not a config file, which lands in a public repository.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// The master-data endpoint answers one dataset per `option`.
const (
	optionCommodities = "02"
	optionStates      = "04"
	optionDistricts   = "05"
	optionMarkets     = "06"
)

type stateRow struct {
	StateID      int    `json:"state_id"`
	StateName    string `json:"state_name"`
	AgmStateCode string `json:"agm_state_code"`
}

type districtRow struct {
	StateName       string `json:"state_name"`
	DistrictID      int    `json:"district_id"`
	DistrictName    string `json:"district_name"`
	AgmStateCode    string `json:"agm_state_code"`
	AgmDistrictCode int    `json:"agm_district_code"`
}

type marketRow struct {
	MarketID     int    `json:"market_id"`
	StateName    string `json:"state_name"`
	MarketName   string `json:"market_name"`
	DistrictName string `json:"district_name"`
	Latitude     string `json:"market_latitude"`
	Longitude    string `json:"market_longitude"`
	CenterCode   int    `json:"agm_market_center_code"`
}

type commodityRow struct {
	CommodityID      int    `json:"commodity_id"`
	CommodityName    string `json:"commodity_name"`
	AgmCommodityCode int    `json:"agm_commodity_code"`
	GroupName        string `json:"commodity_group_name"`
}

// metadata is the single file the generators read. One document rather than
// several, so a payload set is built from one fetch and cannot be assembled from
// mismatched halves.
type metadata struct {
	FetchedAt   string      `json:"fetchedAt"`
	States      []string    `json:"states"`
	Commodities []commodity `json:"commodities"`
	Markets     []market    `json:"markets"`
}

// commodity is one entry of the commodity list.
//
// Code is commodity_id and is what a payload carries. AgmCode is
// agm_commodity_code and is NOT: the two differ for 531 of the 560
// commodities. Both are kept so the difference is visible rather than a
// surprise -- Tomato is code 65 and agmCode 78.
type commodity struct {
	Code    string `json:"code"`
	Name    string `json:"name"`
	AgmCode int    `json:"agmCode"`
	Group   string `json:"group"`
}

// market is everything about one market, including the identifiers a provider
// call needs.
//
// district_id is the reason this tool exists. The market list alone carries a
// district NAME, and a select built on a name cannot be resolved by a provider
// that wants the id.
type market struct {
	MarketID        int    `json:"market_id"`
	MarketName      string `json:"market_name"`
	StateName       string `json:"state_name"`
	AgmStateCode    string `json:"agm_state_code"`
	DistrictID      int    `json:"district_id"`
	DistrictName    string `json:"district_name"`
	AgmDistrictCode int    `json:"agm_district_code"`
	CenterCode      int    `json:"agm_market_center_code"`
	Latitude        string `json:"market_latitude"`
	Longitude       string `json:"market_longitude"`

	// What this market actually trades, from the market-commodity mapping.
	// Empty when the mapping has nothing for it -- which is most markets in
	// some states, and is reported rather than filled in.
	//
	// Code and name only: agmCode and group belong to the global commodity
	// list, and repeating them here empty would suggest the mapping had
	// supplied them.
	Commodities []traded `json:"commodities"`
}

// traded is one commodity a market deals in. Code is commodity_id, the value a
// payload carries.
type traded struct {
	Code string `json:"code"`
	Name string `json:"name"`
}

// mappingRow is one market in the market-commodity mapping response.
type mappingRow struct {
	MarketID int `json:"market_id"`
	Details  []struct {
		CmdtID   int    `json:"cmdt_id"`
		CmdtName string `json:"cmdt_name"`
	} `json:"cmdt_details"`
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "fetch-mandiPrice-metadata:", err)
		os.Exit(1)
	}
}

func run() error {
	out := flag.String("out", "reference", "directory to write the reference files into")
	states := flag.String("states", "", "comma-separated state names to keep; empty keeps all")
	timeout := flag.Duration("timeout", 60*time.Second, "per-request timeout")
	fromDate := flag.String("from-date", "01-01-2026", "mapping window start, DD-MM-YYYY")
	toDate := flag.String("to-date", "01-12-2026", "mapping window end, DD-MM-YYYY")
	flag.Parse()

	endpoint := os.Getenv("AGMARKNET_MASTER_URL")
	if endpoint == "" {
		return fmt.Errorf("AGMARKNET_MASTER_URL must be set in the environment. Endpoints and " +
			"credentials are deliberately not flags and not config: this repository is public, " +
			"and a flag would put them in shell history")
	}

	client := &http.Client{Timeout: *timeout}

	// A token can be supplied directly, or exchanged for credentials. The
	// exchange is the normal path; the override exists for a token obtained
	// some other way.
	token := os.Getenv("AGMARKNET_TOKEN")
	if token == "" {
		exchanged, err := exchangeToken(client)
		if err != nil {
			return err
		}
		token = exchanged
		fmt.Println("exchanged credentials for a token")
	}

	var (
		stateRows     []stateRow
		districtRows  []districtRow
		marketRows    []marketRow
		commodityRows []commodityRow
	)
	if err := fetch(client, endpoint, token, optionStates, &stateRows); err != nil {
		return err
	}
	if err := fetch(client, endpoint, token, optionDistricts, &districtRows); err != nil {
		return err
	}
	if err := fetch(client, endpoint, token, optionMarkets, &marketRows); err != nil {
		return err
	}
	if err := fetch(client, endpoint, token, optionCommodities, &commodityRows); err != nil {
		return err
	}
	fmt.Printf("fetched %d states, %d districts, %d markets, %d commodities\n",
		len(stateRows), len(districtRows), len(marketRows), len(commodityRows))

	keep := map[string]bool{}
	for _, name := range strings.Split(*states, ",") {
		if name = strings.TrimSpace(name); name != "" {
			keep[name] = true
		}
	}

	markets, unmatched := join(stateRows, districtRows, marketRows, keep)

	// Which commodities each market actually trades. One call per state, and
	// the endpoint wants agm_state_code rather than the name.
	mappingURL := os.Getenv("AGMARKNET_MAPPING_URL")
	withCommodities := 0
	if mappingURL == "" {
		fmt.Println("AGMARKNET_MAPPING_URL is not set, so no market will carry a commodity list")
	} else {
		codes := map[string]bool{}
		for _, m := range markets {
			codes[m.AgmStateCode] = true
		}
		byMarket := map[int][]traded{}
		for code := range codes {
			rows, err := fetchMapping(client, mappingURL, token, code, *fromDate, *toDate)
			if err != nil {
				return err
			}
			for _, row := range rows {
				list := make([]traded, 0, len(row.Details))
				for _, d := range row.Details {
					list = append(list, traded{Code: strconv.Itoa(d.CmdtID), Name: d.CmdtName})
				}
				if len(list) > 0 {
					byMarket[row.MarketID] = list
				}
			}
			fmt.Printf("  %s: %d market(s) with a commodity list\n", code, len(rows))
		}
		for i := range markets {
			if list, ok := byMarket[markets[i].MarketID]; ok {
				markets[i].Commodities = list
				withCommodities++
			}
		}
	}
	if len(markets) == 0 {
		return fmt.Errorf("no markets survived the join -- check the --states names against " +
			"state_name in the source")
	}
	sort.Slice(markets, func(i, j int) bool { return markets[i].MarketID < markets[j].MarketID })

	commodities := make([]commodity, 0, len(commodityRows))
	for _, c := range commodityRows {
		commodities = append(commodities, commodity{
			Code:    strconv.Itoa(c.CommodityID),
			Name:    c.CommodityName,
			AgmCode: c.AgmCommodityCode,
			Group:   c.GroupName,
		})
	}
	sort.Slice(commodities, func(i, j int) bool {
		a, _ := strconv.Atoi(commodities[i].Code)
		b, _ := strconv.Atoi(commodities[j].Code)
		return a < b
	})

	// The states actually WRITTEN, not the filter that was asked for. An empty
	// --states means "keep everything", and reporting the filter then left this
	// list empty in a file describing 36 states -- which reads as "no states"
	// to anything downstream.
	present := map[string]bool{}
	for _, m := range markets {
		if m.StateName != "" {
			present[m.StateName] = true
		}
	}
	kept := make([]string, 0, len(present))
	for name := range present {
		kept = append(kept, name)
	}
	sort.Strings(kept)

	if err := os.MkdirAll(*out, 0o755); err != nil {
		return err
	}
	document := metadata{
		FetchedAt:   time.Now().UTC().Format(time.RFC3339),
		States:      kept,
		Commodities: commodities,
		Markets:     markets,
	}
	path := filepath.Join(*out, "mandi-metadata.json")
	if err := writeJSON(path, document); err != nil {
		return err
	}

	fmt.Printf("wrote %d markets (%d with a real commodity list) and %d commodities to %s\n",
		len(markets), withCommodities, len(commodities), path)
	if unmatched > 0 {
		// Not fatal, and not hidden: a market whose district is not in the
		// district list cannot carry a district_id, so a select built on it
		// would be unresolvable.
		fmt.Printf("skipped %d market(s) whose district is not in the district list\n", unmatched)
	}
	return nil
}

// exchangeToken swaps the access name and password for a short-lived token.
//
// Everything it needs comes from the environment. Nothing about the endpoint or
// the credentials is written down anywhere in this repository, which is public.
func exchangeToken(client *http.Client) (string, error) {
	tokenURL := os.Getenv("AGMARKNET_TOKEN_URL")
	accessName := os.Getenv("AGMARKNET_ACCESS_NAME")
	password := os.Getenv("AGMARKNET_PASSWORD")
	if tokenURL == "" || accessName == "" || password == "" {
		return "", fmt.Errorf("no AGMARKNET_TOKEN was given, so one has to be exchanged: set " +
			"AGMARKNET_TOKEN_URL, AGMARKNET_ACCESS_NAME and AGMARKNET_PASSWORD in the environment")
	}

	body, err := json.Marshal(map[string]string{
		"access_name": accessName,
		"password":    password,
	})
	if err != nil {
		return "", err
	}

	response, err := client.Post(tokenURL, "application/json", bytes.NewReader(body))
	if err != nil {
		// Deliberately not %w: a wrapped url.Error prints the full URL, and
		// this output gets pasted into issues.
		return "", fmt.Errorf("the token request failed")
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("the token endpoint answered %s", response.Status)
	}

	var answer struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(response.Body).Decode(&answer); err != nil {
		return "", fmt.Errorf("unreadable token response: %w", err)
	}
	if answer.Token == "" {
		return "", fmt.Errorf("the token endpoint answered 200 with no token in it")
	}
	return answer.Token, nil
}

// fetchMapping asks which commodities traded in one state over a date window.
//
// The endpoint answers {"success": false, "message": "No data available."}
// rather than an empty list when it has nothing, which is not an error: some
// states simply have no recorded trades.
func fetchMapping(client *http.Client, endpoint, token, stateCode, from, to string) ([]mappingRow, error) {
	target, err := url.Parse(endpoint)
	if err != nil {
		return nil, fmt.Errorf("AGMARKNET_MAPPING_URL is not a URL: %w", err)
	}
	query := target.Query()
	query.Set("token", token)
	query.Set("statecode", stateCode)
	query.Set("option", "6")
	query.Set("from_date", from)
	query.Set("to_date", to)
	target.RawQuery = query.Encode()

	response, err := client.Get(target.String())
	if err != nil {
		return nil, fmt.Errorf("the mapping request for %s failed", stateCode)
	}
	defer response.Body.Close()

	// 400 IS THE "no data" ANSWER, not a bad request.
	//
	// Agmarknet reports "nothing recorded for this state and window" as 400
	// rather than as an empty list. Treating it as fatal stopped an all-India
	// fetch dead at Delhi, after every state's master data had already been
	// collected. A state with nothing recorded contributes no markets carrying
	// commodities, which the caller already handles and reports.
	if response.StatusCode == http.StatusBadRequest {
		return nil, nil
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("the mapping endpoint answered %s for %s", response.Status, stateCode)
	}
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, err
	}

	var rows []mappingRow
	if err := json.Unmarshal(body, &rows); err != nil {
		// Not a list: the no-data shape.
		return nil, nil
	}
	return rows, nil
}

func fetch(client *http.Client, endpoint, token, option string, into any) error {
	target, err := url.Parse(endpoint)
	if err != nil {
		return fmt.Errorf("AGMARKNET_MASTER_URL is not a URL: %w", err)
	}
	query := target.Query()
	query.Set("token", token)
	query.Set("option", option)
	target.RawQuery = query.Encode()

	response, err := client.Get(target.String())
	if err != nil {
		// Deliberately not %w on the URL: a wrapped url.Error prints the full
		// target, token and all, and this output gets pasted into issues.
		return fmt.Errorf("option %s: request failed", option)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("option %s: the endpoint answered %s", option, response.Status)
	}
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return fmt.Errorf("option %s: %w", option, err)
	}
	if err := json.Unmarshal(body, into); err != nil {
		return fmt.Errorf("option %s: unreadable response: %w", option, err)
	}
	return nil
}

// join puts the district and state identifiers onto each market.
func join(states []stateRow, districts []districtRow, markets []marketRow, keep map[string]bool) ([]market, int) {
	stateCode := make(map[string]string, len(states))
	for _, s := range states {
		stateCode[s.StateName] = s.AgmStateCode
	}

	type districtKey struct{ state, district string }
	byDistrict := make(map[districtKey]districtRow, len(districts))
	for _, d := range districts {
		byDistrict[districtKey{d.StateName, d.DistrictName}] = d
	}

	var joined []market
	unmatched := 0
	for _, m := range markets {
		if len(keep) > 0 && !keep[m.StateName] {
			continue
		}
		d, ok := byDistrict[districtKey{m.StateName, m.DistrictName}]
		if !ok {
			unmatched++
			continue
		}
		joined = append(joined, market{
			MarketID:        m.MarketID,
			MarketName:      m.MarketName,
			StateName:       m.StateName,
			AgmStateCode:    stateCode[m.StateName],
			DistrictID:      d.DistrictID,
			DistrictName:    d.DistrictName,
			AgmDistrictCode: d.AgmDistrictCode,
			CenterCode:      m.CenterCode,
			Latitude:        m.Latitude,
			Longitude:       m.Longitude,
		})
	}
	return joined, unmatched
}

func writeJSON(path string, value any) error {
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	encoder.SetIndent("", " ")
	return encoder.Encode(value)
}
