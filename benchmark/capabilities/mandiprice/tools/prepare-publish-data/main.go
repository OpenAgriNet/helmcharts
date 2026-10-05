// Generate publish payloads for the benchmark, from real Agmarknet market data.
//
// The shape of a payload lives in the template file, not here. This reads the
// market source data, keeps the states asked for, and repeats the template once
// per MARKET-COMMODITY PAIR -- so when the payload structure settles, the
// template changes and this does not.
//
// One resource is one market and one commodity, rather than a market carrying
// every commodity it trades. See the `unit` type for why that matters.
//
//	make publish-data
//	go run ./capabilities/mandiprice/tools/publish-data --config capabilities/mandiprice/config/publish.yaml
package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// Left in the payload for the load tool to replace on every request. A string
// swap costs nothing; re-serialising a multi-megabyte payload per iteration
// would show up in the benchmark as latency the service never caused.
const (
	txnPlaceholder       = "__TRANSACTION_ID__"
	msgPlaceholder       = "__MESSAGE_ID__"
	timestampPlaceholder = "__TIMESTAMP__"
)

// market is one row of the metadata file that fetch-mandiPrice-metadata writes.
// Coordinates are
// strings there, and are parsed rather than passed through: a coordinate
// emitted as a string is valid JSON that no geo query will ever match.
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
	Commodities []commodity `json:"commodities"`
}

// placedMarket is a market whose coordinates parsed and looked plausible.
type placedMarket struct {
	market
	lon, lat float64
}

// unit is one resource: a market and ONE commodity it trades.
//
// A market trades between 1 and 80 commodities, median 4. Publishing a market
// as a single resource carrying all of them made every resource a different
// size, and so made every discover response a different size -- one answer 5 MB
// and the next 1 MB, from queries that matched the same number of things. One
// commodity per resource makes every resource the same shape, which is the only
// way a response size means anything.
//
// It also multiplies honestly: the pairs are real trades, not the same market
// cloned.
//
// copy is 0 for the real pair and counts up for duplicates added to reach
// targetResources. It only affects the resource id, so a duplicate is
// distinguishable without being a different shape.
type unit struct {
	placedMarket
	commodity commodity
	copy      int
}

// catalogConfig is one catalog: which metadata rows belong to it, and what to
// call it.
//
// `match` is compared against the metadata field named by catalogs.groupBy.
// For MandiPrice that field is state_name, so one catalog per state. Another
// capability groups by whatever its own metadata carries -- a region, a
// provider -- by changing groupBy, not by changing this program.
type catalogConfig struct {
	Code      string   `yaml:"code"`
	Name      string   `yaml:"name"`
	Match     string   `yaml:"match"`
	Languages []string `yaml:"languages"`
}

// catalogsConfig is the whole set. The number of catalogs is the number of
// entries, stated explicitly as `count` so a mismatch is caught rather than
// silently producing fewer.
type catalogsConfig struct {
	GroupBy string          `yaml:"groupBy"`
	Count   int             `yaml:"count"`
	Entries []catalogConfig `yaml:"entries"`
}

// commodity is one entry of supportedCommodities, with the real Agmarknet code.
type commodity struct {
	Code string `yaml:"code" json:"code"`
	Name string `yaml:"name" json:"name"`
}

type config struct {
	Template string `yaml:"template"`
	Source   string `yaml:"source"`

	Context       map[string]string `yaml:"context"`
	SchemaContext []string          `yaml:"schemaContext"`

	Resource struct {
		Context string `yaml:"context"`
		Type    string `yaml:"type"`
	} `yaml:"resource"`

	CatalogIDPrefix  string `yaml:"catalogIdPrefix"`
	ResourceIDPrefix string `yaml:"resourceIdPrefix"`
	ProviderID       string `yaml:"providerId"`

	Catalogs           catalogsConfig `yaml:"catalogs"`
	ScaleFactor        int            `yaml:"scaleFactor"`
	CatalogsPerRequest int            `yaml:"catalogsPerRequest"`

	// How many resources to produce in total, across every catalog.
	//
	// Set it and scaleFactor is worked out for you, and the result lands on
	// this number EXACTLY: each state gets a share proportional to how many
	// real market-commodity pairs it has, so the states keep their relative
	// sizes and the total is the number asked for rather than the nearest
	// whole multiple.
	//
	// 0 leaves scaleFactor in charge, which gives a whole multiple of the real
	// data and whatever total that comes to.
	TargetResources int `yaml:"targetResources"`

	// How many resources go in one catalog. 0 means "all of that state's
	// resources", which is one catalog per state. A positive number splits a
	// state across several catalogs, so a payload can carry a chosen number of
	// catalogs of a chosen size.
	//
	// It also bounds the request: 100k resources in one POST is not a publish,
	// it is a timeout. The publish directive's updateMode is MERGE, so a state
	// split across many requests still ends up as ONE catalog.
	ResourcesPerCatalog int `yaml:"resourcesPerCatalog"`

	// Plausible extent for a coordinate, as [minLon, minLat, maxLon, maxLat].
	// The source data carries a handful of corrupt rows -- a longitude of
	// 703620, a latitude equal to its longitude, a market named "Testing" --
	// and a resource at an impossible place answers spatial queries it should
	// never match. Left empty, only WGS84 validity is checked.
	CoordinateBounds []float64 `yaml:"coordinateBounds"`

	Validity struct {
		StartsAt string `yaml:"startsAt"`
		EndsAt   string `yaml:"endsAt"`
	} `yaml:"validity"`

	Output string `yaml:"output"`
}

func (c *config) validate() error {
	if c.Source == "" {
		return fmt.Errorf("source is required")
	}
	if c.Catalogs.GroupBy == "" {
		return fmt.Errorf("catalogs.groupBy is required -- it names the metadata field that decides which rows belong to which catalog")
	}
	// No entries means "one catalog per distinct value of groupBy, taken from
	// the metadata" -- see deriveCatalogs. That is how all-India works without
	// thirty-odd states written out by hand and kept in step with a refetch.
	if len(c.Catalogs.Entries) > 0 && c.Catalogs.Count != len(c.Catalogs.Entries) {
		return fmt.Errorf("catalogs.count says %d but %d entries are listed",
			c.Catalogs.Count, len(c.Catalogs.Entries))
	}
	if c.TargetResources < 0 {
		return fmt.Errorf("targetResources cannot be negative, got %d", c.TargetResources)
	}
	if c.ScaleFactor < 1 {
		return fmt.Errorf("scaleFactor must be at least 1, got %d", c.ScaleFactor)
	}
	if c.CatalogsPerRequest < 1 {
		return fmt.Errorf("catalogsPerRequest must be at least 1, got %d", c.CatalogsPerRequest)
	}
	if c.ResourcesPerCatalog < 0 {
		return fmt.Errorf("resourcesPerCatalog cannot be negative, got %d", c.ResourcesPerCatalog)
	}
	if n := len(c.CoordinateBounds); n != 0 && n != 4 {
		return fmt.Errorf("coordinateBounds needs 4 numbers [minLon, minLat, maxLon, maxLat], got %d", n)
	}
	return nil
}

// plausible reports whether a coordinate is usable. Invalid is not a judgement
// about the market -- it is a statement that this row cannot be placed on a
// map, so publishing it would put a resource somewhere it is not.
// field returns a named metadata field, so grouping can be configured rather
// than compiled in. Adding a groupable field is one case here.
func (m market) field(name string) string {
	switch name {
	case "state_name":
		return m.StateName
	case "agm_state_code":
		return m.AgmStateCode
	case "district_name":
		return m.DistrictName
	default:
		return ""
	}
}

func (c *config) plausible(lon, lat float64) bool {
	if lon < -180 || lon > 180 || lat < -90 || lat > 90 {
		return false
	}
	if len(c.CoordinateBounds) == 4 {
		return lon >= c.CoordinateBounds[0] && lon <= c.CoordinateBounds[2] &&
			lat >= c.CoordinateBounds[1] && lat <= c.CoordinateBounds[3]
	}
	return true
}

// template is the payload shape: one catalog, one resource, one directive.
type template struct {
	Catalog          map[string]any `json:"catalog"`
	Resource         map[string]any `json:"resource"`
	PublishDirective map[string]any `json:"publishDirective"`
}

// tokens carries both the string substitutions and the numeric ones. Numbers
// are separate because JSON tells them apart: a coordinate written as "74.6"
// is a string, and a geo query against a string coordinate matches nothing.
type tokens struct {
	text    map[string]string
	numeric map[string]float64
	// lists replace a whole string value with a JSON array -- languages, or
	// the commodities a market trades.
	lists map[string]any
}

// substitute walks a decoded JSON structure replacing placeholders.
func substitute(node any, t tokens) (any, error) {
	switch typed := node.(type) {
	case map[string]any:
		out := make(map[string]any, len(typed))
		// Sorted, so the generated JSON is stable regardless of Go's
		// randomised map iteration.
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			replaced, err := substitute(typed[key], t)
			if err != nil {
				return nil, err
			}
			out[key] = replaced
		}
		return out, nil
	case []any:
		out := make([]any, len(typed))
		for i, value := range typed {
			replaced, err := substitute(value, t)
			if err != nil {
				return nil, err
			}
			out[i] = replaced
		}
		return out, nil
	case string:
		// A numeric placeholder must be the WHOLE value -- "{{NUM:LON}}"
		// becomes a number, but "lon {{NUM:LON}}" cannot, because the result
		// would have to be a string again.
		if name, ok := wholePlaceholder(typed, "{{LIST:"); ok {
			value, ok := t.lists[name]
			if !ok {
				return nil, fmt.Errorf("template asks for list %q, which the generator does not provide", name)
			}
			return value, nil
		}
		if name, ok := numericName(typed); ok {
			value, ok := t.numeric[name]
			if !ok {
				return nil, fmt.Errorf("template asks for number %q, which the generator does not provide", name)
			}
			return value, nil
		}
		return substituteString(typed, t)
	default:
		return node, nil
	}
}

func numericName(s string) (string, bool) { return wholePlaceholder(s, "{{NUM:") }

// wholePlaceholder matches only when the placeholder IS the whole value.
// "{{NUM:LON}}" can become a number and "{{LIST:commodities}}" an array, but
// "lon {{NUM:LON}}" cannot -- the result would have to be a string again.
func wholePlaceholder(s, open string) (string, bool) {
	const closing = "}}"
	if strings.HasPrefix(s, open) && strings.HasSuffix(s, closing) {
		name := s[len(open) : len(s)-len(closing)]
		if !strings.Contains(name, "{{") {
			return name, true
		}
	}
	return "", false
}

func substituteString(text string, t tokens) (string, error) {
	for token, value := range t.text {
		text = strings.ReplaceAll(text, "{{"+token+"}}", value)
	}
	return text, nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "generate:", err)
		os.Exit(1)
	}
}

func run() error {
	configPath := flag.String("config", "", "path to the generation config")
	outFlag := flag.String("out", "", "output directory, overriding the config")
	flag.Parse()

	if *configPath == "" {
		return fmt.Errorf("--config is required")
	}

	absConfig, err := filepath.Abs(*configPath)
	if err != nil {
		return err
	}
	root, err := benchmarkRoot(absConfig)
	if err != nil {
		return err
	}

	cfg, err := loadConfig(absConfig)
	if err != nil {
		return err
	}

	tpl, err := loadTemplate(filepath.Join(root, cfg.Template))
	if err != nil {
		return err
	}

	meta, err := loadMetadata(filepath.Join(root, cfg.Source))
	if err != nil {
		return err
	}

	outDir := *outFlag
	if outDir == "" {
		outDir = filepath.Join(root, cfg.Output)
	}
	if err := os.RemoveAll(outDir); err != nil {
		return err
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}

	return generate(cfg, tpl, meta.Markets, outDir)
}

// benchmarkRoot walks up from the config file until it finds go.mod.
//
// Paths inside a config are relative to the benchmark directory, not to the
// config's own location -- so a config can be moved or nested without every
// path inside it changing. Deriving the root by counting directories upward is
// what broke when configs gained a capability folder; a marker file does not
// care how deep the config sits.
func benchmarkRoot(configPath string) (string, error) {
	dir := filepath.Dir(configPath)
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("no go.mod above %s, so the benchmark directory cannot be found", configPath)
		}
		dir = parent
	}
}

func loadConfig(path string) (*config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	cfg := &config{}
	if err := yaml.Unmarshal(raw, cfg); err != nil {
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}

func loadTemplate(path string) (*template, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	tpl := &template{}
	if err := json.Unmarshal(raw, tpl); err != nil {
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}
	if tpl.Catalog == nil || tpl.Resource == nil {
		return nil, fmt.Errorf("%s must define both a catalog and a resource block", path)
	}
	return tpl, nil
}

// metadata is the single document fetch-mandiPrice-metadata writes. Markets and
// commodities come from the same fetch, so a payload set can never be assembled from
// mismatched halves.
type metadata struct {
	FetchedAt   string      `json:"fetchedAt"`
	States      []string    `json:"states"`
	Commodities []commodity `json:"commodities"`
	Markets     []market    `json:"markets"`
}

func loadMetadata(path string) (*metadata, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading the mandi metadata: %w -- fetch it first with "+
			"`make get-mandi-metadata`", err)
	}
	var m metadata
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}
	if len(m.Markets) == 0 {
		return nil, fmt.Errorf("%s holds no markets", path)
	}
	if len(m.Commodities) == 0 {
		return nil, fmt.Errorf("%s holds no commodities -- there is nothing for a discover "+
			"filter to match on", path)
	}
	return &m, nil
}

// manifest is what the discover benchmark reads so its queries hit things that
// were actually published. Querying terms absent from the payloads measures the
// empty-result path and reports it as fast.
type manifest struct {
	Payloads int `json:"payloads"`
	Catalogs int `json:"catalogs"`

	// Resources is the total published; RealPairs is how many of those are a
	// distinct market-commodity pair rather than a duplicate added to reach
	// targetResources. Resources / RealPairs is how hard the real data was
	// stretched, which is worth knowing when reading a result.
	Resources int `json:"resources"`
	RealPairs int `json:"realPairs"`

	TargetResources     int             `json:"targetResources"`
	ResourcesPerCatalog int             `json:"resourcesPerCatalog"`
	ScaleFactor         int             `json:"scaleFactor"`
	SkippedNoCoords     int             `json:"skippedNoCoordinates"`
	SkippedBadCoords    int             `json:"skippedBadCoordinates"`
	CatalogsPerRequest  int             `json:"catalogsPerRequest"`
	States              []manifestState `json:"states"`
}

type manifestState struct {
	Code       string    `json:"code"`
	Name       string    `json:"name"`
	CatalogIDs []string  `json:"catalogIds"`
	Markets    int       `json:"markets"`
	Districts  []string  `json:"districts"`
	BBox       []float64 `json:"bbox"` // minLon, minLat, maxLon, maxLat, from real coordinates
}

// quota decides how many resources each catalog gets.
//
// With targetResources set, the total is that number EXACTLY. Each state gets a
// share proportional to the real pairs it has, so the states keep their
// relative sizes; the rounding remainder goes to the states with the largest
// fractional part, and ties break on the match name so two runs agree.
//
// Without it, scaleFactor multiplies each state's real pairs and the total is
// whatever that comes to.
func quota(cfg *config, pairs map[string][]unit) map[string]int {
	out := make(map[string]int, len(pairs))

	if cfg.TargetResources <= 0 {
		factor := cfg.ScaleFactor
		if factor < 1 {
			factor = 1
		}
		for name, list := range pairs {
			out[name] = len(list) * factor
		}
		return out
	}

	total := 0
	for _, list := range pairs {
		total += len(list)
	}
	if total == 0 {
		return out
	}

	// Largest-remainder apportionment. Floor everyone first, then hand out what
	// is left over one at a time.
	type share struct {
		name      string
		remainder float64
	}
	shares := make([]share, 0, len(pairs))
	assigned := 0
	for name, list := range pairs {
		exact := float64(cfg.TargetResources) * float64(len(list)) / float64(total)
		whole := int(exact)
		out[name] = whole
		assigned += whole
		shares = append(shares, share{name: name, remainder: exact - float64(whole)})
	}
	sort.Slice(shares, func(i, j int) bool {
		if shares[i].remainder != shares[j].remainder {
			return shares[i].remainder > shares[j].remainder
		}
		return shares[i].name < shares[j].name
	})
	for i := 0; assigned < cfg.TargetResources; i, assigned = i+1, assigned+1 {
		out[shares[i%len(shares)].name]++
	}
	return out
}

// repeatTo grows or trims a state's pairs to exactly n resources.
//
// Cycling rather than repeating the whole list n times: a partial last pass
// still covers the start of the list, so a state whose quota is not a whole
// multiple keeps every market represented instead of dropping the tail.
func repeatTo(pairs []unit, n int) []unit {
	if len(pairs) == 0 || n <= 0 {
		return nil
	}
	out := make([]unit, 0, n)
	for i := 0; i < n; i++ {
		u := pairs[i%len(pairs)]
		u.copy = i / len(pairs)
		out = append(out, u)
	}
	return out
}

// deriveCatalogs builds one catalog per group that actually has markets, in
// sorted order, when the config lists none.
//
// Writing out thirty-odd states by hand is a list that goes stale the first
// time the metadata is refetched with a different set. The market rows already
// carry agm_state_code, which is the short code a catalog id wants, so there is
// nothing to invent.
//
// It takes the GROUPED markets rather than the raw metadata, so a group that
// lost every market to a missing coordinate or an empty commodity list never
// becomes an empty catalog. All-India fetches 36 states and 9 of them have any
// commodity mapping at all.
//
// Languages default to English only. A state whose catalog should advertise
// more gets an explicit entry; naming the right languages for every state is
// not something to guess at.
func deriveCatalogs(byGroup map[string][]placedMarket) []catalogConfig {
	keys := make([]string, 0, len(byGroup))
	for key, markets := range byGroup {
		if key == "" || len(markets) == 0 {
			continue
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)

	out := make([]catalogConfig, 0, len(keys))
	for _, key := range keys {
		code := byGroup[key][0].AgmStateCode
		if code == "" {
			code = key
		}
		out = append(out, catalogConfig{Code: code, Name: key, Match: key, Languages: []string{"en"}})
	}
	return out
}

func generate(cfg *config, tpl *template, allMarkets []market, outDir string) error {
	// With no entries listed, every group is in scope and the catalogs are
	// derived AFTER filtering -- from the markets that survived, not from the
	// metadata. All-India carries 36 states of which only 9 have any commodity
	// mapping, so deriving from the raw list produces 27 catalogs with nothing
	// in them and the run stops on the first.
	derive := len(cfg.Catalogs.Entries) == 0

	inScope := map[string]bool{}
	for _, st := range cfg.Catalogs.Entries {
		inScope[st.Match] = true
	}

	byGroup := map[string][]placedMarket{}
	var noCoords, badCoords, noCommodities int
	var rejected []string

	for _, m := range allMarkets {
		if !derive && !inScope[m.field(cfg.Catalogs.GroupBy)] {
			continue
		}
		if m.Latitude == "" || m.Longitude == "" {
			noCoords++
			continue
		}
		if len(m.Commodities) == 0 {
			// A MandiPrice resource with nothing to price says nothing, and no
			// select could name it. The mapping simply has no trades recorded
			// for this market in the window that was fetched.
			noCommodities++
			continue
		}
		lon, lonErr := strconv.ParseFloat(strings.TrimSpace(m.Longitude), 64)
		lat, latErr := strconv.ParseFloat(strings.TrimSpace(m.Latitude), 64)
		if lonErr != nil || latErr != nil || !cfg.plausible(lon, lat) {
			badCoords++
			rejected = append(rejected, fmt.Sprintf("%s / %s (%s, %s)",
				m.StateName, m.MarketName, m.Longitude, m.Latitude))
			continue
		}
		byGroup[m.field(cfg.Catalogs.GroupBy)] = append(byGroup[m.field(cfg.Catalogs.GroupBy)], placedMarket{market: m, lon: lon, lat: lat})
	}

	if derive {
		cfg.Catalogs.Entries = deriveCatalogs(byGroup)
		if len(cfg.Catalogs.Entries) == 0 {
			return fmt.Errorf("no catalogs: no market in the metadata has both coordinates "+
				"and a commodity list, and catalogs.entries lists none. Field %q decides the "+
				"grouping", cfg.Catalogs.GroupBy)
		}
		fmt.Printf("derived %d catalog(s) from %s\n", len(cfg.Catalogs.Entries), cfg.Catalogs.GroupBy)
	}

	var (
		written      []string
		catalogs     []any
		directives   []any
		totalBytes   int64
		realCount    int
		resources    int
		catalogIndex int
	)
	states := make([]manifestState, 0, len(cfg.Catalogs.Entries))

	// Expand each state's markets into market-commodity pairs -- one resource
	// each -- then scale that list to the size this state is owed.
	pairsByGroup := map[string][]unit{}
	totalPairs := 0
	for _, st := range cfg.Catalogs.Entries {
		markets := byGroup[st.Match]
		if len(markets) == 0 {
			return fmt.Errorf("no rows matched %q on field %q -- check catalogs.groupBy and the "+
				"entry's match value against the metadata", st.Match, cfg.Catalogs.GroupBy)
		}
		sort.Slice(markets, func(i, j int) bool { return markets[i].MarketID < markets[j].MarketID })

		pairs := make([]unit, 0, len(markets)*4)
		for _, mk := range markets {
			// Sorted, because the mapping endpoint's order is not stable and
			// this file has to be byte-identical from one run to the next.
			list := append([]commodity(nil), mk.Commodities...)
			sort.Slice(list, func(i, j int) bool { return list[i].Code < list[j].Code })
			for _, c := range list {
				pairs = append(pairs, unit{placedMarket: mk, commodity: c})
			}
		}
		pairsByGroup[st.Match] = pairs
		totalPairs += len(pairs)
	}

	quotas := quota(cfg, pairsByGroup)

	// Chunk each state, then take one chunk from each state in turn. Without
	// the interleave a request could carry two chunks of the same state, which
	// is the same catalog twice in one message.
	type chunk struct {
		state catalogConfig
		index int
		units []unit
	}
	var rounds [][]chunk
	expanded := make(map[string][]unit, len(cfg.Catalogs.Entries))
	for index, st := range cfg.Catalogs.Entries {
		units := repeatTo(pairsByGroup[st.Match], quotas[st.Match])
		expanded[st.Match] = units

		size := cfg.ResourcesPerCatalog
		if size == 0 || size > len(units) {
			size = len(units)
		}
		for start, round := 0, 0; start < len(units); start, round = start+size, round+1 {
			end := start + size
			if end > len(units) {
				end = len(units)
			}
			for len(rounds) <= round {
				rounds = append(rounds, nil)
			}
			rounds[round] = append(rounds[round], chunk{state: st, index: index, units: units[start:end]})
		}
	}

	byCatalogEntry := map[string]*manifestState{}
	var flat []chunk
	for _, round := range rounds {
		flat = append(flat, round...)
	}

	for n, c := range flat {
		catalog, directive, entry, err := buildCatalog(cfg, tpl, c.state, c.index, c.units)
		if err != nil {
			return err
		}
		catalogIndex++
		for _, u := range c.units {
			if u.copy == 0 {
				realCount++
			}
		}
		resources += len(c.units)

		agg, ok := byCatalogEntry[c.state.Code]
		if !ok {
			agg = &manifestState{Code: c.state.Code, Name: c.state.Name, CatalogIDs: entry.CatalogIDs}
			byCatalogEntry[c.state.Code] = agg
			states = append(states, manifestState{})
		}
		agg.Markets += entry.Markets
		if agg.BBox == nil {
			agg.BBox = entry.BBox
		}
		for _, d := range entry.Districts {
			if !contains(agg.Districts, d) {
				agg.Districts = append(agg.Districts, d)
			}
		}

		catalogs = append(catalogs, catalog)
		directives = append(directives, directive)

		if len(catalogs) < cfg.CatalogsPerRequest && n != len(flat)-1 {
			continue
		}

		name := fmt.Sprintf("payload-%05d.json", len(written))
		size, err := writePayload(cfg, filepath.Join(outDir, name), catalogs, directives)
		if err != nil {
			return err
		}
		totalBytes += size
		written = append(written, name)
		catalogs = nil
		directives = nil
	}

	states = states[:0]
	for _, st := range cfg.Catalogs.Entries {
		if agg, ok := byCatalogEntry[st.Code]; ok {
			sort.Strings(agg.Districts)
			states = append(states, *agg)
		}
	}

	// A flat file list, because the load tool needs one and should not have to
	// understand the manifest: JMeter reads it with a CSV Data Set Config.
	if err := writeFileList(filepath.Join(outDir, "files.csv"), written); err != nil {
		return err
	}

	// Every published market, flat. This is what the discover query generator
	// draws its points and polygons from, so its queries land on places that
	// were actually published rather than on plausible-looking coordinates.
	if err := writeResourceList(filepath.Join(outDir, "resources.csv"), cfg, expanded); err != nil {
		return err
	}
	if err := writeMarketList(filepath.Join(outDir, "markets.csv"), cfg, byGroup); err != nil {
		return err
	}

	m := manifest{
		Payloads:            len(written),
		Catalogs:            len(byCatalogEntry),
		Resources:           resources,
		RealPairs:           realCount,
		TargetResources:     cfg.TargetResources,
		ResourcesPerCatalog: cfg.ResourcesPerCatalog,
		ScaleFactor:         cfg.ScaleFactor,
		SkippedNoCoords:     noCoords,
		SkippedBadCoords:    badCoords,
		CatalogsPerRequest:  cfg.CatalogsPerRequest,
		States:              states,
	}
	if _, err := writeJSON(filepath.Join(outDir, "manifest.json"), m); err != nil {
		return err
	}

	stretch := 0.0
	if realCount > 0 {
		stretch = float64(resources) / float64(realCount)
	}
	fmt.Printf("%d payload(s), %d catalog(s), %d resources from %d real market-commodity pairs (x%.2f), %.1f MB -> %s\n",
		len(written), len(byCatalogEntry), resources, realCount, stretch,
		float64(totalBytes)/(1024*1024), outDir)
	if noCommodities > 0 {
		fmt.Printf("skipped %d market(s) with no commodities recorded in the mapping\n", noCommodities)
	}
	if noCoords > 0 {
		fmt.Printf("skipped %d market(s) with no coordinates\n", noCoords)
	}
	if badCoords > 0 {
		fmt.Printf("skipped %d market(s) whose coordinates are not plausible:\n", badCoords)
		for _, line := range rejected {
			fmt.Printf("    %s\n", line)
		}
	}
	return nil
}

func writePayload(cfg *config, path string, catalogs, directives []any) (int64, error) {
	context := map[string]any{}
	for key, value := range cfg.Context {
		context[key] = value
	}
	context["transactionId"] = txnPlaceholder
	context["messageId"] = msgPlaceholder
	context["timestamp"] = timestampPlaceholder
	if len(cfg.SchemaContext) > 0 {
		context["schemaContext"] = cfg.SchemaContext
	}

	message := map[string]any{"catalogs": catalogs}
	if len(directives) > 0 && directives[0] != nil {
		message["publishDirectives"] = directives
	}

	return writeJSON(path, map[string]any{"context": context, "message": message})
}

func buildCatalog(cfg *config, tpl *template, st catalogConfig, index int, units []unit) (any, any, manifestState, error) {
	// One catalog per state, whatever number of requests carry it. The publish
	// directive's updateMode is MERGE, so a second request naming the same
	// catalog adds to it rather than replacing it.
	catalogID := cfg.CatalogIDPrefix + st.Code
	distinct := map[int]bool{}
	for _, u := range units {
		distinct[u.MarketID] = true
	}
	entry := manifestState{Code: st.Code, Name: st.Name, CatalogIDs: []string{catalogID}, Markets: len(distinct)}

	languages := st.Languages
	if len(languages) == 0 {
		languages = []string{"en"}
	}

	base := tokens{
		text: map[string]string{
			"CATALOG_ID":       catalogID,
			"CATALOG_INDEX":    strconv.Itoa(index),
			"STATE_CODE":       st.Code,
			"STATE_NAME":       st.Name,
			"PROVIDER_ID":      cfg.ProviderID,
			"RESOURCE_CONTEXT": cfg.Resource.Context,
			"RESOURCE_TYPE":    cfg.Resource.Type,
			"VALID_FROM":       cfg.Validity.StartsAt,
			"VALID_TO":         cfg.Validity.EndsAt,
		},
		numeric: map[string]float64{},
		lists:   map[string]any{"languages": languages},
	}

	catalog, err := substitute(tpl.Catalog, base)
	if err != nil {
		return nil, nil, entry, err
	}

	districts := map[string]bool{}
	minLon, minLat := 180.0, 90.0
	maxLon, maxLat := -180.0, -90.0

	resources := make([]any, 0, len(units))
	for _, u := range units {
		lon, lat := u.lon, u.lat

		// One id per market-commodity pair, so the same market appears once per
		// commodity it trades rather than once overall. The -r suffix separates
		// duplicates added to reach targetResources -- without it the service
		// would see the same resource id republished and deduplicate it, and
		// the catalog would be a fraction of the size asked for.
		resourceID := fmt.Sprintf("%s%d-c%s", cfg.ResourceIDPrefix, u.MarketID, u.commodity.Code)
		if u.copy > 0 {
			resourceID = fmt.Sprintf("%s-r%d", resourceID, u.copy)
		}

		rt := tokens{
			text:    make(map[string]string, len(base.text)+10),
			numeric: map[string]float64{"LON": lon, "LAT": lat},
			lists: map[string]any{
				"languages": languages,
				// Exactly one, which is what makes every resource the same size.
				"commodities": []commodity{u.commodity},
			},
		}
		for key, value := range base.text {
			rt.text[key] = value
		}
		rt.text["RESOURCE_ID"] = resourceID
		rt.text["MARKET_ID"] = strconv.Itoa(u.MarketID)
		rt.text["MARKET_NAME"] = u.MarketName
		rt.text["DISTRICT_ID"] = strconv.Itoa(u.DistrictID)
		rt.text["DISTRICT_NAME"] = u.DistrictName
		rt.text["AGM_DISTRICT_CODE"] = strconv.Itoa(u.AgmDistrictCode)
		rt.text["AGM_STATE_CODE"] = u.AgmStateCode
		rt.text["CENTER_CODE"] = strconv.Itoa(u.CenterCode)
		rt.text["COMMODITY_CODE"] = u.commodity.Code
		rt.text["COMMODITY_NAME"] = u.commodity.Name

		resource, err := substitute(tpl.Resource, rt)
		if err != nil {
			return nil, nil, entry, err
		}
		resources = append(resources, resource)

		if u.copy == 0 {
			districts[u.DistrictName] = true
			minLon, maxLon = min(minLon, lon), max(maxLon, lon)
			minLat, maxLat = min(minLat, lat), max(maxLat, lat)
		}
	}

	catalogMap, ok := catalog.(map[string]any)
	if !ok {
		return nil, nil, entry, fmt.Errorf("catalog template is not a JSON object")
	}
	catalogMap["resources"] = resources

	names := make([]string, 0, len(districts))
	for name := range districts {
		names = append(names, name)
	}
	sort.Strings(names)
	entry.Districts = names
	entry.BBox = []float64{minLon, minLat, maxLon, maxLat}

	var directive any
	if tpl.PublishDirective != nil {
		directive, err = substitute(tpl.PublishDirective, base)
		if err != nil {
			return nil, nil, entry, err
		}
	}
	return catalogMap, directive, entry, nil
}

func contains(list []string, want string) bool {
	for _, item := range list {
		if item == want {
			return true
		}
	}
	return false
}

func writeJSON(path string, value any) (int64, error) {
	file, err := os.Create(path)
	if err != nil {
		return 0, err
	}
	defer file.Close()

	if err := json.NewEncoder(file).Encode(value); err != nil {
		return 0, err
	}
	info, err := file.Stat()
	if err != nil {
		return 0, err
	}
	return info.Size(), nil
}

// writeMarketList records where every published market is.
// writeResourceList records every resource that was published, as the three
// things a discover query is matched on: where it is, what commodity it is, and
// which catalog it belongs to.
//
// This is what lets the discover generator COUNT what a query would return
// instead of guessing. Without it, tuning a query to return about seventy
// resources is trial and error against a service that has to be running.
func writeResourceList(path string, cfg *config, units map[string][]unit) error {
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()

	writer := csv.NewWriter(file)
	defer writer.Flush()

	if err := writer.Write([]string{"state", "commodityCode", "lon", "lat"}); err != nil {
		return err
	}
	for _, st := range cfg.Catalogs.Entries {
		for _, u := range units[st.Match] {
			row := []string{
				st.Code,
				u.commodity.Code,
				strconv.FormatFloat(u.lon, 'f', 6, 64),
				strconv.FormatFloat(u.lat, 'f', 6, 64),
			}
			if err := writer.Write(row); err != nil {
				return err
			}
		}
	}
	return writer.Error()
}

func writeMarketList(path string, cfg *config, byGroup map[string][]placedMarket) error {
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()

	writer := csv.NewWriter(file)
	defer writer.Flush()

	if err := writer.Write([]string{"state", "districtId", "district", "marketId", "lon", "lat"}); err != nil {
		return err
	}
	for _, st := range cfg.Catalogs.Entries {
		markets := byGroup[st.Match]
		sort.Slice(markets, func(i, j int) bool { return markets[i].MarketID < markets[j].MarketID })
		for _, mk := range markets {
			row := []string{
				st.Code,
				strconv.Itoa(mk.DistrictID),
				mk.DistrictName,
				strconv.Itoa(mk.MarketID),
				strconv.FormatFloat(mk.lon, 'f', 6, 64),
				strconv.FormatFloat(mk.lat, 'f', 6, 64),
			}
			if err := writer.Write(row); err != nil {
				return err
			}
		}
	}
	return writer.Error()
}

func writeFileList(path string, names []string) error {
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()

	writer := csv.NewWriter(file)
	defer writer.Flush()

	if err := writer.Write([]string{"payload"}); err != nil {
		return err
	}
	for _, name := range names {
		if err := writer.Write([]string{name}); err != nil {
			return err
		}
	}
	return writer.Error()
}
