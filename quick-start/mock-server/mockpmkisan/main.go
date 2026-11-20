// Command mockpmkisan stands in for PM-KISAN's chatbot and grievance APIs
// during local end-to-end runs, so the adapter's provider leg can be exercised
// before anyone holds real PM-KISAN credentials.
//
// It serves both of the envelopes the real service uses, because the adapter
// picks between them by capability and a mock that spoke only one would leave
// half the path untested:
//
//	aesCbcKeyInBand  the OTP service: base64(AES-128-CBC(PKCS#7(json)))@key,
//	                 the key sent beside the ciphertext as 32 hex characters
//	aesGcm           the grievance service: base64(AES-GCM(json)) under a fixed
//	                 key and nonce taken from the environment
//
// # What it deliberately gets wrong, and why
//
// The real service spells its yes/no flag at least four different ways, and
// every PM-KISAN mapping carries a fallback chain over Rsponce, Responce,
// Status and status to cope. That chain has never been run against anything
// but another fake, so here each endpoint picks a different spelling: whichever
// one a mapping forgot is a visible failure rather than a silent pass.
//
// For the same reason the counts and handles come back as the wrong JSON type
// on purpose -- LatestInstallmentPaid as a string, GrievanceID as a number --
// since the mappings claim to accept either and that claim is untested.
//
// It answers at "d.output" on some endpoints and bare "output" on others, as
// the real one does, so a mapping that reads only one path is caught. Replies
// are keyed off what was asked for, so a request that reached the wrong
// endpoint shows up as wrong data instead of passing.
//
// A missing or wrong service token is answered with plain text where the
// envelope belongs, which is how the real service refuses. The adapter reads
// that as a rejection, so it proves the token was actually sent.
//
// It does not reproduce PM-KISAN's rate limits, its downtime pages, or the
// real shape of its error bodies. Those remain unobserved.
package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash/fnv"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

// blockSize is AES's, and also the length of the key material taken from a
// key string. Named so the padding code below reads as the spec does.
const blockSize = aes.BlockSize

// keySeparator joins the ciphertext and its key on the wire: cipher@key.
const keySeparator = "@"

var (
	addr         = flag.String("addr", ":9102", "address to listen on")
	otpToken     = flag.String("otp-token", "local-pmkisan-otp-token", "token the OTP service requires, in Token")
	serviceToken = flag.String("service-token", "local-pmkisan-service-token", "token the grievance service requires, in TokenNo")
	gcmKeyHex    = flag.String("gcm-key", "", "hex AES key for the grievance envelope (default: PMKISAN_GRIEVANCE_KEY)")
	gcmIVHex     = flag.String("gcm-iv", "", "hex nonce for the grievance envelope (default: PMKISAN_GRIEVANCE_IV)")
	validOTP     = flag.String("otp", "1234", "the OTP that verifies; anything else is refused")
	unknownRegNo = flag.String("unknown-reg-no", "UNKNOWN0000", "the one registration that does NOT exist; every other is found")
)

func main() {
	flag.Parse()
	if *gcmKeyHex == "" {
		*gcmKeyHex = os.Getenv("PMKISAN_GRIEVANCE_KEY")
	}
	if *gcmIVHex == "" {
		*gcmIVHex = os.Getenv("PMKISAN_GRIEVANCE_IV")
	}
	if *gcmKeyHex == "" || *gcmIVHex == "" {
		log.Fatal("the grievance envelope needs a key and a nonce: set -gcm-key and -gcm-iv, " +
			"or PMKISAN_GRIEVANCE_KEY and PMKISAN_GRIEVANCE_IV")
	}
	if _, _, err := gcmCipher(); err != nil {
		log.Fatalf("the grievance key and nonce are not usable: %v", err)
	}

	// The OTP service. Key in band, token in Token, answers under d.output.
	http.HandleFunc("/ChatbotOTP", cbcHandler("d.output", sendOTP))
	http.HandleFunc("/ChatbotOTPVerified", cbcHandler("d.output", verifyOTP))
	// Bare "output" here, so a mapping that reads only d.output is caught.
	http.HandleFunc("/ChatbotUserDetails", cbcHandler("output", userDetails))
	http.HandleFunc("/ChatbotBeneficiaryStatus", cbcHandler("output", beneficiaryStatus))

	// The grievance service. Fixed-key GCM, token in TokenNo.
	http.HandleFunc("/LodgeGrievance", gcmHandler("d.output", lodgeGrievance))
	http.HandleFunc("/GrievanceStatusCheck", gcmHandler("output", grievanceStatus))

	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"status":"ok"}`)
	})

	log.Printf("mockpmkisan listening on %s; OTP %q verifies, every registration but %q exists",
		*addr, *validOTP, *unknownRegNo)
	if err := http.ListenAndServe(*addr, nil); err != nil {
		log.Printf("mockpmkisan stopped: %v", err)
		os.Exit(1)
	}
}

// answer is what an endpoint decided: the JSON to seal back.
type answer map[string]any

// endpoint handles one decrypted request.
type endpoint func(request map[string]any) answer

// ---------------------------------------------------------------------------
// The two envelopes
// ---------------------------------------------------------------------------

// cbcHandler serves an endpoint behind the OTP service's key-in-band envelope.
func cbcHandler(path string, handle endpoint) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sealed, ok := envelopeIn(w, r)
		if !ok {
			return
		}
		ciphertext, keyString := sealed, ""
		if at := strings.LastIndex(sealed, keySeparator); at >= 0 {
			ciphertext, keyString = sealed[:at], sealed[at+1:]
		}
		if keyString == "" {
			// The key travels with the ciphertext; without it there is nothing
			// to decrypt with, which is a caller error rather than a refusal.
			refuse(w, r, "the request carried no key")
			return
		}
		raw, err := base64.StdEncoding.DecodeString(ciphertext)
		if err != nil {
			refuse(w, r, "the request was not base64")
			return
		}
		plain, err := cbcDecrypt(keyString, raw)
		if err != nil {
			refuse(w, r, "the request would not decrypt")
			return
		}

		request, ok := decode(w, r, plain)
		if !ok {
			return
		}
		if text(request["Token"]) != *otpToken {
			// Plain text where the envelope belongs: the real service's refusal.
			refuse(w, r, "invalid token")
			return
		}

		out, err := json.Marshal(handle(request))
		if err != nil {
			refuse(w, r, "the answer could not be encoded")
			return
		}
		back, err := cbcEncrypt(keyString, out)
		if err != nil {
			refuse(w, r, "the answer could not be encrypted")
			return
		}
		// /ChatbotUserDetails names its key on the way back, as the real
		// service may; the rest leave it off so the caller falls back to the
		// key it sent. Both branches of that parse are then exercised.
		if r.URL.Path == "/ChatbotUserDetails" {
			back += keySeparator + keyString
		}
		write(w, r, path, back)
	}
}

// gcmHandler serves an endpoint behind the grievance service's fixed-key GCM.
func gcmHandler(path string, handle endpoint) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sealed, ok := envelopeIn(w, r)
		if !ok {
			return
		}
		raw, err := base64.StdEncoding.DecodeString(sealed)
		if err != nil {
			refuse(w, r, "the request was not base64")
			return
		}
		gcm, nonce, err := gcmCipher()
		if err != nil {
			refuse(w, r, "the envelope is misconfigured here")
			return
		}
		plain, err := gcm.Open(nil, nonce, raw, nil)
		if err != nil {
			refuse(w, r, "the request would not decrypt")
			return
		}

		request, ok := decode(w, r, plain)
		if !ok {
			return
		}
		if text(request["TokenNo"]) != *serviceToken {
			refuse(w, r, "invalid token")
			return
		}

		out, err := json.Marshal(handle(request))
		if err != nil {
			refuse(w, r, "the answer could not be encoded")
			return
		}
		write(w, r, path, base64.StdEncoding.EncodeToString(gcm.Seal(nil, nonce, out, nil)))
	}
}

// envelopeIn reads EncryptedRequest out of the posted body.
func envelopeIn(w http.ResponseWriter, r *http.Request) (string, bool) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return "", false
	}
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		refuse(w, r, "the body was not JSON")
		return "", false
	}
	sealed := text(body["EncryptedRequest"])
	if sealed == "" {
		refuse(w, r, "the body carried no EncryptedRequest")
		return "", false
	}
	return sealed, true
}

// decode reads the decrypted request as a JSON object.
func decode(w http.ResponseWriter, r *http.Request, plain []byte) (map[string]any, bool) {
	var request map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(plain), &request); err != nil {
		refuse(w, r, "the decrypted request was not a JSON object")
		return nil, false
	}
	return request, true
}

// write answers with the sealed string at the configured path.
func write(w http.ResponseWriter, r *http.Request, path, sealed string) {
	log.Printf("%s -> sealed answer at %s", r.URL.Path, path)
	body := map[string]any{"output": sealed}
	if path == "d.output" {
		body = map[string]any{"d": map[string]any{"output": sealed}}
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(body); err != nil {
		log.Printf("could not write the answer: %v", err)
	}
}

// refuse answers 200 with plain text where the envelope belongs, which is how
// the real service refuses. The caller reads it as a rejection, not a crash.
func refuse(w http.ResponseWriter, r *http.Request, why string) {
	log.Printf("%s refused: %s", r.URL.Path, why)
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"d":{"output":%q}}`, "Error: "+why)
}

// ---------------------------------------------------------------------------
// The endpoints
// ---------------------------------------------------------------------------

// sendOTP is /ChatbotOTP. Spells its flag "Rsponce".
func sendOTP(request map[string]any) answer {
	kind, value := text(request["Types"]), text(request["Values"])
	if kind != "Ben_id" && kind != "Mobile" {
		return answer{"Rsponce": "False", "Message": "Unknown Types"}
	}
	if value == "" {
		return answer{"Rsponce": "False", "Message": "Registration number not found"}
	}
	log.Printf("OTP for %s %s is %s", kind, value, *validOTP)
	return answer{"Rsponce": "True", "Message": "OTP sent"}
}

// verifyOTP is /ChatbotOTPVerified. Spells its flag "Responce".
func verifyOTP(request map[string]any) answer {
	if text(request["OTP"]) != *validOTP {
		return answer{"Responce": "False", "Message": "Invalid OTP"}
	}
	return answer{"Responce": "True", "Message": "OTP verified"}
}

// userDetails is /ChatbotUserDetails. Spells its flag "Status", and reports the
// instalment count as a string, which the mapping claims to accept.
func userDetails(request map[string]any) answer {
	value := text(request["Values"])
	if !exists(value) {
		return answer{"Status": "False", "Message": "No record found"}
	}
	paid := 4 + int(hash(value)%12)
	return answer{
		"Status":                "True",
		"Reg_No":                regNoFor(value),
		"LatestInstallmentPaid": fmt.Sprintf("%d", paid),
		"DateOfRegistration":    "2019-02-24T00:00:00",
		"eKYC_Status":           "Y",
		"Name":                  "Mock Beneficiary",
	}
}

// beneficiaryStatus is /ChatbotBeneficiaryStatus, the follow-up read whose
// answer lands at _beneficiaryStatus. Spells its flag "status", the fourth
// spelling, so all four are covered across the six endpoints.
//
// Every field besides the flag and the message is a blocker phrase, and
// "No errors" means there is none -- which is the convention the mapping
// relies on and the reason this endpoint exists separately at all.
func beneficiaryStatus(request map[string]any) answer {
	// Half the registrations are blocked, chosen by the identifier, so both
	// the blocked and the clear path can be reached on demand.
	if hash(text(request["Values"]))%2 == 0 {
		return answer{
			"status":            "true",
			"Message":           "Beneficiary status",
			"IncomeTaxPayeeFlg": "Income Tax Payee",
			"LandSeedingFlg":    "Land Seeding, KYS",
			"OtherFlg":          "No errors",
		}
	}
	return answer{
		"status":            "true",
		"Message":           "Beneficiary status",
		"IncomeTaxPayeeFlg": "No errors",
		"LandSeedingFlg":    "No errors",
	}
}

// lodgeGrievance is /LodgeGrievance. Returns the handle as a number, which the
// mapping claims to accept either way.
func lodgeGrievance(request map[string]any) answer {
	identity := text(request["IdentityNo"])
	if !exists(identity) {
		return answer{"Rsponce": "False", "message": "Registration number not found"}
	}
	if text(request["GrievanceDescription"]) == "" {
		return answer{"Rsponce": "False", "message": "Grievance description is required"}
	}
	return answer{
		"Rsponce":     "True",
		"GrievanceID": int(hash(identity+text(request["GrievanceDescription"]))%900000 + 100000),
		"message":     "Grievance lodged",
	}
}

// grievanceStatus is /GrievanceStatusCheck. Answers under "details" with
// dd/MM/yyyy dates, which is the shape the mapping converts from.
//
// One record is dated today, because the mapping matches on the filing date and
// a lodge followed by a status read happens on the same day.
func grievanceStatus(request map[string]any) answer {
	if !exists(text(request["IdentityNo"])) {
		return answer{"Status": "False", "Message": "No record found"}
	}
	today := time.Now().Format("02/01/2006")
	return answer{
		"Status": "True",
		"details": []map[string]any{
			{
				"GrievanceDate":        today,
				"GrievanceStatus":      "Under Review",
				"GrievanceDescription": "Instalment not received",
				"OfficerReply":         "",
				"OfficeReplyDate":      "",
			},
			{
				"GrievanceDate":        "14/03/2024",
				"GrievanceStatus":      "Disposed",
				"GrievanceDescription": "eKYC not updated",
				"OfficerReply":         "eKYC completed at the CSC.",
				"OfficeReplyDate":      "02/04/2024",
			},
		},
	}
}

// ---------------------------------------------------------------------------
// Crypto, matching the adapter's codecs exactly
// ---------------------------------------------------------------------------

// gcmCipher builds the fixed-key, fixed-nonce AEAD. A fixed nonce breaks GCM;
// it is reproduced here because the real service works this way.
func gcmCipher() (cipher.AEAD, []byte, error) {
	key, err := hex.DecodeString(strings.TrimSpace(*gcmKeyHex))
	if err != nil {
		return nil, nil, fmt.Errorf("the key is not hex: %w", err)
	}
	nonce, err := hex.DecodeString(strings.TrimSpace(*gcmIVHex))
	if err != nil {
		return nil, nil, fmt.Errorf("the nonce is not hex: %w", err)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, nil, err
	}
	gcm, err := cipher.NewGCMWithNonceSize(block, len(nonce))
	if err != nil {
		return nil, nil, err
	}
	return gcm, nonce, nil
}

// keyMaterial is the first 16 bytes of the key string, zero-padded. The key is
// also the IV, as the caller's codec has it.
func keyMaterial(keyString string) []byte {
	key := make([]byte, blockSize)
	copy(key, keyString)
	return key
}

func cbcEncrypt(keyString string, plain []byte) (string, error) {
	key := keyMaterial(keyString)
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	padded := pkcs7Pad(plain, blockSize)
	out := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, key).CryptBlocks(out, padded)
	return base64.StdEncoding.EncodeToString(out), nil
}

func cbcDecrypt(keyString string, raw []byte) ([]byte, error) {
	if len(raw) == 0 || len(raw)%blockSize != 0 {
		return nil, errors.New("ciphertext is not a whole number of blocks")
	}
	key := keyMaterial(keyString)
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	out := make([]byte, len(raw))
	cipher.NewCBCDecrypter(block, key).CryptBlocks(out, raw)
	return pkcs7Unpad(out, blockSize)
}

func pkcs7Pad(data []byte, size int) []byte {
	n := size - len(data)%size
	return append(append([]byte{}, data...), bytes.Repeat([]byte{byte(n)}, n)...)
}

func pkcs7Unpad(data []byte, size int) ([]byte, error) {
	if len(data) == 0 {
		return nil, errors.New("empty plaintext")
	}
	n := int(data[len(data)-1])
	if n == 0 || n > size || n > len(data) {
		return nil, errors.New("bad padding")
	}
	for _, b := range data[len(data)-n:] {
		if int(b) != n {
			return nil, errors.New("bad padding")
		}
	}
	return data[:len(data)-n], nil
}

// exists is whether PM-KISAN holds this registration. Everything is held
// except one configured identifier, so a fixture carrying any id at all gets
// an answer, while the not-found path stays reachable by asking for that one.
func exists(identity string) bool {
	return identity != "" && !strings.EqualFold(identity, *unknownRegNo)
}

// regNoFor echoes a mobile lookup back as a registration number, since that is
// what the caller reads Reg_No for: a Ben_id lookup already has one.
func regNoFor(value string) string {
	if strings.HasPrefix(value, "UP") || strings.HasPrefix(value, "MH") {
		return value
	}
	return fmt.Sprintf("UP%08dA", hash(value)%100000000)
}

// text returns a trimmed string, or "" if the value is not one.
func text(value any) string {
	s, _ := value.(string)
	return strings.TrimSpace(s)
}

// hash makes the answers depend on what was asked for, so a request that
// reached the wrong endpoint shows up as wrong data rather than passing.
func hash(s string) uint32 {
	h := fnv.New32a()
	_, _ = h.Write([]byte(s))
	return h.Sum32()
}
