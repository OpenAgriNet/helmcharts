// Command mockpmfby stands in for the PMFBY (Pradhan Mantri Fasal Bima Yojana)
// APIs during local end-to-end runs, so the adapter's insurance and grievance
// legs can be exercised before anyone holds real PMFBY credentials. The policy
// and claim reads are gated behind an OTP to a mobile number, which nobody
// developing this has, so a mock is the only way the session path gets run.
//
// Seven endpoints, the seven the adapter calls, in the two groups PMFBY
// itself keeps them in.
//
// The insurance group, on the NIC side:
//
//	POST /api/v1/services/nic/getOtp              mobile          -> sends an OTP
//	POST /api/v1/services/nic/verifyMobile        mobile + otp    -> verifies
//	GET  /api/v1/policy/policy/farmerpolicylist   farmerID        -> the policies
//	GET  /api/v1/claims/claims/claimSearchReport  farmerID        -> the claims
//
// The grievance group, on the FGMS side, behind its own login:
//
//	POST /krphapi/FGMS/NICUsersLogin                      uid+pwd -> a Token
//	POST /krphapi/FGMS/AddKRPHNCIPGrievenceSupportTicket  ticket  -> files it
//	POST /krphapi/FGMS/GetGrievenceTicketsStatus          ticket  -> its state
//
// # What it deliberately gets wrong, and why
//
// Every awkwardness here is one the real service has, kept so a mapping that
// passes against this mock also passes against PMFBY:
//
//   - The two groups answer in two different envelopes. NIC answers
//     {status, data, error} with a BOOLEAN status; FGMS answers
//     {responseCode, responseMessage, responseDynamic} with responseCode as
//     the STRING "1" or "0". A mapping reading one shape on both is caught.
//   - getOtp's data is a bare string with a TRAILING SPACE, not an object.
//   - A failed FGMS call answers responseDynamic: null and keeps every other
//     key, so a mapping that tests for the key's presence rather than its
//     value is caught.
//   - The policy read nests the interesting rows one level down, in
//     applicationList inside each policy, and the claim read keeps them flat.
//     The two reads do not share a shape.
//   - The claim rows mix cases on sibling keys -- Status beside ClaimStatus
//     beside claimPaymentStatus, cropID beside cropName -- and carry nulls in
//     numeric fields. That is PMFBY's own spelling, not a typo here.
//   - Both reads return the farmer's bank account number and Aadhaar-linked
//     payment flag. They are here ON PURPOSE: the adapter's mappings are
//     supposed to drop them, and a mock that never sends them could not tell
//     a mapping that drops them from one that never saw them.
//   - The OTP is single-use. A second verify with the same OTP is refused,
//     which is what forces the adapter to HOLD the session rather than
//     re-verify per call.
//
// # The one place it does NOT match PMFBY, and what that costs
//
// verifyMobile here answers data.farmerID alongside data.verified. The real
// verifyMobile answers only {"verified": true}: PMFBY resolves a mobile to a
// farmerID through a SEPARATE internal call,
// GET /api/v1/services/services/farmerMobileExists, which is not part of the
// published Beckn surface. One Beckn action maps to one upstream call, so the
// confirm action cannot make both -- and farmerID is a required query
// parameter on both reads.
//
// So this mock folds the resolution into verifyMobile. The cost is real and
// is the opposite of every other choice above: a mapping that passes against
// this mock will NOT pass against real PMFBY unmodified. Closing it needs one
// of -- PMFBY exposing farmerID on verifyMobile, the adapter learning to
// chain two upstream calls behind one action, or the caller supplying a
// farmerID it already holds. farmerMobileExists is served here too, with the
// real shape, so whichever way it is closed has something to test against.
//
// It does not reproduce PMFBY's rate limits, its downtime pages, or the real
// shape of its error bodies. Those remain unobserved.
package main

import (
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

type server struct {
	uid       string
	pwd       string
	otp       string
	unknown   string
	expiresIn int

	mu sync.Mutex
	// FGMS tokens issued and still accepted.
	tokens map[string]time.Time
	// OTPs already spent, by mobile. Replaying one is what a leaked log
	// would try.
	spent map[string]bool
	// Monotonic, so a token or ticket number is readable in a test log
	// without being guessable from the mobile alone.
	issued int
}

func main() {
	addr := flag.String("addr", ":9104", "address to listen on")
	uid := flag.String("access-uid", "local-pmfby-uid", "the appAccessUID the FGMS login accepts")
	pwd := flag.String("access-pwd", "local-pmfby-pwd", "the appAccessPWD the FGMS login accepts")
	otp := flag.String("otp", "123456", "the OTP that verifies")
	unknown := flag.String("unknown-mobile", "9999999999",
		"the one mobile PMFBY does not know, for reaching the refusal path")
	expiresIn := flag.Int("expires-in", 3600, "how long an FGMS token lasts, in seconds")
	flag.Parse()

	s := &server{
		uid: *uid, pwd: *pwd, otp: *otp, unknown: *unknown, expiresIn: *expiresIn,
		tokens: map[string]time.Time{},
		spent:  map[string]bool{},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/services/nic/getOtp", s.getOTP)
	mux.HandleFunc("/api/v1/services/nic/verifyMobile", s.verifyMobile)
	mux.HandleFunc("/api/v1/services/services/farmerMobileExists", s.mobileExists)
	mux.HandleFunc("/api/v1/policy/policy/farmerpolicylist", s.policyList)
	mux.HandleFunc("/api/v1/claims/claims/claimSearchReport", s.claimReport)
	mux.HandleFunc("/krphapi/FGMS/NICUsersLogin", s.login)
	mux.HandleFunc("/krphapi/FGMS/AddKRPHNCIPGrievenceSupportTicket", s.fileTicket)
	mux.HandleFunc("/krphapi/FGMS/GetGrievenceTicketsStatus", s.ticketStatus)

	log.Printf("mockpmfby listening on %s; otp=%s, unknown mobile=%s", *addr, *otp, *unknown)
	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

func writeJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Printf("could not write the answer: %v", err)
	}
}

// nicFail is the NIC envelope's refusal: status false, the reason in error,
// and data left null rather than dropped.
func nicFail(w http.ResponseWriter, reason string) {
	writeJSON(w, map[string]any{"data": nil, "error": reason, "status": false})
}

// fgmsFail is the FGMS envelope's refusal. Every key the success answer has
// is still here, which is why a mapping must read responseCode rather than
// test for responseDynamic.
func fgmsFail(w http.ResponseWriter, reason string) {
	writeJSON(w, map[string]any{
		"jsonString":      nil,
		"recordCount":     0,
		"responseCode":    "0",
		"responseObject":  nil,
		"responseDynamic": nil,
		"responseMessage": reason,
	})
}

// read decodes a JSON body into a loose map. PMFBY sends otp as a number on
// one call and a string on the next, so nothing here is typed tightly.
func read(r *http.Request) map[string]any {
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		return nil
	}
	return body
}

func text(body map[string]any, key string) string {
	switch v := body[key].(type) {
	case string:
		return strings.TrimSpace(v)
	case float64:
		// JSON has one number type, so an OTP sent as 123456 must come back
		// as "123456" and not "123456.000000".
		return strings.TrimSuffix(strings.TrimRight(formatFloat(v), "0"), ".")
	default:
		return ""
	}
}

func formatFloat(v float64) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// farmerID is PMFBY's own id for a mobile. Derived rather than held so any
// mobile a tester types resolves, and stable so a second call agrees with the
// first.
func farmerID(mobile string) string { return "FBY" + mobile }

// fgmsAuthorised checks the FGMS token. The grievance endpoints send it bare
// in Authorization, with no Bearer prefix, as PMFBY has it.
func (s *server) fgmsAuthorised(w http.ResponseWriter, r *http.Request) bool {
	token := strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))

	s.mu.Lock()
	expires, found := s.tokens[token]
	s.mu.Unlock()

	if !found || time.Now().After(expires) {
		log.Printf("%s %s refused: no live FGMS token", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusUnauthorized)
		fgmsFail(w, "Invalid or expired token")
		return false
	}
	return true
}

// login issues the FGMS token the grievance endpoints want. Its expiry is
// stated as an RFC 3339 time, which is one of the two forms the adapter's
// tokenExpiryField reads.
func (s *server) login(w http.ResponseWriter, r *http.Request) {
	body := read(r)
	if text(body, "appAccessUID") != s.uid || text(body, "appAccessPWD") != s.pwd {
		log.Printf("NICUsersLogin refused: wrong credentials")
		fgmsFail(w, "Invalid credentials")
		return
	}

	s.mu.Lock()
	s.issued++
	token := "mockpmfby-fgms-token-" + itoa(s.issued)
	expires := time.Now().Add(time.Duration(s.expiresIn) * time.Second)
	s.tokens[token] = expires
	s.mu.Unlock()

	log.Printf("NICUsersLogin: token issued")
	writeJSON(w, map[string]any{
		"responseCode":    "1",
		"responseMessage": "Login successful",
		"responseDynamic": map[string]any{
			"token": map[string]any{
				"Token":   token,
				"validTo": expires.UTC().Format(time.RFC3339),
			},
		},
	})
}

// fileTicket opens a grievance. A retried filing opens a SECOND ticket, as the
// real one does, which is why the adapter is configured never to retry it.
func (s *server) fileTicket(w http.ResponseWriter, r *http.Request) {
	if !s.fgmsAuthorised(w, r) {
		return
	}
	body := read(r)
	application := text(body, "applicationNo")
	if application == "" {
		fgmsFail(w, "Application Details not Exists")
		return
	}
	// An application number ending in 0 is one FGMS does not hold, so the
	// refusal path is reachable without a second magic constant.
	if strings.HasSuffix(application, "0") {
		log.Printf("AddKRPHNCIPGrievenceSupportTicket %s: no such application", application)
		fgmsFail(w, "Application Details not Exists")
		return
	}

	s.mu.Lock()
	s.issued++
	// Digits only. The Grievance pack pins case.ticketNo to ^[0-9]+$, so a
	// prefixed ticket would be refused by our own schema before the caller
	// ever got to read it back.
	ticket := itoa(2025100000 + s.issued)
	s.mu.Unlock()

	log.Printf("AddKRPHNCIPGrievenceSupportTicket %s: ticket %s", application, ticket)
	writeJSON(w, map[string]any{
		"responseCode":    "1",
		"responseMessage": "Grievance ticket created successfully",
		"responseDynamic": map[string]any{
			"GrievenceSupportTicketNo": ticket,
			"ApplicationNo":            application,
			"TicketStatus":             "Open",
			"CreatedOn":                time.Now().UTC().Format("02-01-2006"),
		},
	})
}

// ticketStatus answers an ARRAY in responseDynamic, where fileTicket answers
// an object there. The key is the same; the shape is not.
func (s *server) ticketStatus(w http.ResponseWriter, r *http.Request) {
	if !s.fgmsAuthorised(w, r) {
		return
	}
	body := read(r)
	ticket := text(body, "GrievenceSupportTicketNo")
	mobile := text(body, "requestorMobileNo")
	if ticket == "" && mobile == "" {
		fgmsFail(w, "Ticket Details not Exists")
		return
	}
	if mobile == s.unknown {
		log.Printf("GetGrievenceTicketsStatus %s: unknown mobile", mobile)
		fgmsFail(w, "Ticket Details not Exists")
		return
	}

	log.Printf("GetGrievenceTicketsStatus %s: 1 ticket", ticket)
	// Field names are FGMS's own, down to the spelling. The mapping reads
	// InsertDateTime as RFC 3339 and refuses an answer without it, so the filing
	// date is sent the way FGMS sends it rather than in a friendlier format.
	writeJSON(w, map[string]any{
		"responseCode":    "1",
		"responseMessage": "Ticket details fetched",
		"recordCount":     1,
		"responseDynamic": []map[string]any{
			{
				"GrievenceSupportTicketNo": ticket,
				"ApplicationNo":            "040108251010160770608",
				"TicketStatus":             "Open",
				"RequestorMobileNo":        mask(mobile),
				"RequestYear":              "2025",
				"RequestSeason":            "1",
				"CropName":                 "Bajra",
				"TicketCategoryID":         3,
				"TicketCategoryName":       "Claim",
				"TicketSubCategoryID":      10,
				"TicketSubCategoryName":    "Claim not received",
				"GrievenceDescription":     "Crop loss claim not updated on the portal.",
				"InsertDateTime":           "2025-12-03T11:02:49.000Z",
				"latestRemark":             "Forwarded to the insurance company for verification.",
			},
		},
	})
}

// getOTP sends the OTP. Its data is a bare string with a trailing space, which
// is PMFBY's, not a slip here.
func (s *server) getOTP(w http.ResponseWriter, r *http.Request) {
	body := read(r)
	mobile := text(body, "mobile")
	if mobile == "" {
		nicFail(w, "Mobile number is required")
		return
	}
	if mobile == s.unknown {
		log.Printf("getOtp %s: unknown mobile", mobile)
		nicFail(w, "Mobile number is not registered with PMFBY")
		return
	}

	s.mu.Lock()
	s.spent[mobile] = false
	s.mu.Unlock()

	log.Printf("getOtp %s: OTP sent", mobile)
	writeJSON(w, map[string]any{
		"data":   "OTP sent successfully to entered mobile number ",
		"error":  "",
		"status": true,
	})
}

// verifyMobile answers the OTP. See the package comment: data.farmerID is the
// one field here the real PMFBY does not send.
func (s *server) verifyMobile(w http.ResponseWriter, r *http.Request) {
	body := read(r)
	mobile := text(body, "mobile")
	otp := text(body, "otp")
	if mobile == "" || otp == "" {
		nicFail(w, "Mobile number and OTP are required")
		return
	}
	if otp != s.otp {
		log.Printf("verifyMobile %s: wrong OTP %q", mobile, otp)
		nicFail(w, "Invalid OTP")
		return
	}

	s.mu.Lock()
	if s.spent[mobile] {
		s.mu.Unlock()
		log.Printf("verifyMobile %s: OTP already spent", mobile)
		nicFail(w, "OTP has already been used")
		return
	}
	s.spent[mobile] = true
	s.mu.Unlock()

	log.Printf("verifyMobile %s: verified", mobile)
	writeJSON(w, map[string]any{
		"data":   map[string]any{"verified": true, "farmerID": farmerID(mobile)},
		"error":  "",
		"status": true,
	})
}

// mobileExists is the internal resolution step the real PMFBY uses and the
// Beckn surface does not expose. Served with its real shape so whatever closes
// the divergence in verifyMobile has something to test against.
func (s *server) mobileExists(w http.ResponseWriter, r *http.Request) {
	mobile := r.URL.Query().Get("mobile")
	if mobile == "" || mobile == s.unknown {
		nicFail(w, "Farmer not found")
		return
	}
	writeJSON(w, map[string]any{
		"status": true,
		"data":   map[string]any{"result": map[string]any{"farmerID": farmerID(mobile)}},
	})
}

// nicToken is the header the two reads carry. PMFBY names it token, not
// Authorization, and sends it bare.
func (s *server) nicToken(w http.ResponseWriter, r *http.Request) bool {
	token := strings.TrimSpace(r.Header.Get("token"))
	if token == "" {
		token = strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
	}
	if token == "" {
		log.Printf("%s %s refused: no token header", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusUnauthorized)
		nicFail(w, "Unauthorized")
		return false
	}
	return true
}

// policyList nests the rows one level down, in applicationList.
func (s *server) policyList(w http.ResponseWriter, r *http.Request) {
	if !s.nicToken(w, r) {
		return
	}
	farmer := r.URL.Query().Get("farmerID")
	if farmer == "" {
		nicFail(w, "farmerID is required")
		return
	}
	// A farmer id ending in 0 holds no policy, so the empty answer is
	// reachable. PMFBY sends an empty array, not a refusal.
	if strings.HasSuffix(farmer, "0") {
		log.Printf("farmerpolicylist %s: none", farmer)
		writeJSON(w, map[string]any{"data": []any{}, "error": "", "status": true})
		return
	}

	log.Printf("farmerpolicylist %s: 1 policy, 2 applications", farmer)
	writeJSON(w, map[string]any{
		"error":  "",
		"status": true,
		"data": []map[string]any{
			{
				"mobile":       "XXXXXX" + last4(farmer),
				"policyID":     "PID" + last4(farmer) + "01",
				"stateName":    "MADHYA PRADESH",
				"districtName": "Sehore",
				"applicationList": []map[string]any{
					{
						"cropName":             "Wheat - IR",
						"claimType":            "Yield Based",
						"createdAt":            "03-12-2023",
						"createdBy":            "BANK",
						"seasonName":           "RABI",
						"seasonYear":           "2023",
						"sumInsured":           19520,
						"claimAmount":          0,
						"claimStatus":          "CALCULATED",
						"farmerShare":          292.8,
						"policyStatus":         "APPROVED",
						"totalPremium":         1171.2,
						"accountNumber":        "XXXXXXXX4411",
						"applicationNo":        "MP2023R" + last4(farmer) + "1",
						"applicationSource":    "BANK",
						"insuranceCompanyName": "AGRICULTURE INSURANCE COMPANY",
					},
					{
						"cropName":             "Gram",
						"claimType":            "Yield Based",
						"createdAt":            "28-11-2023",
						"createdBy":            "CSC",
						"seasonName":           "RABI",
						"seasonYear":           "2023",
						"sumInsured":           31000,
						"claimAmount":          4820,
						"claimStatus":          "APPROVED",
						"farmerShare":          465,
						"policyStatus":         "APPROVED",
						"totalPremium":         1860,
						"accountNumber":        "XXXXXXXX4411",
						"applicationNo":        "MP2023R" + last4(farmer) + "2",
						"applicationSource":    "CSC",
						"insuranceCompanyName": "AGRICULTURE INSURANCE COMPANY",
					},
				},
			},
		},
	})
}

// claimReport keeps its rows flat, and mixes key cases the way PMFBY does.
func (s *server) claimReport(w http.ResponseWriter, r *http.Request) {
	if !s.nicToken(w, r) {
		return
	}
	farmer := r.URL.Query().Get("farmerID")
	if farmer == "" {
		nicFail(w, "farmerID is required")
		return
	}
	if strings.HasSuffix(farmer, "0") {
		log.Printf("claimSearchReport %s: none", farmer)
		writeJSON(w, map[string]any{"data": []any{}, "error": "", "status": true})
		return
	}

	log.Printf("claimSearchReport %s: 2 claims", farmer)
	writeJSON(w, map[string]any{
		"error":  "",
		"status": true,
		"data": []map[string]any{
			{
				"ifsc":               "",
				"Status":             "FRESH Application",
				"amount":             nil,
				"cropID":             "118",
				"cropName":           "Mustard",
				"ClaimType":          nil,
				"UtrNumber":          nil,
				"claimDate":          "",
				"FarmerName":         "Ramesh Patel",
				"TotalClaim":         0,
				"ClaimStatus":        "Claim Calculation Awaited",
				"paymentMode":        "",
				"partialClaim":       0,
				"totalPayable":       nil,
				"accountNumber":      "XXXXXXXX4411",
				"applicationNo":      "MP2025R" + last4(farmer) + "1",
				"claimPaymentStatus": "UNPAID",
				"aadharPayment":      "XXXXXXXX7788",
			},
			{
				"ifsc":               "SBIN0001234",
				"Status":             "Claim Processed",
				"amount":             4820,
				"cropID":             "094",
				"cropName":           "Gram",
				"ClaimType":          "Yield Based",
				"UtrNumber":          "UTR25110012345",
				"claimDate":          "14-10-2025",
				"FarmerName":         "Ramesh Patel",
				"TotalClaim":         4820,
				"ClaimStatus":        "Claim Paid",
				"paymentMode":        "NEFT",
				"partialClaim":       0,
				"totalPayable":       4820,
				"accountNumber":      "XXXXXXXX4411",
				"applicationNo":      "MP2025R" + last4(farmer) + "2",
				"claimPaymentStatus": "PAID",
				"aadharPayment":      "XXXXXXXX7788",
			},
		},
	})
}

func mask(text string) string {
	if len(text) <= 4 {
		return text
	}
	return strings.Repeat("X", len(text)-4) + last4(text)
}

func last4(text string) string {
	if len(text) <= 4 {
		return text
	}
	return text[len(text)-4:]
}

func itoa(n int) string {
	b, _ := json.Marshal(n)
	return string(b)
}
