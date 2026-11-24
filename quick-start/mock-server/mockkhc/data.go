package main

import "fmt"

// Reference data. The row counts, the field names and the absence of paging
// are measured against KHC UAT. The identifiers are this mock's own, derived
// so a child's parent can be recovered from its id:
//
//	district      = state      * 100 + n   (n = 1..38)
//	sub-district  = district   * 100 + n   (n = 1..9)
//	village       = sub-district * 100 + n (n = 1..86)
//
// which is what lets the filing check that a villageId actually sits under the
// stateId sent, a check whose presence in the real service is unobserved.
//
// Scheme 38 and its ten categories are the real ones, copied from UAT on
// 2026-11-24. Everything else is plausible filler and must not be read as
// KHC's master data.

const (
	districtsPerState   = 38
	subDistrictsPerDist = 9
	villagesPerSubDist  = 86
	categoriesPerScheme = 10
	subCategoriesPerCat = 6
)

// aifScheme is the one scheme whose values are real, and the one the published
// catalog advertises.
const aifScheme = 38

var states = []string{
	"Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam",
	"Bihar", "Chandigarh", "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu",
	"Delhi", "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir",
	"Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep", "Madhya Pradesh",
	"Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha",
	"Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
	"Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
}

var districtNames = []string{
	"Araria", "Arwal", "Aurangabad", "Banka", "Begusarai", "Bhagalpur", "Bhojpur",
	"Buxar", "Darbhanga", "Gaya", "Gopalganj", "Jamui", "Jehanabad", "Kaimur",
	"Katihar", "Khagaria", "Kishanganj", "Lakhisarai", "Madhepura", "Madhubani",
	"Munger", "Muzaffarpur", "Nalanda", "Nawada", "Pashchim Champaran", "Patna",
	"Purba Champaran", "Purnia", "Rohtas", "Saharsa", "Samastipur", "Saran",
	"Sheikhpura", "Sheohar", "Sitamarhi", "Siwan", "Supaul", "Vaishali",
}

var subDistrictNames = []string{
	"Sadar", "Bihta", "Danapur", "Fatuha", "Masaurhi", "Paliganj", "Barh",
	"Mokama", "Phulwari Sharif", "Naubatpur", "Dulhin Bazar", "Bakhtiarpur",
}

// Village names repeat across sub-districts, as they do in the real data, so a
// caller that assumes one name resolves to one village is caught. Suffixes are
// how the real data tells two neighbouring villages of the same name apart.
var villageNames = []string{
	"Rampur", "Madhopur", "Bishanpur", "Chakia", "Dharampur", "Gangapur",
	"Harpur", "Jamalpur", "Kishanpur", "Lalganj", "Mahuli", "Narayanpur",
	"Pipra", "Rajapur", "Sahpur", "Tajpur", "Uhlas", "Barauni", "Chainpur",
	"Dumra", "Ekma", "Ghoswari", "Hasanpur", "Itarhi", "Jagdishpur", "Kateya",
	"Laukaha", "Mairwa", "Nagarnausa", "Obra",
}

var villageSuffixes = []string{"", " Khurd", " Kalan"}

var schemeNames = []string{
	"Pradhan Mantri Kisan Samman Nidhi", "Pradhan Mantri Fasal Bima Yojana",
	"Kisan Credit Card", "Soil Health Card", "Paramparagat Krishi Vikas Yojana",
	"National Mission on Edible Oils", "Rashtriya Krishi Vikas Yojana",
	"Mission for Integrated Development of Horticulture",
	"Sub-Mission on Agricultural Mechanization", "National Food Security Mission",
	"Pradhan Mantri Krishi Sinchayee Yojana", "e-National Agriculture Market",
	"Micro Irrigation Fund", "Formation of Farmer Producer Organisations",
	"National Beekeeping and Honey Mission", "Crop Diversification Programme",
	"National Bamboo Mission", "Integrated Scheme for Agricultural Marketing",
	"Agriclinics and Agribusiness Centres", "Seed Village Programme",
	"National Project on Organic Farming", "Rainfed Area Development",
	"Per Drop More Crop", "Sub-Mission on Seeds and Planting Material",
	"Sub-Mission on Agriculture Extension", "National Mission on Natural Farming",
	"Market Intervention Scheme", "Price Support Scheme",
	"Interest Subvention Scheme", "Dairy Entrepreneurship Development Scheme",
	"National Livestock Mission", "Pradhan Mantri Matsya Sampada Yojana",
	"Krishonnati Yojana", "Mission Organic Value Chain Development",
	"National Mission on Agricultural Extension and Technology",
	"Rashtriya Gokul Mission", "Agroforestry Scheme",
}

var divisionNames = []string{
	"Others", "Agriculture", "Horticulture", "Cooperation", "Marketing",
	"Animal Husbandry", "Fisheries", "Extension", "Credit", "Irrigation",
	"Seeds", "Mechanization",
}

// The ten real problem categories of scheme 38, copied from UAT.
var aifCategories = []struct {
	ID   int
	Name string
}{
	{39, "Bank Loan Sanction Issues"},
	{40, "Credit Guarantee Coverage Issues"},
	{41, "Fund Disbursement Issues"},
	{42, "Infrastructure Implementation Issues"},
	{43, "Interest Subvention Issues"},
	{44, "Monitoring & Reporting Issues"},
	{45, "Nodal Agency / Department Issues"},
	{46, "Portal / Technical Issues"},
	{47, "Project Appraisal & Approval Issues"},
	{48, "Registration & Application Issues"},
}

// The real sub-categories of category 39. The sixth is filler: only five were
// recorded against UAT.
var category39 = []struct {
	ID   int
	Name string
}{
	{1725, "Account wrongly classified as NPA"},
	{1726, "Bank refused loan under AIF"},
	{1727, "Excess collateral demanded"},
	{1728, "Interest rate dispute"},
	{1729, "Loan amount reduced without explanation"},
	{1730, "Sanction letter not issued after approval"},
}

var genericCategories = []string{
	"Application Issues", "Payment Issues", "Documentation Issues",
	"Eligibility Issues", "Portal / Technical Issues", "Bank Account Issues",
	"Verification Issues", "Status Not Updated", "Officer Response Issues",
	"Other Issues",
}

var genericSubCategories = []string{
	"Application rejected without reason", "Amount not credited",
	"Documents not accepted", "Declared ineligible wrongly",
	"Portal not accepting the request", "No response from the office",
}

// queryParamOf is the camelCase parameter that selects a list. The rows it
// returns use lowercase unseparated field names instead, on purpose: that
// mismatch is the provider's and a caller assuming one casing throughout is
// caught here.
func queryParamOf(list string) string {
	switch list {
	case "districts", "schemes":
		return "stateId"
	case "sub-districts":
		return "districtId"
	case "villages":
		return "subDistrictId"
	case "categories":
		return "scheme"
	case "sub-categories":
		return "problemCategory"
	}
	return ""
}

// villageState recovers the state a village sits under from its id alone.
func villageState(village int) int {
	return village / 100 / 100 / 100
}

// rowsFor builds one list. A nil result is a 404: a parent this mock does not
// know. An empty non-nil slice would be an answer, and the two are not the
// same thing.
func rowsFor(list string, parent int) []map[string]any {
	switch list {
	case "states":
		rows := make([]map[string]any, 0, len(states))
		for i, name := range states {
			rows = append(rows, map[string]any{"stateid": i + 1, "statename": name})
		}
		return rows

	case "districts":
		if parent < 1 || parent > len(states) {
			return nil
		}
		rows := make([]map[string]any, 0, districtsPerState)
		for n := 1; n <= districtsPerState; n++ {
			rows = append(rows, map[string]any{
				"districtid":   parent*100 + n,
				"districtname": rotate(districtNames, parent, n),
			})
		}
		return rows

	case "sub-districts":
		if state := parent / 100; state < 1 || state > len(states) {
			return nil
		}
		rows := make([]map[string]any, 0, subDistrictsPerDist)
		for n := 1; n <= subDistrictsPerDist; n++ {
			rows = append(rows, map[string]any{
				"subdistrictid":   parent*100 + n,
				"subdistrictname": rotate(subDistrictNames, parent, n),
			})
		}
		return rows

	case "villages":
		if state := parent / 100 / 100; state < 1 || state > len(states) {
			return nil
		}
		rows := make([]map[string]any, 0, villagesPerSubDist)
		for n := 1; n <= villagesPerSubDist; n++ {
			// Rotating by the sub-district is what makes the same village name
			// appear under several of them.
			index := (parent + n - 1) % len(villageNames)
			suffix := villageSuffixes[(n-1)/len(villageNames)%len(villageSuffixes)]
			rows = append(rows, map[string]any{
				"villageid":   parent*100 + n,
				"villagename": villageNames[index] + suffix,
			})
		}
		return rows

	case "schemes":
		if parent < 1 || parent > len(states) {
			return nil
		}
		rows := make([]map[string]any, 0, len(schemeNames))
		for i, name := range schemeNames {
			// Ids 10..46, so the real AIF scheme 38 falls inside the range and
			// keeps its real name, division and division name. No filler name
			// repeats it: two rows answering to one scheme name leaves a tool
			// asking which, on data KHC itself would not return.
			id := 10 + i
			division, divisionName := 20+id%len(divisionNames), divisionNames[id%len(divisionNames)]
			if id == aifScheme {
				name, division, divisionName = "Agriculture Infrastructure Fund (AIF)", 31, "Others"
			}
			rows = append(rows, map[string]any{
				"scheme":       id,
				"schemename":   name,
				"division":     division,
				"divisionname": divisionName,
			})
		}
		return rows

	case "categories":
		if parent < 10 || parent > 10+len(schemeNames)-1 {
			return nil
		}
		if parent == aifScheme {
			rows := make([]map[string]any, 0, len(aifCategories))
			for _, category := range aifCategories {
				rows = append(rows, map[string]any{
					"problemcategory": category.ID, "categoryname": category.Name,
				})
			}
			return rows
		}
		rows := make([]map[string]any, 0, categoriesPerScheme)
		for n := 1; n <= categoriesPerScheme; n++ {
			rows = append(rows, map[string]any{
				"problemcategory": parent*100 + n,
				"categoryname":    genericCategories[(n-1)%len(genericCategories)],
			})
		}
		return rows

	case "sub-categories":
		if parent == 39 {
			rows := make([]map[string]any, 0, len(category39))
			for _, sub := range category39 {
				rows = append(rows, map[string]any{
					"problemsubcategory": sub.ID, "subcategoryname": sub.Name,
				})
			}
			return rows
		}
		if parent <= 0 {
			return nil
		}
		rows := make([]map[string]any, 0, subCategoriesPerCat)
		for n := 1; n <= subCategoriesPerCat; n++ {
			rows = append(rows, map[string]any{
				"problemsubcategory": parent*10 + n,
				"subcategoryname": fmt.Sprintf("%s (%s)",
					genericSubCategories[(n-1)%len(genericSubCategories)],
					categoryLabel(parent)),
			})
		}
		return rows
	}
	return nil
}

// categoryLabel names the parent inside a sub-category's own name, because
// KHC's sub-category row carries no parent field and a flat list of 54 is
// otherwise unusable. It is the mock's way of showing that gap rather than
// papering over it; the real rows do not carry this.
func categoryLabel(category int) string {
	for _, known := range aifCategories {
		if known.ID == category {
			return known.Name
		}
	}
	return fmt.Sprintf("category %d", category)
}

// rotate picks from a pool so each parent gets a different arrangement without
// a table per parent.
func rotate(pool []string, parent, n int) string {
	return pool[(parent+n-1)%len(pool)]
}
