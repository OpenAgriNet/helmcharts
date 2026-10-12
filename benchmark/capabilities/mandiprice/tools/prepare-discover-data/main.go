// Generate discover queries for the benchmark.
//
// Queries are built from the payloads that was published: points and polygons
// come from real market coordinates, commodity codes from the real Agmarknet
// list. Random, but seeded, so the same config always produces the same set and
// two runs measure the same work.
//
//	make discover-data
//	go run ./capabilities/mandiprice/tools/discover-data --config capabilities/mandiprice/config/discover.yaml
package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

const (
	txnPlaceholder       = "__TRANSACTION_ID__"
	msgPlaceholder       = "__MESSAGE_ID__"
	timestampPlaceholder = "__TIMESTAMP__"
)

// queryMix weights the two geometries. Every query carries a JSONPath filter
// as well, so there is nothing else to choose.
type queryMix struct {
	Point   int `yaml:"point"`
	Polygon int `yaml:"polygon"`
}

type config struct {
	Source  string `yaml:"source"`
	Seed    int64  `yaml:"seed"`
	Queries int    `yaml:"queries"`

	Context       map[string]string `yaml:"context"`
	SchemaContext []string          `yaml:"schemaContext"`
	Targets       string            `yaml:"targets"`

	QueryMix queryMix `yaml:"queryMix"`

	Point struct {
		// Two markets in different states, no further apart than this, give the
		// line a query centre is placed on.
		BorderWithinMeters float64 `yaml:"borderWithinMeters"`

		// Refuse a query that would need a circle larger than this to find its
		// resources. Without it a rare commodity produces a query covering half
		// the country, which measures a different thing from the rest.
		MaxRadiusMeters float64 `yaml:"maxRadiusMeters"`
	} `yaml:"point"`

	Polygon struct {
		// The same cap, for how far a box is pushed out around its centre.
		MaxPaddingMeters float64 `yaml:"maxPaddingMeters"`
	} `yaml:"polygon"`

	// How big an answer a query gets, and how far it may vary.
	//
	// The generator knows every resource that was published -- resources.csv
	// says where each one is and which commodity it is -- so it does not guess
	// an extent and hope. It SOLVES for one: it sorts that commodity's
	// resources by distance from the query's centre and puts the circle's edge
	// between the last resource it wants and the first it does not.
	//
	// Every answer is then the same size, which is the whole reason a resource
	// carries one commodity. Without it one response is 100 KB and the next
	// 4 MB, and a latency figure covering both describes neither.
	//
	// Tolerance exists because duplicates share their market's coordinates:
	// copies of one market are at one point, so the achievable counts step by
	// however many copies that is -- about ten here -- rather than one at a
	// time. A tolerance narrower than that step rejects nearly every draw.
	Matches struct {
		Target    int `yaml:"target"`
		Tolerance int `yaml:"tolerance"`

		// How many states the answer must come from.
		//
		// A query that lands inside one state is a query about one catalog, and
		// discovery across catalogs is the thing worth measuring. Centres sit
		// on a line between two markets either side of a border, so this is a
		// check that the extent solved for actually reached both.
		MinStates int `yaml:"minStates"`
	} `yaml:"matches"`

	Filter struct {
		Expression string `yaml:"expression"`

		// Only ask about commodities with at least this many PUBLISHED
		// resources. Raised automatically to matches.target + 1, since a
		// commodity with fewer resources than a query wants back can never
		// answer one.
		MinResources int `yaml:"minResources"`

		// Filled from the published resources, not from the config file.
		CommodityCodes []string `yaml:"-"`
	} `yaml:"filter"`

	Output string `yaml:"output"`
}

func (c *config) validate() error {
	if c.Source == "" {
		return fmt.Errorf("source is required")
	}
	if c.Queries < 1 {
		return fmt.Errorf("queries must be at least 1, got %d", c.Queries)
	}
	if c.Targets == "" {
		return fmt.Errorf("targets is required -- without it a spatial query matches nothing")
	}
	if c.QueryMix.Point < 0 || c.QueryMix.Polygon < 0 {
		return fmt.Errorf("queryMix weights cannot be negative")
	}
	if c.QueryMix.Point+c.QueryMix.Polygon == 0 {
		return fmt.Errorf("queryMix must ask for at least one point or polygon query")
	}
	if c.Point.MaxRadiusMeters <= 0 {
		return fmt.Errorf("point.maxRadiusMeters must be positive, got %v", c.Point.MaxRadiusMeters)
	}
	if c.Polygon.MaxPaddingMeters <= 0 {
		return fmt.Errorf("polygon.maxPaddingMeters must be positive, got %v", c.Polygon.MaxPaddingMeters)
	}
	if c.Matches.Target < 1 {
		return fmt.Errorf("matches.target must be at least 1, got %d -- a query that matches "+
			"nothing measures the empty path and reports it as fast", c.Matches.Target)
	}
	if c.Matches.Tolerance < 0 {
		return fmt.Errorf("matches.tolerance cannot be negative, got %d", c.Matches.Tolerance)
	}
	if c.Matches.Tolerance >= c.Matches.Target {
		return fmt.Errorf("matches.tolerance (%d) is not below matches.target (%d), which "+
			"allows an empty answer", c.Matches.Tolerance, c.Matches.Target)
	}
	return nil
}

// market is one row of the payloads's markets.csv.
type market struct {
	State    string
	District string
	ID       string
	Lon, Lat float64
}

// resource is one row of resources.csv: one published resource, as the three
// things a query matches on.
type resource struct {
	State     string
	Commodity string
	Lon, Lat  float64
}

// index groups published resources by commodity, because every query filters on
// one. Counting a query's matches is then a scan of that commodity's resources
// rather than of all hundred thousand.
type index map[string][]resource

func buildIndex(rows []resource) index {
	out := index{}
	for _, r := range rows {
		out[r.Commodity] = append(out[r.Commodity], r)
	}
	return out
}

// solveRadius finds a radius that returns as close to `target` resources of one
// commodity as the data allows, and reports what it would actually return.
//
// Not a search over candidate radii: the answer is read straight off the sorted
// distances. Put the edge between the n-th nearest resource and the (n+1)-th and
// the circle holds exactly n -- provided those two are at different distances,
// which is where the tolerance is spent. Copies of one market share its
// coordinates, so distances come in ties of about ten and only some counts are
// reachable. The reachable count nearest the target wins.
//
// ok is false when this commodity has too few resources near this centre, or
// when reaching them would need a circle larger than maxRadius.
func (ix index) solveRadius(code string, lon, lat float64, target, tolerance int, maxRadius float64) (radius float64, count, states int, ok bool) {
	rs := ix[code]
	if len(rs) <= target {
		return 0, 0, 0, false
	}

	type hit struct {
		distance float64
		state    string
	}
	hits := make([]hit, 0, len(rs))
	for _, r := range rs {
		hits = append(hits, hit{distance: haversine(lon, lat, r.Lon, r.Lat), state: r.State})
	}
	sort.Slice(hits, func(i, j int) bool { return hits[i].distance < hits[j].distance })

	// Walk outwards from the target, taking the first count that a radius can
	// actually produce: n is reachable only if the n-th and (n+1)-th nearest are
	// at different distances.
	best := -1
	for delta := 0; delta <= tolerance; delta++ {
		for _, n := range []int{target - delta, target + delta} {
			if n < 1 || n >= len(hits) {
				continue
			}
			if hits[n-1].distance < hits[n].distance {
				best = n
				break
			}
		}
		if best > 0 {
			break
		}
	}
	if best < 0 {
		return 0, 0, 0, false
	}

	radius = (hits[best-1].distance + hits[best].distance) / 2
	if radius > maxRadius {
		return 0, 0, 0, false
	}

	seen := map[string]bool{}
	for _, h := range hits[:best] {
		seen[h.state] = true
	}
	return radius, best, len(seen), true
}

// solvePadding does the same for a box: the smallest padding whose box holds a
// count within tolerance of the target.
//
// A box grows in two dimensions at once, so there is no sorted list to read the
// answer off. Count rises with padding though, which makes it a bisection: find
// the padding where the count crosses the target, then check where it landed.
func (ix index) solvePadding(code string, lon, lat float64, target, tolerance int, maxPadding float64) (padding float64, count, states int, ok bool) {
	if len(ix[code]) <= target {
		return 0, 0, 0, false
	}

	lo, hi := 0.0, maxPadding
	if n, _ := ix.countInBox(code, lon, lat, hi); n < target-tolerance {
		// Even the largest box allowed does not hold enough.
		return 0, 0, 0, false
	}

	// 40 halvings takes a 500 km range below a millimetre, which is far past
	// the point where another resource could slip in or out.
	for i := 0; i < 40; i++ {
		mid := (lo + hi) / 2
		if n, _ := ix.countInBox(code, lon, lat, mid); n < target {
			lo = mid
		} else {
			hi = mid
		}
	}

	count, states = ix.countInBox(code, lon, lat, hi)
	if count < target-tolerance || count > target+tolerance {
		return 0, 0, 0, false
	}
	return hi, count, states, true
}

// countInBox is withinBox addressed by centre and padding. A separate function
// because Go will not spread boxAround's four return values into a call that
// also takes a commodity code.
func (ix index) countInBox(code string, lon, lat, padding float64) (int, int) {
	minLon, minLat, maxLon, maxLat := boxAround(lon, lat, padding)
	return ix.withinBox(code, minLon, minLat, maxLon, maxLat)
}

// boxAround returns the bounding box `padding` metres around a point.
//
// Latitude is a constant 111 km per degree; longitude shrinks towards the
// poles, so it is scaled by the cosine of the box's own latitude. Without that
// a box in Uttar Pradesh would be noticeably narrower on the ground than the
// same box in Karnataka.
func boxAround(lon, lat, padding float64) (float64, float64, float64, float64) {
	padLat := padding / 111000
	padLon := padLat / math.Max(0.2, math.Cos(lat*math.Pi/180))
	return lon - padLon, lat - padLat, lon + padLon, lat + padLat
}

// withinCircle counts the resources of one commodity inside a circle, and how
// many states they span.
func (ix index) withinCircle(code string, lon, lat, radius float64) (int, int) {
	states := map[string]bool{}
	n := 0
	for _, r := range ix[code] {
		if haversine(lon, lat, r.Lon, r.Lat) <= radius {
			n++
			states[r.State] = true
		}
	}
	return n, len(states)
}

// withinBox counts the resources of one commodity inside a bounding box.
func (ix index) withinBox(code string, minLon, minLat, maxLon, maxLat float64) (int, int) {
	states := map[string]bool{}
	n := 0
	for _, r := range ix[code] {
		if r.Lon >= minLon && r.Lon <= maxLon && r.Lat >= minLat && r.Lat <= maxLat {
			n++
			states[r.State] = true
		}
	}
	return n, len(states)
}

func loadResources(path string) ([]resource, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("reading the published resources: %w -- run the publish data preparation first", err)
	}
	defer file.Close()

	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) < 2 {
		return nil, fmt.Errorf("%s holds no resources", path)
	}

	idx := map[string]int{}
	for i, name := range rows[0] {
		idx[name] = i
	}
	for _, needed := range []string{"state", "commodityCode", "lon", "lat"} {
		if _, ok := idx[needed]; !ok {
			return nil, fmt.Errorf("%s has no %q column -- regenerate the publish payloads", path, needed)
		}
	}

	out := make([]resource, 0, len(rows)-1)
	for _, row := range rows[1:] {
		lon, err := strconv.ParseFloat(row[idx["lon"]], 64)
		if err != nil {
			return nil, err
		}
		lat, err := strconv.ParseFloat(row[idx["lat"]], 64)
		if err != nil {
			return nil, err
		}
		out = append(out, resource{
			State:     row[idx["state"]],
			Commodity: row[idx["commodityCode"]],
			Lon:       lon,
			Lat:       lat,
		})
	}
	return out, nil
}

// borderPair is a market and its nearest neighbour in a different state. A
// circle centred between them, or a box drawn around them, reaches into both.
type borderPair struct {
	a, b market
}

const earthRadiusMeters = 6371000

func haversine(aLon, aLat, bLon, bLat float64) float64 {
	rad := func(d float64) float64 { return d * math.Pi / 180 }
	dLat := rad(bLat - aLat)
	dLon := rad(bLon - aLon)
	h := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(rad(aLat))*math.Cos(rad(bLat))*math.Sin(dLon/2)*math.Sin(dLon/2)
	return 2 * earthRadiusMeters * math.Asin(math.Sqrt(h))
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "discover-data-generator:", err)
		os.Exit(1)
	}
}

func run() error {
	configPath := flag.String("config", "", "path to the query generation config")
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

	markets, err := loadMarkets(filepath.Join(root, cfg.Source, "markets.csv"))
	if err != nil {
		return err
	}

	// Every resource that was actually published, so a query's answer can be
	// counted here rather than discovered when the benchmark runs.
	rows, err := loadResources(filepath.Join(root, cfg.Source, "resources.csv"))
	if err != nil {
		return err
	}
	ix := buildIndex(rows)

	if err := commodityCodes(cfg, ix); err != nil {
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

	return generate(cfg, markets, ix, outDir)
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

// loadCommodityCodes reads the codes a filter may ask about, from the same
// metadata the payloads was built from.
//
// `code` in that document is commodity_id and NOT agm_commodity_code: the two
// differ for 531 of the 560 commodities, and the published payload carries the
// former. A filter built on the wrong one matches nothing and reports it as
// fast.
// commodityCodes decides which commodities queries may ask about, from what was
// actually PUBLISHED rather than from the metadata.
//
// The difference matters. The metadata counts how many markets trade a
// commodity; the payloads repeat those markets to reach targetResources, so a
// commodity in 30 markets is 300 published resources. Filtering on the market
// count threw away almost everything -- 9 commodities of 247 -- and left every
// query asking about the same handful of staples, which is a narrow slice of an
// index holding 100,000 resources.
//
// The floor here is a published-resource count, and it has to clear the match
// target: a commodity with fewer resources than a query wants back can never
// answer one.
func commodityCodes(cfg *config, ix index) error {
	floor := cfg.Filter.MinResources
	if floor <= cfg.Matches.Target {
		floor = cfg.Matches.Target + 1
	}

	codes := make([]string, 0, len(ix))
	for code, rs := range ix {
		if len(rs) >= floor {
			codes = append(codes, code)
		}
	}
	if len(codes) == 0 {
		return fmt.Errorf("no commodity has at least %d published resources, so no query could "+
			"return %d -- lower matches.target or filter.minResources, or publish more data",
			floor, cfg.Matches.Target)
	}
	// Sorted so the draw does not depend on map iteration order.
	sort.Slice(codes, func(i, j int) bool {
		a, aErr := strconv.Atoi(codes[i])
		b, bErr := strconv.Atoi(codes[j])
		if aErr == nil && bErr == nil {
			return a < b
		}
		return codes[i] < codes[j]
	})
	cfg.Filter.CommodityCodes = codes
	return nil
}

func loadMarkets(path string) ([]market, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("reading the published payloads: %w -- run the publish data preparation first", err)
	}
	defer file.Close()

	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) < 2 {
		return nil, fmt.Errorf("%s holds no markets", path)
	}

	// Read by header name, not position. The payloads grew a column once already,
	// and a positional read does not fail when that happens -- it quietly parses
	// the wrong field.
	index := map[string]int{}
	for i, name := range rows[0] {
		index[name] = i
	}
	for _, needed := range []string{"state", "district", "marketId", "lon", "lat"} {
		if _, ok := index[needed]; !ok {
			return nil, fmt.Errorf("%s has no %q column -- regenerate the payloads", path, needed)
		}
	}

	markets := make([]market, 0, len(rows)-1)
	for _, row := range rows[1:] {
		lon, err := strconv.ParseFloat(row[index["lon"]], 64)
		if err != nil {
			return nil, err
		}
		lat, err := strconv.ParseFloat(row[index["lat"]], 64)
		if err != nil {
			return nil, err
		}
		markets = append(markets, market{
			State:    row[index["state"]],
			District: row[index["district"]],
			ID:       row[index["marketId"]],
			Lon:      lon,
			Lat:      lat,
		})
	}
	return markets, nil
}

// borderPairs finds, for every market, its nearest market in another state,
// keeping the pairs close enough to be worth centring a query on.
//
// This is what makes a query span two states without anyone hand-picking
// coordinates: the geometry is derived from where the data actually is.
func borderPairs(markets []market, within float64) []borderPair {
	var pairs []borderPair
	for i, a := range markets {
		best := -1
		bestDistance := math.MaxFloat64
		for j, b := range markets {
			if i == j || a.State == b.State {
				continue
			}
			d := haversine(a.Lon, a.Lat, b.Lon, b.Lat)
			if d < bestDistance {
				bestDistance, best = d, j
			}
		}
		if best >= 0 && bestDistance <= within {
			pairs = append(pairs, borderPair{a: a, b: markets[best]})
		}
	}
	return pairs
}

type spatialConstraint struct {
	Op             string         `json:"op"`
	Targets        string         `json:"targets"`
	Geometry       map[string]any `json:"geometry"`
	DistanceMeters float64        `json:"distanceMeters,omitempty"`
	Quantifier     string         `json:"quantifier,omitempty"`
}

type filterClause struct {
	Type       string `json:"type"`
	Expression string `json:"expression"`
}

// queryNote is what the manifest records about one query, so a slow or empty
// result can be traced back to what was asked.
type queryNote struct {
	File          string   `json:"file"`
	Family        string   `json:"family"`
	Op            string   `json:"op"`
	States        []string `json:"statesNear"`
	RadiusMeters  float64  `json:"radiusMeters,omitempty"`
	PaddingMeters float64  `json:"paddingMeters,omitempty"`
	CentreLon     float64  `json:"centreLon"`
	CentreLat     float64  `json:"centreLat"`
	CommodityCode string   `json:"commodityCode,omitempty"`

	// What this query will match, counted against the published resources
	// rather than guessed. A run that returns something else means the service
	// and these payloads have come apart -- which is worth knowing before
	// reading a latency figure.
	Matches   int `json:"matches"`
	StatesHit int `json:"statesHit"`
}

func generate(cfg *config, markets []market, ix index, outDir string) error {
	random := rand.New(rand.NewSource(cfg.Seed))

	pairs := borderPairs(markets, cfg.Point.BorderWithinMeters)
	if len(pairs) == 0 {
		return fmt.Errorf("no market is within %.0f m of another state's market, so no query "+
			"can be built that spans two states -- raise point.borderWithinMeters",
			cfg.Point.BorderWithinMeters)
	}

	// The geometry draw, expanded by weight so a weighted pick is one index.
	var draw []string
	for i := 0; i < cfg.QueryMix.Point; i++ {
		draw = append(draw, "point")
	}
	for i := 0; i < cfg.QueryMix.Polygon; i++ {
		draw = append(draw, "polygon")
	}

	var (
		written []string
		notes   []queryNote
	)

	// A draw that lands outside the match band is discarded and redrawn. The
	// cap stops an impossible band -- a minimum no extent can reach -- from
	// spinning forever with nothing to show for it.
	const attemptsPerQuery = 200
	maxAttempts := cfg.Queries * attemptsPerQuery
	rejected := 0

	for n, attempt := 0, 0; n < cfg.Queries; attempt++ {
		if attempt >= maxAttempts {
			return fmt.Errorf("gave up after %d attempts with %d of %d queries written: no "+
				"extent returns %d resources give or take %d. Raise matches.tolerance, "+
				"lower matches.target, raise point.maxRadiusMeters or "+
				"polygon.maxPaddingMeters, or publish more data",
				attempt, n, cfg.Queries, cfg.Matches.Target, cfg.Matches.Tolerance)
		}

		chosen := draw[random.Intn(len(draw))]
		pair := pairs[random.Intn(len(pairs))]

		// The centre is placed ANYWHERE on the line between the two markets,
		// not at its midpoint. That is what makes every query distinct: a
		// midpoint gives one centre per border pair and 411 pairs is 411
		// queries, after which a longer run is just the same queries again
		// against a warm cache.
		t := random.Float64()
		lon := pair.a.Lon + t*(pair.b.Lon-pair.a.Lon)
		lat := pair.a.Lat + t*(pair.b.Lat-pair.a.Lat)

		// The commodity is chosen before the extent, because how far a query
		// has to reach to find its resources depends entirely on which
		// commodity it asks for.
		code := cfg.Filter.CommodityCodes[random.Intn(len(cfg.Filter.CommodityCodes))]

		note := queryNote{
			Family:        chosen,
			States:        []string{pair.a.State, pair.b.State},
			CommodityCode: code,
			CentreLon:     lon,
			CentreLat:     lat,
		}
		var spatial spatialConstraint
		var matched, statesHit int
		ok := false

		switch chosen {
		case "point":
			radius, count, states, found := ix.solveRadius(
				code, lon, lat, cfg.Matches.Target, cfg.Matches.Tolerance, cfg.Point.MaxRadiusMeters)
			if found && states >= cfg.Matches.MinStates {
				spatial = spatialConstraint{
					Op:      "S_DWITHIN",
					Targets: cfg.Targets,
					Geometry: map[string]any{
						"type":        "Point",
						"coordinates": []float64{lon, lat},
					},
					DistanceMeters: math.Round(radius),
					Quantifier:     "ANY",
				}
				note.Op = "S_DWITHIN"
				note.RadiusMeters = math.Round(radius)
				matched, statesHit, ok = count, states, true
			}

		case "polygon":
			padding, count, states, found := ix.solvePadding(
				code, lon, lat, cfg.Matches.Target, cfg.Matches.Tolerance, cfg.Polygon.MaxPaddingMeters)
			if found && states >= cfg.Matches.MinStates {
				minLon, minLat, maxLon, maxLat := boxAround(lon, lat, padding)
				// GeoJSON rings close by repeating the first position, and wind
				// counter-clockwise for an exterior ring.
				spatial = spatialConstraint{
					Op:      "S_INTERSECTS",
					Targets: cfg.Targets,
					Geometry: map[string]any{
						"type": "Polygon",
						"coordinates": [][][]float64{{
							{minLon, minLat},
							{maxLon, minLat},
							{maxLon, maxLat},
							{minLon, maxLat},
							{minLon, minLat},
						}},
					},
					Quantifier: "ANY",
				}
				note.Op = "S_INTERSECTS"
				note.PaddingMeters = math.Round(padding)
				matched, statesHit, ok = count, states, true
			}
		}

		if !ok {
			// This commodity, from this centre, cannot answer with the number
			// of resources asked for. Draw again.
			rejected++
			continue
		}
		n++
		note.Matches = matched
		note.StatesHit = statesHit

		intent := map[string]any{"spatial": []spatialConstraint{spatial}}

		// Always. A consumer asks "what near here sells this", not one or the
		// other, so a query without both measures a shape nobody sends.
		expression := strings.ReplaceAll(strings.TrimSpace(cfg.Filter.Expression), "{{COMMODITY_CODE}}", code)
		intent["filters"] = filterClause{Type: "jsonpath", Expression: expression}

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

		name := fmt.Sprintf("query-%05d.json", len(written))
		payload := map[string]any{"context": context, "message": map[string]any{"intent": intent}}
		if err := writeJSON(filepath.Join(outDir, name), payload); err != nil {
			return err
		}
		note.File = name
		written = append(written, name)
		notes = append(notes, note)
	}

	if err := writeFileList(filepath.Join(outDir, "files.csv"), written); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(outDir, "manifest.json"), map[string]any{
		"seed":        cfg.Seed,
		"queries":     len(written),
		"targets":     cfg.Targets,
		"borderPairs": len(pairs),
		"notes":       notes,
	}); err != nil {
		return err
	}

	byFamily := map[string]int{}
	for _, note := range notes {
		key := note.Op
		if note.CommodityCode != "" {
			key += " + filter"
		}
		byFamily[key]++
	}
	fmt.Printf("%d queries from %d border pairs -> %s\n", len(written), len(pairs), outDir)
	keys := make([]string, 0, len(byFamily))
	for key := range byFamily {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		fmt.Printf("    %-24s %d\n", key, byFamily[key])
	}

	// What every query will return, counted rather than hoped for. A wide
	// spread here is the thing to fix before running anything: responses of
	// different sizes make one latency figure out of several populations.
	counts := make([]int, 0, len(notes))
	spanned := map[int]int{}
	for _, note := range notes {
		counts = append(counts, note.Matches)
		spanned[note.StatesHit]++
	}
	sort.Ints(counts)
	fmt.Printf("    matches per query        min %d, median %d, max %d\n",
		counts[0], counts[len(counts)/2], counts[len(counts)-1])
	fmt.Printf("    states per query         1:%d  2:%d  3+:%d\n",
		spanned[1], spanned[2], len(notes)-spanned[1]-spanned[2])
	fmt.Printf("    draws rejected           %d (no extent gave %d±%d matches)\n",
		rejected, cfg.Matches.Target, cfg.Matches.Tolerance)

	// Distinct by construction -- centres are continuous, so two queries should
	// never be identical. Counted rather than assumed: a duplicate means a run
	// is re-asking a question the service has already cached the answer to.
	seen := map[string]bool{}
	for _, note := range notes {
		seen[fmt.Sprintf("%s|%.6f|%.6f|%.0f|%.0f", note.CommodityCode,
			note.CentreLon, note.CentreLat, note.RadiusMeters, note.PaddingMeters)] = true
	}
	fmt.Printf("    distinct queries         %d of %d\n", len(seen), len(notes))
	return nil
}

func writeJSON(path string, value any) error {
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()
	return json.NewEncoder(file).Encode(value)
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
