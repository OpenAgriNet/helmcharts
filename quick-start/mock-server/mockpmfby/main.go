// Command mockpmfby stands in for PMFBY's grievance service (KRPH FGMS),
// copying pmfbydemo.amnex.co.in as observed on 2026-10-23.
//
// Every answer is HTTP 200 in FGMS's envelope: responseCode "1" is success and
// "0" a refusal carrying the portal's message. The token from /NICUsersLogin
// is sent bare in Authorization.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"
)

const basePath = "/krphapi/FGMS"

// application is an enrolment the portal knows, with the phone it was made from.
type application struct {
	no, phone, farmer, crop string
}

// The demo's application, which already has a ticket, and one without, so a
// repeat lodge and a fresh one can both be exercised.
var applications = []application{
	{"040108251010160770608", "9001361774", "Fateh Singh", "Green Gram (Moong)"},
	{"040108251010160770606", "9591931134", "Mock Farmer", "Paddy"},
}

// The only category names the demo has shown; the full list is still awaited.
var (
	categoryNames    = map[int]string{3: "Enrollment"}
	subCategoryNames = map[int]string{10: "Portal Issues Login"}
)

// demoLodge is the lodge behind the demo's existing ticket.
var demoLodge = map[string]any{
	"complaintDate": "2026-05-11", "requestYear": "2025", "requestSeason": 1,
	"ticketCategoryID": 3, "ticketSubCategoryID": 10, "grievenceDescription": "Claim is not recieved by me.",
}

var (
	mu sync.Mutex
	// One ticket per application, as the demo keeps.
	tickets = map[string]map[string]any{
		applications[0].no: record("121026000099003", 13,
			time.Date(2026, 10, 12, 11, 2, 49, 0, time.UTC), demoLodge, applications[0]),
	}
	nextNo = 121026000099004
	nextID = 14
)

func main() {
	addr := flag.String("addr", ":9102", "address to listen on")
	user := flag.String("user", "NCIP_USER", "appAccessUID the login accepts")
	pass := flag.String("pass", "pmfby-password", "appAccessPWD the login accepts")
	token := flag.String("token", "local-pmfby-token", "token the login issues")
	flag.Parse()

	handle("NICUsersLogin", "", func(body map[string]any) envelope {
		if missing := absent(body, "appAccessUID", "appAccessPWD"); missing != "" {
			return refused(missing + " is required")
		}
		if body["appAccessUID"] != *user || body["appAccessPWD"] != *pass {
			return refused("UserName or Password does not match!")
		}
		now, until := time.Now().UTC(), time.Now().UTC().Add(30*time.Minute)
		return envelope{ResponseCode: "1", ResponseMessage: "SUCCESS", ResponseDynamic: map[string]any{
			"AppAccessUID": *user, "rcode": 1, "rmessage": "SUCCESS",
			"token": map[string]any{
				"Token":          *token,
				"expirationTime": float64(until.UnixMilli()) / 1000,
				"validFrom":      now.Format(time.RFC3339Nano),
				"validTo":        until.Format(time.RFC3339Nano),
			},
		}}
	})

	handle("AddKRPHNCIPGrievenceSupportTicket", *token, func(body map[string]any) envelope {
		if missing := absent(body, "requestorMobileNo", "complaintDate", "receiptSourceID", "ticketCategoryID",
			"ticketSubCategoryID", "requestYear", "requestSeason", "applicationNo", "grievenceDescription"); missing != "" {
			return refused(missing + " is required")
		}
		app, known := find(fmt.Sprint(body["applicationNo"]))
		if !known {
			return refused("Application Details not Exists")
		}
		if fmt.Sprint(body["requestorMobileNo"]) != app.phone {
			return refused("Mobile Number Not Matched")
		}
		mu.Lock()
		defer mu.Unlock()
		if t, raised := tickets[app.no]; raised {
			return envelope{ResponseCode: "1", ResponseMessage: "Ticket Already Raised for !", ResponseDynamic: ticketRef(t)}
		}
		// ponytail: the demo's message for a fresh ticket is unobserved, since its
		// only application already has one.
		t := record(fmt.Sprint(nextNo), nextID, time.Now(), body, app)
		nextNo++
		nextID++
		tickets[app.no] = t
		return envelope{ResponseCode: "1", ResponseMessage: "Ticket Raised Successfully", ResponseDynamic: ticketRef(t)}
	})

	// Matched on the phone alone: the demo ignores the ticket number unless it
	// is absent, and with no phone answers its first record.
	handle("GetGrievenceTicketsStatus", *token, func(body map[string]any) envelope {
		phone, hasPhone := body["requestorMobileNo"]
		_, hasTicket := body["GrievenceSupportTicketNo"]
		mu.Lock()
		defer mu.Unlock()
		for _, app := range applications {
			t, raised := tickets[app.no]
			if raised && (!hasPhone || (hasTicket && fmt.Sprint(phone) == app.phone)) {
				// A successful read is the one answer whose responseCode is a number.
				return envelope{ResponseCode: 1, ResponseMessage: "Fetched successfully", ResponseDynamic: t}
			}
		}
		return envelope{ResponseCode: "0", ResponseMessage: "No record found", ResponseDynamic: []any{}}
	})

	log.Printf("mockpmfby listening on %s%s", *addr, basePath)
	if err := http.ListenAndServe(*addr, nil); err != nil {
		log.Printf("mockpmfby stopped: %v", err)
		os.Exit(1)
	}
}

// envelope is FGMS's answer to every call.
type envelope struct {
	ResponseObject  any    `json:"responseObject"`
	ResponseDynamic any    `json:"responseDynamic"`
	ResponseCode    any    `json:"responseCode"`
	ResponseMessage string `json:"responseMessage"`
	JSONString      any    `json:"jsonString"`
	RecordCount     int    `json:"recordCount"`
}

func refused(message string) envelope { return envelope{ResponseCode: "0", ResponseMessage: message} }

// handle serves one operation. A non-empty token must arrive bare in Authorization.
func handle(operation, token string, serve func(map[string]any) envelope) {
	http.HandleFunc("POST "+basePath+"/"+operation, func(w http.ResponseWriter, r *http.Request) {
		log.Printf("%s %s", r.Method, r.URL.Path)
		var body map[string]any
		answer := refused("Unexpected token in JSON")
		switch auth := r.Header.Get("Authorization"); {
		case token != "" && auth == "":
			answer = refused("Token required!")
		case token != "" && auth != token:
			answer = refused("jwt malformed")
		case json.NewDecoder(r.Body).Decode(&body) == nil:
			answer = serve(body)
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(answer); err != nil {
			log.Printf("could not write the answer: %v", err)
		}
	})
}

// absent names the first field missing or empty in body, or "" if none is.
func absent(body map[string]any, fields ...string) string {
	for _, field := range fields {
		if value, ok := body[field]; !ok || value == nil || value == "" {
			return field
		}
	}
	return ""
}

func find(no string) (application, bool) {
	for _, app := range applications {
		if app.no == no {
			return app, true
		}
	}
	return application{}, false
}

// record is a ticket as the read returns it, with every field the demo sends.
// Year, season and categories come back as the numbers they were lodged with.
func record(no string, id int, insertedAt time.Time, lodge map[string]any, app application) map[string]any {
	category, subCategory := number(lodge["ticketCategoryID"]), number(lodge["ticketSubCategoryID"])
	return map[string]any{
		"GrievenceSupportTicketID": id, "GrievenceSupportTicketNo": no, "TicketRequestorID": nil,
		"FarmerName": app.farmer, "RequestorMobileNo": app.phone, "ComplaintDate": lodge["complaintDate"],
		"RepresentationType": "SINGLE", "StateCodeAlpha": "0A5BE76C-BDC2-4405-A32C-C46448FF136B",
		"DistrictRequestorID": nil, "VillageRequestorID": nil, "NyayPanchayatID": nil, "NyayPanchayat": nil,
		"GramPanchayatID": "", "GramPanchayat": "", "GrievenceTicketSourceTypeID": 132306,
		"GrievenceSourceOtherType": nil, "TicketSubCategoryID": subCategory, "TicketCategoryID": category,
		"RequestYear": number(lodge["requestYear"]), "RequestSeason": number(lodge["requestSeason"]), "SocialMediaTypeID": nil, "OtherSocialMedia": nil,
		"SocialMediaURL": nil, "ReceiptSourceID": 134306, "VillageName": nil, "CropName": app.crop,
		"TicketStatusID": 109301, "ApplicationNo": app.no, "InsuranceCompanyID": 1,
		"InsurancePolicyNo": app.no[:len(app.no)-2], "Email": nil, "SubDistrictID": nil,
		"SubDistrictName": "Bhadra", "TicketSubCategoryName": nameOrNil(subCategoryNames, subCategory), "TicketStatus": "Open",
		"InsuranceCompany": "AGRICULTURE INSURANCE COMPANY", "TicketCategoryName": nameOrNil(categoryNames, category),
		"StateMasterName": "RAJASTHAN", "DistrictMasterName": "Hanumangarh",
		"GrievenceDescription": lodge["grievenceDescription"], "IsLegal": nil, "LegalRemarks": nil,
		"CPGramRegistrationNumber": nil, "NCIPReferenceNumber": nil, "InsertUserID": 77777,
		"InsertDateTime": insertedAt.UTC().Format("2006-01-02T15:04:05.000Z"), "latestRemark": "",
	}
}

func ticketRef(t map[string]any) map[string]any {
	return map[string]any{"GrievenceSupportTicketNo": t["GrievenceSupportTicketNo"],
		"GrievenceSupportTicketID": t["GrievenceSupportTicketID"]}
}

// number reads a lodged value the way the portal stores it, as an integer.
func number(value any) int {
	n, _ := strconv.Atoi(fmt.Sprint(value))
	return n
}

func nameOrNil(names map[int]string, code int) any {
	if name, known := names[code]; known {
		return name
	}
	return nil
}
