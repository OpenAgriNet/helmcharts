// Command mockkcc stands in for the Kisan Credit Card APIs during local
// end-to-end runs. KCC's own host is PRODUCTION -- an OTP sent there is a real
// SMS to a real farmer's phone -- so a mock is the only way this leg gets
// exercised at all.
//
// Three endpoints, the three the adapter calls:
//
//	POST /auth/login                              form login -> a service token
//	POST /auth/requestOtp                         mobile     -> sends an OTP
//	POST /bharatVistaar/krishika/applicationStatus requestID+otp -> applications
//
// # What it deliberately gets wrong, and why
//
// Every awkwardness here is one the real service has, kept so a mapping that
// passes against this mock also passes against KCC:
//
//   - The login is form-urlencoded, not JSON, and wants entityType beside the
//     credential. A client that posts JSON is refused.
//   - Request and reply bodies are SEALED: AES-256-CBC under a fixed key,
//     written as hex under "data". The IV is the key's own first block.
//   - Only SUCCESSES are sealed. A refusal comes back in clear, as
//     {"status": false, "message": "..."} -- which is the only thing telling
//     the two apart, because opening the envelope replaces the whole body.
//   - applicationStatus answers the OTP and reads the applications in ONE
//     call. There is no verify endpoint, so a wrong OTP is refused here.
//   - The OTP is checked against the requestID rather than spent, so a retry
//     asks the same question again rather than a different one.
//   - A REJECTED application is put back to DRAFT, and only rejectionReason
//     says otherwise. A mapping reading the status alone hides the rejection.
//   - applicationNo comes back as a number on one row and a string on another,
//     and amounts arrive as numbers, as digit strings, and as null.
//
// It does not reproduce KCC's rate limits, its downtime pages, or the real
// shape of its error bodies. Those remain unobserved.
package main

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// issued is one OTP waiting to be answered: the mobile it went to, and when it
// stops being accepted.
type issued struct {
	mobile  string
	expires time.Time
}

type server struct {
	key       []byte
	userID    string
	password  string
	entity    string
	otp       string
	token     string
	unknown   string
	expiresIn int

	mu sync.Mutex
	// OTPs sent and not yet expired, by requestID.
	pending map[string]issued
	// Monotonic, so a requestID is readable in a test log.
	requests int
}

func main() {
	addr := flag.String("addr", ":9105", "address to listen on")
	key := flag.String("key", "dev-only-mock-kcc-key-0123456789",
		"the fixed AES-256 key: 64 hex characters, or 32 plain ones")
	userID := flag.String("user-id", "local-kcc-user", "the userID the login accepts")
	password := flag.String("password", "local-kcc-password", "the password the login accepts")
	entity := flag.String("entity-type", "KRISHIKA", "the entityType the login wants beside the credential")
	otp := flag.String("otp", "123456", "the OTP that verifies")
	token := flag.String("token", "local-kcc-token", "the service token the login issues")
	unknown := flag.String("unknown-mobile", "9999999999",
		"the one mobile KCC has no application for, for reaching the refusal path")
	expiresIn := flag.Int("expires-in", 300, "the expiresIn the OTP answer states, in seconds")
	flag.Parse()

	material, err := keyBytes(*key)
	if err != nil {
		log.Fatalf("mockkcc: -key %v", err)
	}

	s := &server{
		key: material, userID: *userID, password: *password, entity: *entity,
		otp: *otp, token: *token, unknown: *unknown, expiresIn: *expiresIn,
		pending: map[string]issued{},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/auth/login", s.login)
	mux.HandleFunc("/auth/requestOtp", s.requestOTP)
	mux.HandleFunc("/bharatVistaar/krishika/applicationStatus", s.applicationStatus)

	log.Printf("mockkcc listening on %s; otp=%s, unknown mobile=%s", *addr, *otp, *unknown)
	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

// keyBytes reads the key the same way the adapter does: hex at 64 characters,
// raw bytes at 32. One length or the other, so the same text never means two
// different keys.
func keyBytes(raw string) ([]byte, error) {
	raw = strings.TrimSpace(raw)
	switch len(raw) {
	case 64:
		return hex.DecodeString(raw)
	case 32:
		return []byte(raw), nil
	default:
		return nil, fmt.Errorf("must be 64 hex characters or 32 plain ones; got %d", len(raw))
	}
}

// login is form-urlencoded on purpose: KCC's issuer takes a form, and a client
// posting JSON gets nothing back.
func (s *server) login(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		s.refuse(w, "The login body could not be read as a form")
		return
	}
	if r.PostForm.Get("userID") != s.userID || r.PostForm.Get("password") != s.password {
		log.Printf("login refused: userID %q", r.PostForm.Get("userID"))
		s.refuse(w, "Invalid credentials")
		return
	}
	// Checked rather than ignored, so a config that drops the constant beside
	// the credential is caught here and not six months later.
	if r.PostForm.Get("entityType") != s.entity {
		log.Printf("login refused: entityType %q", r.PostForm.Get("entityType"))
		s.refuse(w, "Invalid entityType")
		return
	}
	log.Printf("login: issued a token to %s", s.userID)
	writeJSON(w, map[string]any{
		"status": true,
		"data":   map[string]any{"token": s.token, "sessionTTL": 3600},
	})
}

// requestOTP opens the sealed body, and answers sealed when it can send an OTP
// and in clear when it cannot.
func (s *server) requestOTP(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) {
		return
	}
	var body struct {
		MobileNumber string `json:"mobileNumber"`
		OtpFor       string `json:"otpFor"`
	}
	if !s.openInto(w, r, &body) {
		return
	}
	if body.MobileNumber == "" {
		s.refuse(w, "Mobile number is required")
		return
	}
	if body.MobileNumber == s.unknown {
		log.Printf("requestOtp %s: nothing filed", mask(body.MobileNumber))
		s.refuse(w, "No KCC application found for this mobile number")
		return
	}

	s.mu.Lock()
	s.requests++
	requestID := fmt.Sprintf("RQ-%04d", 9000+s.requests)
	s.pending[requestID] = issued{
		mobile:  body.MobileNumber,
		expires: time.Now().Add(time.Duration(s.expiresIn) * time.Second),
	}
	s.mu.Unlock()

	log.Printf("requestOtp %s: %s, otp=%s", mask(body.MobileNumber), requestID, s.otp)
	// expiresIn is a STRING on the wire, as KCC sends it.
	s.seal(w, map[string]any{
		"requestID": requestID,
		"message":   "OTP sent successfully",
		"expiresIn": fmt.Sprintf("%d", s.expiresIn),
	})
}

// applicationStatus is both halves at once: it checks the OTP and answers with
// the applications. A wrong OTP is refused HERE, because KCC has no separate
// verify endpoint to refuse it on.
func (s *server) applicationStatus(w http.ResponseWriter, r *http.Request) {
	if !s.authorised(w, r) {
		return
	}
	var body struct {
		RequestID string `json:"requestID"`
		// A number on the wire, as KCC's contract says. A quoted OTP fails to
		// decode here, which is the point.
		OTP json.Number `json:"otp"`
	}
	if !s.openInto(w, r, &body) {
		return
	}

	s.mu.Lock()
	held, found := s.pending[body.RequestID]
	s.mu.Unlock()

	switch {
	case !found:
		log.Printf("applicationStatus %q: no such requestID", body.RequestID)
		s.refuse(w, "Invalid requestID")
		return
	case time.Now().After(held.expires):
		log.Printf("applicationStatus %s: expired", body.RequestID)
		s.refuse(w, "OTP expired, please request again")
		return
	case body.OTP.String() != s.otp:
		log.Printf("applicationStatus %s: wrong OTP", body.RequestID)
		s.refuse(w, "Invalid OTP entered")
		return
	}

	log.Printf("applicationStatus %s: answering for %s", body.RequestID, mask(held.mobile))
	s.seal(w, map[string]any{"applications": applicationsFor(held.mobile)})
}

// applicationsFor keys the answer off the mobile's last digit, so every shape
// a mapping has to survive is reachable without changing the mock:
//
//	0  no applications at all
//	1  one sanctioned application
//	   anything else: three, one of them rejected behind a DRAFT status
func applicationsFor(mobile string) []map[string]any {
	sanctioned := map[string]any{
		"applicationNo": 100245, "applicationCurrentStatus": "SANCTIONED",
		// A digit string here and a number below, as KCC mixes them.
		"requiredLoanAmount": "150000", "sanctionedAmount": 140000,
		"bankName": "State Bank of India", "branchName": "Hubballi Main",
		"rejectionReason": nil, "updatedAt": "2026-10-14T10:05:00+05:30",
		"applicationStatusHistory": []map[string]any{
			{"applicationStatus": "SUBMITTED", "createdAt": "2026-09-30T09:00:00+05:30"},
			{"applicationStatus": "UNDER_REVIEW", "createdAt": "2026-10-05T11:20:00+05:30"},
			{"applicationStatus": "SANCTIONED", "createdAt": "2026-10-14T10:05:00+05:30"},
		},
	}
	switch {
	case strings.HasSuffix(mobile, "0"):
		// Not a refusal: a farmer whose application was never filed from this
		// mobile is ANSWERED with an empty list.
		return []map[string]any{}
	case strings.HasSuffix(mobile, "1"):
		return []map[string]any{sanctioned}
	}
	return []map[string]any{
		sanctioned,
		{
			// A string where the row above had a number.
			"applicationNo": "100246", "applicationCurrentStatus": "DRAFT",
			"requiredLoanAmount": 90000, "sanctionedAmount": nil,
			"bankName": "Canara Bank", "branchName": nil,
			"rejectionReason": nil, "updatedAt": "2026-11-02T08:00:00+05:30",
			"applicationStatusHistory": []map[string]any{
				{"applicationStatus": "DRAFT", "createdAt": "2026-11-02T08:00:00+05:30"},
			},
		},
		{
			// Rejected, and the status says DRAFT. Only the reason tells.
			"applicationNo": 100247, "applicationCurrentStatus": "DRAFT",
			"requiredLoanAmount": "200000", "sanctionedAmount": nil,
			"bankName": "Karnataka Gramin Bank", "branchName": "Dharwad",
			"rejectionReason": "Land records do not match the applicant name",
			"updatedAt":       "2026-10-20T16:40:00+05:30",
			"applicationStatusHistory": []map[string]any{
				{"applicationStatus": "SUBMITTED", "createdAt": "2026-10-01T09:00:00+05:30"},
				{"applicationStatus": "REJECTED", "createdAt": "2026-10-20T16:40:00+05:30"},
			},
		},
	}
}

// authorised checks the service token the login issued.
func (s *server) authorised(w http.ResponseWriter, r *http.Request) bool {
	if strings.TrimSpace(r.Header.Get("token")) == s.token {
		return true
	}
	log.Printf("%s %s refused: token %q", r.Method, r.URL.Path, r.Header.Get("token"))
	w.Header().Set("Content-Type", "text/plain")
	w.WriteHeader(http.StatusUnauthorized)
	fmt.Fprintln(w, "Unauthorized")
	return false
}

// openInto reads {"data": "<hex>"}, decrypts it and decodes the plaintext.
func (s *server) openInto(w http.ResponseWriter, r *http.Request, into any) bool {
	var sealed struct {
		Data string `json:"data"`
	}
	if err := json.NewDecoder(r.Body).Decode(&sealed); err != nil || sealed.Data == "" {
		log.Printf("%s: no envelope under data", r.URL.Path)
		s.refuse(w, "Request could not be read")
		return false
	}
	raw, err := hex.DecodeString(sealed.Data)
	if err != nil || len(raw) == 0 || len(raw)%aes.BlockSize != 0 {
		log.Printf("%s: data is not whole AES blocks of hex", r.URL.Path)
		s.refuse(w, "Request could not be decrypted")
		return false
	}
	block, _ := aes.NewCipher(s.key)
	out := make([]byte, len(raw))
	cipher.NewCBCDecrypter(block, s.key[:aes.BlockSize]).CryptBlocks(out, raw)
	plain, err := unpad(out)
	if err != nil {
		log.Printf("%s: %v -- is the caller on the same key?", r.URL.Path, err)
		s.refuse(w, "Request could not be decrypted")
		return false
	}
	if err := json.Unmarshal(plain, into); err != nil {
		log.Printf("%s: the plaintext is not the JSON expected: %v", r.URL.Path, err)
		s.refuse(w, "Request could not be read")
		return false
	}
	return true
}

// seal answers a success: the body encrypted and written as hex under data,
// with status true beside it.
func (s *server) seal(w http.ResponseWriter, value any) {
	plain, err := json.Marshal(value)
	if err != nil {
		log.Printf("could not build the answer: %v", err)
		s.refuse(w, "Internal server error")
		return
	}
	block, _ := aes.NewCipher(s.key)
	padded := pad(plain)
	out := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, s.key[:aes.BlockSize]).CryptBlocks(out, padded)
	writeJSON(w, map[string]any{"status": true, "data": hex.EncodeToString(out)})
}

// refuse answers in CLEAR. KCC seals only its successes, and that asymmetry is
// what the mappings read to tell an answer from a refusal.
func (s *server) refuse(w http.ResponseWriter, message string) {
	writeJSON(w, map[string]any{"status": false, "message": message})
}

func writeJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Printf("could not write the answer: %v", err)
	}
}

func pad(b []byte) []byte {
	n := aes.BlockSize - len(b)%aes.BlockSize
	out := make([]byte, len(b), len(b)+n)
	copy(out, b)
	for i := 0; i < n; i++ {
		out = append(out, byte(n))
	}
	return out
}

func unpad(b []byte) ([]byte, error) {
	if len(b) == 0 {
		return nil, fmt.Errorf("the plaintext is empty")
	}
	n := int(b[len(b)-1])
	if n == 0 || n > aes.BlockSize || n > len(b) {
		return nil, fmt.Errorf("the padding is not PKCS#7")
	}
	for _, c := range b[len(b)-n:] {
		if int(c) != n {
			return nil, fmt.Errorf("the padding is not PKCS#7")
		}
	}
	return b[:len(b)-n], nil
}

// mask keeps a mobile out of the log in full, the way the mappings keep it out
// of an answer.
func mask(mobile string) string {
	if len(mobile) <= 4 {
		return mobile
	}
	return "XXXXXX" + mobile[len(mobile)-4:]
}
