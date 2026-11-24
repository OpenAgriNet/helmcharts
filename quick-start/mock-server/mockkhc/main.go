// Command mockkhc stands in for the Kisan Help Center APIs during local
// end-to-end runs.
//
// KHC is the one provider where a mock is not only about credentials. Filing a
// grievance WRITES to a real grievance desk, under a real farmer's name, and
// no retry undoes it. So the filing path is exercised here permanently, not
// just until credentials arrive, and the real host is called once, deliberately,
// with the desk owner watching.
//
// Eleven operations, the eleven the native client calls:
//
//	POST /auth/send-otp                      mobile              -> sends an OTP
//	POST /auth/verify-otp                    mobile + otp         -> isRegistered + token
//	GET  /v1/state/reference/states                               -> 36 states
//	GET  /v1/state/reference/districts       stateId              -> 38 districts
//	GET  /v1/state/reference/sub-districts   districtId           -> 9 sub-districts
//	GET  /v1/state/reference/villages        subDistrictId        -> 86 villages
//	GET  /v1/state/reference/schemes         stateId              -> 37 schemes
//	GET  /v1/state/reference/categories      scheme               -> 10 categories
//	GET  /v1/state/reference/sub-categories  problemCategory      -> 6 sub-categories
//	POST /v1/state/grievances                the ten identifiers  -> a grievance number
//	GET  /v1/state/grievances/status         grievanceNo (query)  -> the grievance
//	POST /v1/state/grievances/check-status   grievanceNo (query)  -> sends a status OTP
//	POST /v1/state/grievances/verify-otp     grievanceNo + otp    -> the grievance
//
// # What is real and what is filler
//
// Measured against KHC UAT on 2026-11-24 and reproduced exactly: the auth
// header form (`Authorization: ApiKey <key>`), the envelope `{status, message,
// data}` with **status a STRING** ("200 OK", not the number 200), the field
// names on every reference row, the absence of any paging, and the row counts
// above.
//
// Real values, copied from UAT: scheme 38 "Agriculture Infrastructure Fund
// (AIF)" in division 31 "Others", its ten problem categories (39..48), and the
// first five sub-categories of category 39.
//
// Filler, generated deterministically: all state, district, sub-district and
// village names, and every scheme and category other than those above. They
// are plausible, not authoritative. Nothing here should be read as KHC's
// master data.
//
// # What it deliberately gets wrong, and why
//
// Every awkwardness is one UAT has, kept so a mapping or a tool that passes
// here also passes against KHC:
//
//   - `status` is a STRING in a 200 response. A caller testing `status == 200`
//     is caught.
//   - Reference field names are lowercase and unseparated -- `stateid`,
//     `subdistrictname`, `problemsubcategory` -- while the query parameters
//     that select them are camelCase: `stateId`, `subDistrictId`,
//     `problemCategory`. A caller that assumes one casing throughout is caught.
//   - The lists are whole. There is no page parameter and no total, so paging
//     is the caller's problem, and a caller that expects the provider to do it
//     reads one page and stops.
//   - Village names REPEAT across sub-districts, as they do in the real data.
//     A tool that assumes one name resolves to one village is caught.
//   - `GET /v1/state/grievances/status` needs NO OTP: a grievance number alone
//     reads the record. That is what UAT does and it is the open question in
//     migration-plan/open-issues.md item 20, so it is reproduced rather than
//     quietly hardened.
//   - `POST /check-status` reads grievanceNo from the QUERY STRING and answers
//     400 "grievanceNo is required" when it is only in the body, while
//     `POST /verify-otp` reads both from the BODY. That inconsistency is why
//     the status journey cannot be bound to a registry row, and keeping it here
//     is what lets the fix be tested locally when KHC answers.
//
// It does not reproduce KHC's rate limits, downtime pages or real error bodies.
// Those remain unobserved.
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

// filed is one grievance this process accepted, so the status reads have
// something to answer rather than a constant.
type filed struct {
	GrievanceNo       string `json:"grievanceNo"`
	ExternalReference string `json:"externalReferenceNo"`
	CurrentStatus     string `json:"currentStatus"`
	CurrentLevel      string `json:"currentLevel"`
	CreatedAt         string `json:"createdAt"`
	Mobile            string `json:"-"`
}

type server struct {
	authHeader string
	otp        string
	registered string
	stateCode  string

	mu sync.Mutex
	// Tokens issued by verify-otp and still accepted, by token value.
	tokens map[string]time.Time
	// Grievances filed in this process, by grievance number.
	grievances map[string]filed
	// Status OTPs asked for, by grievance number.
	statusOTP map[string]bool
	sequence  int
}

func main() {
	addr := flag.String("addr", ":9106", "address to listen on")
	// The WHOLE Authorization value, not the key inside it. KHC wants
	// "ApiKey <key>", and the adapter's static-header scheme sends one
	// variable verbatim, so the value configured everywhere is the header.
	authHeader := flag.String("auth-header", "ApiKey local-khc-key",
		"the exact Authorization value every call must carry")
	otp := flag.String("otp", "123456", "the OTP that verifies, for both journeys")
	registered := flag.String("registered-mobile", "9000000001",
		"the one mobile KHC already knows, for reaching the existing-farmer branch; "+
			"every other mobile answers isRegistered false")
	stateCode := flag.String("grievance-prefix", "BR",
		"the two letters KHC puts at the front of a grievance number")
	flag.Parse()

	s := &server{
		authHeader: *authHeader,
		otp:        *otp,
		registered: *registered,
		stateCode:  *stateCode,
		tokens:     map[string]time.Time{},
		grievances: map[string]filed{},
		statusOTP:  map[string]bool{},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/auth/send-otp", s.sendOTP)
	mux.HandleFunc("/auth/verify-otp", s.verifyOTP)
	mux.HandleFunc("/v1/state/reference/states", s.reference("states"))
	mux.HandleFunc("/v1/state/reference/districts", s.reference("districts"))
	mux.HandleFunc("/v1/state/reference/sub-districts", s.reference("sub-districts"))
	mux.HandleFunc("/v1/state/reference/villages", s.reference("villages"))
	mux.HandleFunc("/v1/state/reference/schemes", s.reference("schemes"))
	mux.HandleFunc("/v1/state/reference/categories", s.reference("categories"))
	mux.HandleFunc("/v1/state/reference/sub-categories", s.reference("sub-categories"))
	// Longest pattern wins in ServeMux, so the three sub-paths are registered
	// before the collection they sit under.
	mux.HandleFunc("/v1/state/grievances/status", s.readStatus)
	mux.HandleFunc("/v1/state/grievances/check-status", s.checkStatus)
	mux.HandleFunc("/v1/state/grievances/verify-otp", s.verifyStatusOTP)
	mux.HandleFunc("/v1/state/grievances", s.create)

	log.Printf("mockkhc listening on %s; otp=%s, registered mobile=%s",
		*addr, *otp, *registered)
	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

// ----------------------------------------------------------------------------
// Envelope
// ----------------------------------------------------------------------------

// answer writes KHC's envelope. `status` is a STRING in the body and the HTTP
// code is set from the same place, because UAT keeps the two in step and a
// caller reading only one has to still be right.
func answer(w http.ResponseWriter, code int, status, message string, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	body := map[string]any{"status": status, "message": message, "data": data}
	if err := json.NewEncoder(w).Encode(body); err != nil {
		log.Printf("could not write the answer: %v", err)
	}
}

func ok(w http.ResponseWriter, message string, data any) {
	answer(w, http.StatusOK, "200 OK", message, data)
}

func badRequest(w http.ResponseWriter, message string) {
	answer(w, http.StatusBadRequest, "400 BAD_REQUEST", message, nil)
}

func notFound(w http.ResponseWriter, message string) {
	answer(w, http.StatusNotFound, "404 NOT_FOUND", message, nil)
}

// authorised checks the service key. KHC takes it as `Authorization: ApiKey
// <key>` -- not Bearer, and not an apiKey header, both of which were tried
// against UAT and refused.
func (s *server) authorised(w http.ResponseWriter, r *http.Request) bool {
	held := strings.TrimSpace(r.Header.Get("Authorization"))
	if held == s.authHeader {
		return true
	}
	log.Printf("%s %s refused: Authorization %q", r.Method, r.URL.Path, redact(held))
	answer(w, http.StatusUnauthorized, "401 UNAUTHORIZED", "Unauthorized", nil)
	return false
}

// redact keeps the scheme and hides the key, so a log line says which
// credential form arrived without printing the credential.
func redact(header string) string {
	parts := strings.SplitN(header, " ", 2)
	if len(parts) != 2 {
		return header
	}
	return parts[0] + " <" + strconv.Itoa(len(parts[1])) + " chars>"
}

func (s *server) mustPost(w http.ResponseWriter, r *http.Request) bool {
	if r.Method == http.MethodPost {
		return true
	}
	// Wrapped in a 500, which is how UAT answers a wrong method on these
	// paths: the framework's 405 reaches the client inside the service's own
	// error envelope.
	log.Printf("%s %s refused: POST only", r.Method, r.URL.Path)
	answer(w, http.StatusInternalServerError, "500 INTERNAL_SERVER_ERROR",
		"405 METHOD_NOT_ALLOWED", nil)
	return false
}

// ----------------------------------------------------------------------------
// Registration OTP
// ----------------------------------------------------------------------------

func (s *server) sendOTP(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) || !s.mustPost(w, r) {
		return
	}
	var asked struct {
		Mobile string `json:"mobile"`
	}
	if err := json.NewDecoder(r.Body).Decode(&asked); err != nil {
		badRequest(w, "Unreadable request")
		return
	}
	if len(strings.TrimSpace(asked.Mobile)) != 10 {
		badRequest(w, "mobile is required")
		return
	}
	log.Printf("send-otp %s: OTP sent", mask(asked.Mobile))
	// No masked number and no expiry in the reply, as UAT has it: the caller
	// cannot echo either, and inventing one is how the agent starts skipping
	// the tool.
	ok(w, "OTP sent", nil)
}

func (s *server) verifyOTP(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) || !s.mustPost(w, r) {
		return
	}
	var asked struct {
		Mobile string `json:"mobile"`
		OTP    string `json:"otp"`
	}
	if err := json.NewDecoder(r.Body).Decode(&asked); err != nil {
		badRequest(w, "Unreadable request")
		return
	}
	mobile := strings.TrimSpace(asked.Mobile)
	if len(mobile) != 10 {
		badRequest(w, "mobile is required")
		return
	}
	if asked.OTP != s.otp {
		log.Printf("verify-otp %s: wrong OTP", mask(mobile))
		answer(w, http.StatusUnauthorized, "401 UNAUTHORIZED",
			"Invalid or expired OTP", nil)
		return
	}

	s.mu.Lock()
	s.sequence++
	token := fmt.Sprintf("khc-token-%04d", s.sequence)
	s.tokens[token] = time.Now().Add(time.Hour)
	s.mu.Unlock()

	// isRegistered is a BRANCH, not a failure: a farmer KHC has not seen has
	// to send farmerName and gender on the filing. Keyed off one known mobile
	// so both branches are reachable without a second magic constant.
	isRegistered := mobile == s.registered
	log.Printf("verify-otp %s: verified, isRegistered=%v", mask(mobile), isRegistered)
	ok(w, "OTP verified", map[string]any{
		"isRegistered": isRegistered,
		"token":        token,
	})
}

// ----------------------------------------------------------------------------
// Reference lists
// ----------------------------------------------------------------------------

func (s *server) reference(list string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !s.authorised(w, r) {
			return
		}
		if r.Method != http.MethodGet {
			answer(w, http.StatusInternalServerError, "500 INTERNAL_SERVER_ERROR",
				"405 METHOD_NOT_ALLOWED", nil)
			return
		}
		wanted := queryParamOf(list)
		id := 0
		if wanted != "" {
			raw := strings.TrimSpace(r.URL.Query().Get(wanted))
			if raw == "" {
				badRequest(w, wanted+" is required")
				return
			}
			parsed, err := strconv.Atoi(raw)
			if err != nil || parsed <= 0 {
				badRequest(w, wanted+" must be a positive number")
				return
			}
			id = parsed
		}
		rows := rowsFor(list, id)
		if rows == nil {
			notFound(w, "No "+list+" found")
			return
		}
		log.Printf("%s %s=%d: %d rows", list, wanted, id, len(rows))
		ok(w, "", rows)
	}
}

// ----------------------------------------------------------------------------
// Filing
// ----------------------------------------------------------------------------

func (s *server) create(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) || !s.mustPost(w, r) {
		return
	}
	// The header KHC wants the farmer's token on is NOT settled -- no
	// operation declares a second security scheme, and open item 1 in
	// migration-plan/khc/plan.md carries the question. X-KHC-Token is OURS,
	// a placeholder, and it is checked here so the adapter is forced to send
	// the token somewhere rather than drop it silently. The day KHC answers,
	// this name changes in one place.
	token := strings.TrimSpace(r.Header.Get("X-KHC-Token"))
	s.mu.Lock()
	expires, held := s.tokens[token]
	s.mu.Unlock()
	if !held || time.Now().After(expires) {
		log.Printf("create: refused, no live token on X-KHC-Token")
		answer(w, http.StatusUnauthorized, "401 UNAUTHORIZED",
			"Mobile verification is required before filing", nil)
		return
	}

	var asked struct {
		ExternalReferenceNo string `json:"externalReferenceNo"`
		FarmerMobileNumber  string `json:"farmerMobileNumber"`
		FarmerText          string `json:"farmerText"`
		FarmerLanguage      string `json:"farmerLanguage"`
		StateID             *int   `json:"stateId"`
		VillageID           *int   `json:"villageId"`
		Scheme              *int   `json:"scheme"`
		Division            *int   `json:"division"`
		ProblemCategory     *int   `json:"problemCategory"`
		ProblemSubCategory  *int   `json:"problemSubCategory"`
		FarmerName          string `json:"farmerName"`
		Gender              string `json:"gender"`
	}
	if err := json.NewDecoder(r.Body).Decode(&asked); err != nil {
		badRequest(w, "Unreadable request")
		return
	}

	for name, value := range map[string]*int{
		"stateId": asked.StateID, "villageId": asked.VillageID,
		"scheme": asked.Scheme, "division": asked.Division,
		"problemCategory":    asked.ProblemCategory,
		"problemSubCategory": asked.ProblemSubCategory,
	} {
		if value == nil || *value <= 0 {
			badRequest(w, name+" is required")
			return
		}
	}
	if strings.TrimSpace(asked.FarmerText) == "" {
		badRequest(w, "farmerText is required")
		return
	}
	if len(strings.TrimSpace(asked.FarmerMobileNumber)) != 10 {
		badRequest(w, "farmerMobileNumber is required")
		return
	}
	if asked.ExternalReferenceNo == "" {
		badRequest(w, "externalReferenceNo is required")
		return
	}
	// The new-farmer branch, enforced: a mobile KHC does not know must carry
	// a name and a gender, and a filing that skips them is refused rather
	// than accepted with blanks.
	if asked.FarmerMobileNumber != s.registered {
		if strings.TrimSpace(asked.FarmerName) == "" {
			badRequest(w, "farmerName is required for a farmer not yet registered")
			return
		}
		if asked.Gender != "M" && asked.Gender != "F" && asked.Gender != "O" {
			badRequest(w, "gender is required for a farmer not yet registered")
			return
		}
	}
	// Whether KHC validates the ten identifiers against each other is
	// unobserved (support.yaml's `missing`), so one check is made here that
	// it certainly should: a village has to sit under the state sent.
	if villageState(*asked.VillageID) != *asked.StateID {
		log.Printf("create: villageId %d does not sit under stateId %d",
			*asked.VillageID, *asked.StateID)
		badRequest(w, "villageId does not belong to the stateId supplied")
		return
	}

	s.mu.Lock()
	s.sequence++
	number := fmt.Sprintf("%s/%06d/%d", s.stateCode, 100000+s.sequence, s.sequence)
	record := filed{
		GrievanceNo:       number,
		ExternalReference: asked.ExternalReferenceNo,
		CurrentStatus:     "Open",
		CurrentLevel:      "Block",
		CreatedAt:         time.Now().UTC().Format(time.RFC3339),
		Mobile:            asked.FarmerMobileNumber,
	}
	s.grievances[number] = record
	s.mu.Unlock()

	log.Printf("create %s: filed for %s, ref %s",
		number, mask(asked.FarmerMobileNumber), asked.ExternalReferenceNo)
	// externalReferenceNo is echoed BESIDE grievanceNo, never instead of it.
	// They are different values and reporting ours as KHC's would hand the
	// farmer a number no desk recognises.
	ok(w, "Grievance registered", map[string]any{
		"grievanceNo":         number,
		"externalReferenceNo": asked.ExternalReferenceNo,
		"currentStatus":       record.CurrentStatus,
		"createdAt":           record.CreatedAt,
	})
}

// ----------------------------------------------------------------------------
// Status
// ----------------------------------------------------------------------------

// readStatus is the UNGATED read. A grievance number alone opens the record --
// no OTP, no token beyond the service key. That is what UAT does; see
// open-issues.md item 20.
func (s *server) readStatus(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) {
		return
	}
	if r.Method != http.MethodGet {
		answer(w, http.StatusInternalServerError, "500 INTERNAL_SERVER_ERROR",
			"405 METHOD_NOT_ALLOWED", nil)
		return
	}
	number := strings.TrimSpace(r.URL.Query().Get("grievanceNo"))
	if number == "" {
		badRequest(w, "grievanceNo is required")
		return
	}
	s.mu.Lock()
	record, held := s.grievances[strings.ToUpper(number)]
	s.mu.Unlock()
	if !held {
		notFound(w, "Grievance not found")
		return
	}
	log.Printf("status %s: read with no challenge", record.GrievanceNo)
	ok(w, "", record)
}

// checkStatus reads grievanceNo from the QUERY STRING on a POST. A caller that
// sends it in the body gets 400 "grievanceNo is required" -- exactly UAT's
// behaviour, and exactly the shape no registry row can express, since
// BuildEndpoint appends a query string only for methods that take no body.
func (s *server) checkStatus(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) || !s.mustPost(w, r) {
		return
	}
	number := strings.TrimSpace(r.URL.Query().Get("grievanceNo"))
	if number == "" {
		log.Printf("check-status refused: grievanceNo was not in the query string")
		badRequest(w, "grievanceNo is required")
		return
	}
	s.mu.Lock()
	_, held := s.grievances[strings.ToUpper(number)]
	if held {
		s.statusOTP[strings.ToUpper(number)] = true
	}
	s.mu.Unlock()
	if !held {
		notFound(w, "Grievance not found")
		return
	}
	log.Printf("check-status %s: status OTP sent", strings.ToUpper(number))
	ok(w, "OTP sent", nil)
}

// verifyStatusOTP reads BOTH values from the body, unlike check-status. The
// inconsistency is the provider's, kept so whichever way item 20 is settled
// can be tested here first.
func (s *server) verifyStatusOTP(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) || !s.mustPost(w, r) {
		return
	}
	var asked struct {
		GrievanceNo string `json:"grievanceNo"`
		OTP         string `json:"otp"`
	}
	if err := json.NewDecoder(r.Body).Decode(&asked); err != nil {
		badRequest(w, "Unreadable request")
		return
	}
	number := strings.ToUpper(strings.TrimSpace(asked.GrievanceNo))
	if number == "" {
		badRequest(w, "grievanceNo is required")
		return
	}
	if asked.OTP != s.otp {
		answer(w, http.StatusUnauthorized, "401 UNAUTHORIZED",
			"Invalid or expired OTP", nil)
		return
	}
	s.mu.Lock()
	record, held := s.grievances[number]
	sent := s.statusOTP[number]
	s.mu.Unlock()
	if !held {
		notFound(w, "Grievance not found")
		return
	}
	if !sent {
		answer(w, http.StatusUnauthorized, "401 UNAUTHORIZED",
			"Invalid or expired OTP", nil)
		return
	}
	log.Printf("verify-otp %s: status read after a challenge", number)
	ok(w, "", record)
}

func mask(mobile string) string {
	if len(mobile) < 4 {
		return "XXXXXX"
	}
	return "XXXXXX" + mobile[len(mobile)-4:]
}
