// Command mockaif stands in for the Agriculture Infrastructure Fund APIs
// during local end-to-end runs, so the adapter's AIF leg can be exercised
// before anyone holds real AIF credentials. AIF verification is an OTP to a
// mobile number, which nobody developing this has, so a mock is the only way
// the session path gets run at all.
//
// Three endpoints, the three the native client calls:
//
//	POST /validate_beneficiaries/   beneficiaryId        -> sends an OTP
//	POST /validate_beneficiaries/   beneficiaryId + otp  -> issues a Token
//	GET  /Status                    loanApplicationNumber-> the loan's state
//	GET  /support-tickets           beneficiaryId        -> the open tickets
//
// # What it deliberately gets wrong, and why
//
// Every awkwardness here is one the real service has, kept so a mapping that
// passes against this mock also passes against AIF:
//
//   - The success flag and the message are spelled Success and Message on the
//     happy paths and success and message on the BV42201 refusal. A mapping
//     reading only one casing is caught.
//   - /Status answers a BARE JSON STRING ("Disbursed"), not an object. A
//     mapping expecting a field is caught.
//   - /support-tickets answers a bare string when the beneficiary has no
//     tickets, and an array when they do. A mapping that always iterates is
//     caught.
//   - The Token is single-use in the sense that matters: the OTP that bought
//     it cannot be spent twice, so a second verify with the same OTP is
//     refused. That is what forces the adapter to HOLD the session rather than
//     re-verify per call.
//
// A missing or wrong apiKey is a 401, and a missing or wrong bearer on the two
// read endpoints is a 401 too, which is what proves both credentials were
// actually sent -- AIF checks the service key and the farmer's consent on the
// same request, and an adapter sending only one would otherwise look fine.
//
// It does not reproduce AIF's rate limits, its downtime pages, or the real
// shape of its error bodies. Those remain unobserved.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// session is one verified farmer: the beneficiary the OTP went to, and when
// the token stops being accepted.
type session struct {
	beneficiary string
	expires     time.Time
}

type server struct {
	apiKey    string
	otp       string
	unknown   string
	expiresIn int

	mu sync.Mutex
	// Tokens issued and still accepted, by token value.
	sessions map[string]session
	// OTPs already spent, by beneficiary. The real OTP is consumed on the
	// verify that succeeds; replaying it is what a leaked log would try.
	spent map[string]bool
	// Monotonic, so a token is predictable in a test log without being
	// guessable from the beneficiary id alone.
	issued int
}

func main() {
	addr := flag.String("addr", ":9103", "address to listen on")
	apiKey := flag.String("api-key", "local-aif-key", "the apiKey header every call must carry")
	otp := flag.String("otp", "123456", "the OTP that verifies")
	unknown := flag.String("unknown-beneficiary", "999999",
		"the one beneficiary id AIF does not know, for reaching the refusal path")
	expiresIn := flag.Int("expires-in", 3600, "the ExpiresIn the verify answer states, in seconds")
	flag.Parse()

	s := &server{
		apiKey:    *apiKey,
		otp:       *otp,
		unknown:   *unknown,
		expiresIn: *expiresIn,
		sessions:  map[string]session{},
		spent:     map[string]bool{},
	}

	mux := http.NewServeMux()
	// Trailing slash as the native client sends it. Registered both ways
	// because a client that trims it is not a fault worth failing on.
	mux.HandleFunc("/validate_beneficiaries/", s.validate)
	mux.HandleFunc("/validate_beneficiaries", s.validate)
	mux.HandleFunc("/Status", s.loanStatus)
	mux.HandleFunc("/support-tickets", s.tickets)

	log.Printf("mockaif listening on %s; otp=%s, unknown beneficiary=%s", *addr, *otp, *unknown)
	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

// authorised checks the service key. Plain text, not JSON: the real service
// refuses before it reaches anything that would produce a body.
func (s *server) authorised(w http.ResponseWriter, r *http.Request) bool {
	if r.Header.Get("apiKey") == s.apiKey {
		return true
	}
	log.Printf("%s %s refused: apiKey %q", r.Method, r.URL.Path, r.Header.Get("apiKey"))
	w.Header().Set("Content-Type", "text/plain")
	w.WriteHeader(http.StatusUnauthorized)
	fmt.Fprintln(w, "Unauthorized")
	return false
}

// verified checks the farmer's bearer and returns the beneficiary it was
// issued for, so a token cannot be used to ask about somebody else.
func (s *server) verified(w http.ResponseWriter, r *http.Request) (string, bool) {
	token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	token = strings.TrimSpace(token)

	s.mu.Lock()
	held, found := s.sessions[token]
	s.mu.Unlock()

	if !found || time.Now().After(held.expires) {
		log.Printf("%s %s refused: no live session for the bearer", r.Method, r.URL.Path)
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprintln(w, "Unauthorized")
		return "", false
	}
	return held.beneficiary, true
}

func writeJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Printf("could not write the answer: %v", err)
	}
}

// validate is both halves of the OTP exchange: without an otp it sends one,
// with an otp it issues a token. One path, as the real service has it.
func (s *server) validate(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) {
		return
	}
	var asked struct {
		BeneficiaryID any    `json:"beneficiaryId"`
		OTP           string `json:"otp"`
	}
	if err := json.NewDecoder(r.Body).Decode(&asked); err != nil {
		writeJSON(w, map[string]any{"success": false, "message": "BV42200: unreadable request"})
		return
	}
	beneficiary := asText(asked.BeneficiaryID)
	if beneficiary == "" {
		writeJSON(w, map[string]any{"success": false, "message": "BV42200: beneficiaryId is required"})
		return
	}
	// Lower-cased success and message, as AIF spells them on this refusal and
	// nowhere else.
	if beneficiary == s.unknown {
		log.Printf("validate %s: unknown beneficiary", beneficiary)
		writeJSON(w, map[string]any{
			"success": false,
			"message": "BV42201: No beneficiary found with the given id",
		})
		return
	}

	if asked.OTP == "" {
		s.mu.Lock()
		s.spent[beneficiary] = false
		s.mu.Unlock()
		log.Printf("validate %s: OTP sent", beneficiary)
		writeJSON(w, map[string]any{
			"Success":      true,
			"MobileNumber": "XXXXXX" + last4(beneficiary),
			"Message":      "OTP sent successfully",
		})
		return
	}

	if asked.OTP != s.otp {
		log.Printf("validate %s: wrong OTP %q", beneficiary, asked.OTP)
		writeJSON(w, map[string]any{"Success": false, "Message": "Invalid OTP"})
		return
	}

	s.mu.Lock()
	if s.spent[beneficiary] {
		s.mu.Unlock()
		log.Printf("validate %s: OTP already spent", beneficiary)
		writeJSON(w, map[string]any{"Success": false, "Message": "OTP has already been used"})
		return
	}
	s.spent[beneficiary] = true
	s.issued++
	token := fmt.Sprintf("mockaif-token-%d", s.issued)
	s.sessions[token] = session{
		beneficiary: beneficiary,
		expires:     time.Now().Add(time.Duration(s.expiresIn) * time.Second),
	}
	s.mu.Unlock()

	log.Printf("validate %s: verified, token issued", beneficiary)
	writeJSON(w, map[string]any{
		"Success":   true,
		"Token":     token,
		"ExpiresIn": s.expiresIn,
		"Beneficiary": map[string]any{
			"Beneficiary_Id":   beneficiary,
			"Beneficiary_Name": "Beneficiary " + beneficiary,
		},
	})
}

// loanStatus answers a BARE JSON STRING on success, as the real one does.
func (s *server) loanStatus(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) {
		return
	}
	if _, ok := s.verified(w, r); !ok {
		return
	}
	loan := r.URL.Query().Get("loanApplicationNumber")
	if loan == "" {
		writeJSON(w, map[string]any{"Success": false, "Message": "loanApplicationNumber is required"})
		return
	}
	// An even number is a loan AIF does not hold, so the not-found path is
	// reachable without a second magic constant.
	if n, err := strconv.Atoi(loan); err == nil && n%2 == 0 {
		log.Printf("Status %s: no such loan", loan)
		writeJSON(w, map[string]any{"Success": false, "Message": "No loan application found"})
		return
	}
	log.Printf("Status %s: Disbursed", loan)
	writeJSON(w, "Disbursed")
}

// tickets answers an array when there are tickets and a bare string when there
// are none, as the real one does.
func (s *server) tickets(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) {
		return
	}
	verifiedFor, ok := s.verified(w, r)
	if !ok {
		return
	}
	beneficiary := r.URL.Query().Get("beneficiaryId")
	if beneficiary == "" {
		writeJSON(w, map[string]any{"Success": false, "Message": "beneficiaryId is required"})
		return
	}
	// The bearer names a beneficiary; asking about another one with it is the
	// leak the adapter's own binding check exists to stop, so the mock refuses
	// it too rather than quietly answering.
	if beneficiary != verifiedFor {
		log.Printf("support-tickets %s: bearer was issued for %s", beneficiary, verifiedFor)
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprintln(w, "Unauthorized")
		return
	}
	// An even beneficiary id has no tickets, so the string answer is reachable.
	if n, err := strconv.Atoi(beneficiary); err == nil && n%2 == 0 {
		log.Printf("support-tickets %s: none", beneficiary)
		writeJSON(w, "No support tickets found")
		return
	}
	log.Printf("support-tickets %s: 2 tickets", beneficiary)
	writeJSON(w, []map[string]any{
		{
			"BeneficiaryId":         beneficiary,
			"LoanApplicationNumber": 201,
			"Question":              "Subsidy not credited",
			"Status":                "Open",
			"description":           "Interest subvention for the last quarter is pending.",
			"subQueryType":          "Interest Subvention",
		},
		{
			"BeneficiaryId":         beneficiary,
			"LoanApplicationNumber": 203,
			"Question":              "Document re-upload",
			"Status":                "Closed",
			"description":           "Land record was re-submitted and accepted.",
			"subQueryType":          "Documentation",
		},
	})
}

// asText reads an id AIF accepts as either a number or a string. JSON has one
// number type, so 101 arrives as 101 and formatting it as %v would give "101"
// for an int and "101" for a float only by luck.
func asText(value any) string {
	switch v := value.(type) {
	case string:
		return strings.TrimSpace(v)
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	case nil:
		return ""
	default:
		return fmt.Sprintf("%v", v)
	}
}

func last4(text string) string {
	if len(text) <= 4 {
		return text
	}
	return text[len(text)-4:]
}
