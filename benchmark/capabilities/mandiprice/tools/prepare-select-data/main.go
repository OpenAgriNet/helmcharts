// Generate select requests for the benchmark.
//
// Every request names one resource that was actually published and one
// commodity that resource actually trades, both read out of the published payloads.
// A select for a market that was never published, or for a commodity it does
// not carry, measures the provider's rejection path and reports it as fast.
//
// Random, but seeded, so the same config always produces the same set and two
// runs measure the same work.
//
//	make select-data
//	go run ./capabilities/mandiprice/tools/select-data --config capabilities/mandiprice/config/select.yaml
package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
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

type config struct {
	Template string `yaml:"template"`
	Source   string `yaml:"source"`

	Seed     int64 `yaml:"seed"`
	Requests int   `yaml:"requests"`

	Context       map[string]string `yaml:"context"`
	SchemaContext []string          `yaml:"schemaContext"`

	ProviderID string `yaml:"providerId"`
	OfferID    string `yaml:"offerId"`
	Quantity   int    `yaml:"quantity"`

	Output string `yaml:"output"`
}

func (c *config) validate() error {
	if c.Template == "" {
		return fmt.Errorf("template is required")
	}
	if c.Source == "" {
		return fmt.Errorf("source is required")
	}
	if c.Requests < 1 {
		return fmt.Errorf("requests must be at least 1, got %d", c.Requests)
	}
	if c.Quantity < 1 {
		return fmt.Errorf("quantity must be at least 1, got %d", c.Quantity)
	}
	return nil
}

// published is what one resource in the published payloads says about itself. Only
// the parts a select has to echo back are read; the rest is left alone.
type published struct {
	ResourceID  string
	MarketName  string
	District    string
	State       string
	Context     string
	Type        string
	ValidFrom   string
	ValidTo     string
	Commodities []commodity
}

type commodity struct {
	Code string `json:"code"`
	Name string `json:"name"`
}

// The shape read out of a publish payload. Deliberately partial: this mirrors
// only what a select needs, so a change elsewhere in the payload does not
// require a change here.
type publishPayload struct {
	Message struct {
		Catalogs []struct {
			Resources []struct {
				ID                 string `json:"id"`
				ResourceAttributes struct {
					Context     string      `json:"@context"`
					Type        string      `json:"@type"`
					Commodities []commodity `json:"supportedCommodities"`
					Market      struct {
						MarketName string `json:"marketName"`
						District   string `json:"district"`
						State      string `json:"state"`
					} `json:"market"`
					Validity struct {
						StartsAt string `json:"startsAt"`
						EndsAt   string `json:"endsAt"`
					} `json:"validity"`
				} `json:"resourceAttributes"`
			} `json:"resources"`
		} `json:"catalogs"`
	} `json:"message"`
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "select-data-generator:", err)
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

	resources, err := loadPublished(filepath.Join(root, cfg.Source))
	if err != nil {
		return err
	}

	tpl, err := os.ReadFile(filepath.Join(root, cfg.Template))
	if err != nil {
		return err
	}
	var contract map[string]any
	if err := json.Unmarshal(tpl, &struct {
		Contract *map[string]any `json:"contract"`
	}{&contract}); err != nil {
		return fmt.Errorf("reading %s: %w", cfg.Template, err)
	}
	if contract == nil {
		return fmt.Errorf("%s must define a contract block", cfg.Template)
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

	return generate(cfg, contract, resources, outDir)
}

// benchmarkRoot walks up from the config file until it finds go.mod.
//
// Paths inside a config are relative to the benchmark directory, not to the
// config's own location -- so a config can be moved or nested without every
// path inside it changing.
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

// loadPublished reads every resource out of the published payloads.
func loadPublished(dir string) ([]published, error) {
	names, err := filepath.Glob(filepath.Join(dir, "payload-*.json"))
	if err != nil {
		return nil, err
	}
	if len(names) == 0 {
		return nil, fmt.Errorf("no publish payloads in %s -- generate the published payloads first", dir)
	}
	// Sorted so the output does not depend on the order the filesystem happens
	// to return.
	sort.Strings(names)

	var resources []published
	for _, name := range names {
		raw, err := os.ReadFile(name)
		if err != nil {
			return nil, err
		}
		var payload publishPayload
		if err := json.Unmarshal(raw, &payload); err != nil {
			return nil, fmt.Errorf("reading %s: %w", name, err)
		}
		for _, catalog := range payload.Message.Catalogs {
			for _, r := range catalog.Resources {
				a := r.ResourceAttributes
				if len(a.Commodities) == 0 {
					// Nothing to select. Not an error in the payloads, just a
					// resource this benchmark cannot ask a question about.
					continue
				}
				resources = append(resources, published{
					ResourceID:  r.ID,
					MarketName:  a.Market.MarketName,
					District:    a.Market.District,
					State:       a.Market.State,
					Context:     a.Context,
					Type:        a.Type,
					ValidFrom:   a.Validity.StartsAt,
					ValidTo:     a.Validity.EndsAt,
					Commodities: a.Commodities,
				})
			}
		}
	}
	if len(resources) == 0 {
		return nil, fmt.Errorf("no resource in %s carries a commodity, so no select can be built", dir)
	}
	return resources, nil
}

// substitute walks the template replacing placeholders.
func substitute(node any, text map[string]string, numbers map[string]float64) (any, error) {
	switch typed := node.(type) {
	case map[string]any:
		out := make(map[string]any, len(typed))
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			replaced, err := substitute(typed[key], text, numbers)
			if err != nil {
				return nil, err
			}
			out[key] = replaced
		}
		return out, nil
	case []any:
		out := make([]any, len(typed))
		for i, value := range typed {
			replaced, err := substitute(value, text, numbers)
			if err != nil {
				return nil, err
			}
			out[i] = replaced
		}
		return out, nil
	case string:
		// A numeric placeholder must be the WHOLE value: "{{NUM:COUNT}}" can
		// become a number, "count {{NUM:COUNT}}" cannot.
		const open, closing = "{{NUM:", "}}"
		if strings.HasPrefix(typed, open) && strings.HasSuffix(typed, closing) {
			name := typed[len(open) : len(typed)-len(closing)]
			value, ok := numbers[name]
			if !ok {
				return nil, fmt.Errorf("template asks for number %q, which the generator does not provide", name)
			}
			return value, nil
		}
		for token, value := range text {
			typed = strings.ReplaceAll(typed, "{{"+token+"}}", value)
		}
		return typed, nil
	default:
		return node, nil
	}
}

// requestNote is what the manifest records, so a slow or failing request can be
// traced back to what it asked for.
type requestNote struct {
	File          string `json:"file"`
	ResourceID    string `json:"resourceId"`
	MarketName    string `json:"marketName"`
	State         string `json:"state"`
	District      string `json:"district"`
	CommodityCode string `json:"commodityCode"`
	CommodityName string `json:"commodityName"`
}

func generate(cfg *config, contract map[string]any, resources []published, outDir string) error {
	random := rand.New(rand.NewSource(cfg.Seed))

	var (
		written []string
		notes   []requestNote
	)

	for n := 0; n < cfg.Requests; n++ {
		r := resources[random.Intn(len(resources))]
		c := r.Commodities[random.Intn(len(r.Commodities))]

		text := map[string]string{
			"RESOURCE_ID":      r.ResourceID,
			"MARKET_NAME":      r.MarketName,
			"DISTRICT":         r.District,
			"STATE":            r.State,
			"COMMODITY_CODE":   c.Code,
			"COMMODITY_NAME":   c.Name,
			"RESOURCE_CONTEXT": r.Context,
			"RESOURCE_TYPE":    r.Type,
			"VALID_FROM":       r.ValidFrom,
			"VALID_TO":         r.ValidTo,
			"PROVIDER_ID":      cfg.ProviderID,
			"OFFER_ID":         cfg.OfferID,
		}
		numbers := map[string]float64{"COUNT": float64(cfg.Quantity)}

		filled, err := substitute(contract, text, numbers)
		if err != nil {
			return err
		}

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

		name := fmt.Sprintf("request-%05d.json", n)
		payload := map[string]any{
			"context": context,
			"message": map[string]any{"contract": filled},
		}
		if err := writeJSON(filepath.Join(outDir, name), payload); err != nil {
			return err
		}
		written = append(written, name)
		notes = append(notes, requestNote{
			File: name, ResourceID: r.ResourceID, MarketName: r.MarketName,
			State: r.State, District: r.District,
			CommodityCode: c.Code, CommodityName: c.Name,
		})
	}

	if err := writeFileList(filepath.Join(outDir, "files.csv"), written); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(outDir, "manifest.json"), map[string]any{
		"seed":               cfg.Seed,
		"requests":           len(written),
		"resourcesAvailable": len(resources),
		"notes":              notes,
	}); err != nil {
		return err
	}

	states := map[string]int{}
	for _, note := range notes {
		states[note.State]++
	}
	keys := make([]string, 0, len(states))
	for key := range states {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	fmt.Printf("%d select request(s) drawn from %d published resources -> %s\n",
		len(written), len(resources), outDir)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, key+" "+strconv.Itoa(states[key]))
	}
	fmt.Printf("    by state: %s\n", strings.Join(parts, ", "))
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
