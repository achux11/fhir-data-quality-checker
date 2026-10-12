"""
FHIR Clinical Data Quality Checker
Covers: HL7 FHIR R4, clinical data review, completeness/validity/consistency checks,
        referential integrity, terminology standards (LOINC, SNOMED CT), root cause, Tableau

Data: Synthea - free, realistic SYNTHETIC patient records exported as FHIR R4 bundles (no real PHI).

Run in Google Colab (no GPU needed, ~5 minutes):
  Cell 1 (generate 500 synthetic patients as FHIR):
      !wget -q https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar -O synthea.jar
      !java -jar synthea.jar -p 500 -s 42 --exporter.fhir.export=true --exporter.hospital.fhir.export=false --exporter.practitioner.fhir.export=false > /dev/null
  Cell 2: paste this file and run it.

How it is tested honestly: Synthea data is very clean, so the script ALSO injects a known
set of realistic errors (like the ones an HIE sees from EMR feeds) into a copy of the data and
measures how many the checks catch (detection rate). Report BOTH numbers.

Outputs: dq_issues.csv, dq_summary.csv  -> load into Tableau Public.
"""
import glob, json, random
import pandas as pd

random.seed(42)
FHIR_DIR = "output/fhir"
LOINC, SNOMED = "http://loinc.org", "http://snomed.info/sct"
VITAL_RANGES = {            # LOINC code: (name, min, max) - physiologically plausible ranges
    "8867-4": ("Heart rate", 20, 250),
    "8310-5": ("Body temperature", 30, 45),
    "29463-7": ("Body weight", 0.3, 400),
    "8302-2": ("Body height", 20, 260),
    "9279-1": ("Respiratory rate", 4, 70),
}

# ---------- 1. Parse FHIR bundles into flat tables ----------
if not glob.glob(f"{FHIR_DIR}/*.json"):
    raise SystemExit(f"No FHIR files in {FHIR_DIR}/ - run the Synthea cell first (see top of file).")
pats, encs, obs, conds = [], [], [], []
for path in glob.glob(f"{FHIR_DIR}/*.json"):
    bundle = json.load(open(path))
    if bundle.get("resourceType") != "Bundle":
        continue
    for entry in bundle.get("entry", []):
        r = entry["resource"]; rt = r["resourceType"]
        if rt == "Patient":
            pats.append({"id": r["id"], "birthDate": r.get("birthDate"), "gender": r.get("gender"),
                         "deceased": r.get("deceasedDateTime"), "has_address": bool(r.get("address"))})
        elif rt == "Encounter":
            p = r.get("period", {})
            encs.append({"id": r["id"], "patient": r.get("subject", {}).get("reference", ""),
                         "start": p.get("start"), "end": p.get("end"), "status": r.get("status")})
        elif rt == "Observation":
            coding = (r.get("code", {}).get("coding") or [{}])[0]
            vq = r.get("valueQuantity", {})
            obs.append({"id": r["id"], "patient": r.get("subject", {}).get("reference", ""),
                        "encounter": r.get("encounter", {}).get("reference", ""),
                        "system": coding.get("system"), "code": coding.get("code"),
                        "value": vq.get("value"), "unit": vq.get("unit"),
                        "has_value": any(k.startswith("value") for k in r) or "component" in r,
                        "effective": r.get("effectiveDateTime")})
        elif rt == "Condition":
            coding = (r.get("code", {}).get("coding") or [{}])[0]
            conds.append({"id": r["id"], "patient": r.get("subject", {}).get("reference", ""),
                          "system": coding.get("system"), "code": coding.get("code"),
                          "onset": r.get("onsetDateTime")})

P, E, O, C = (pd.DataFrame(x) for x in (pats, encs, obs, conds))
# Synthea references look like "urn:uuid:<id>" or "Patient/<id>" -> normalize to bare id
for df in (E, O, C):
    df["patient"] = df["patient"].str.split(r"[:/]").str[-1]
O["encounter"] = O["encounter"].str.split(r"[:/]").str[-1]
print(f"Loaded: {len(P)} patients | {len(E)} encounters | {len(O)} observations | {len(C)} conditions")

# ---------- 2. Data quality checks ----------
def run_checks(P, E, O, C):
    issues = []
    def add(mask, df, resource, dimension, rule):
        for rid in df.loc[mask, "id"]:
            issues.append((resource, rid, dimension, rule))
    pid, eid = set(P["id"]), set(E["id"])
    # Completeness
    add(P["birthDate"].isna(), P, "Patient", "Completeness", "Missing birthDate")
    add(P["gender"].isna(), P, "Patient", "Completeness", "Missing gender")
    add(E["start"].isna(), E, "Encounter", "Completeness", "Missing period.start")
    add(O["code"].isna(), O, "Observation", "Completeness", "Missing code")
    add(~O["has_value"], O, "Observation", "Completeness", "Missing value")
    add(C["code"].isna(), C, "Condition", "Completeness", "Missing code")
    # Validity / terminology standards
    add(O["code"].notna() & (O["system"] != LOINC), O, "Observation", "Validity", "Code not LOINC")
    add(C["code"].notna() & (C["system"] != SNOMED), C, "Condition", "Validity", "Code not SNOMED CT")
    for code, (name, lo, hi) in VITAL_RANGES.items():
        v = pd.to_numeric(O["value"], errors="coerce")
        add((O["code"] == code) & v.notna() & ~v.between(lo, hi), O, "Observation", "Validity",
            f"{name} out of range")
    # Referential integrity
    add(~E["patient"].isin(pid), E, "Encounter", "Integrity", "Encounter -> unknown Patient")
    add(~O["patient"].isin(pid), O, "Observation", "Integrity", "Observation -> unknown Patient")
    add(O["encounter"].ne("") & ~O["encounter"].isin(eid), O, "Observation", "Integrity",
        "Observation -> unknown Encounter")
    # Consistency
    s, e = pd.to_datetime(E["start"], utc=True, errors="coerce"), pd.to_datetime(E["end"], utc=True, errors="coerce")
    add(e.notna() & s.notna() & (e < s), E, "Encounter", "Consistency", "Encounter ends before it starts")
    birth = P.set_index("id")["birthDate"]
    eb = pd.to_datetime(E["patient"].map(birth), utc=True, errors="coerce")
    add(eb.notna() & s.notna() & (s < eb), E, "Encounter", "Consistency", "Encounter before patient birth")
    add(E["id"].duplicated(keep="first"), E, "Encounter", "Uniqueness", "Duplicate Encounter id")
    return pd.DataFrame(issues, columns=["resource", "id", "dimension", "rule"])

baseline = run_checks(P, E, O, C)

# ---------- 3. Inject known errors (simulated bad EMR feed) and measure detection ----------
P2, E2, O2, C2 = P.copy(), E.copy(), O.copy(), C.copy()
injected = []
def corrupt(df, resource, n, col, value, rule):
    idx = random.sample(list(df.index), min(n, len(df)))
    for i in idx:
        df.at[i, col] = value
        injected.append((resource, df.at[i, "id"], rule))
corrupt(P2, "Patient", 10, "birthDate", None, "Missing birthDate")
corrupt(E2, "Encounter", 40, "start", None, "Missing period.start")
corrupt(E2, "Encounter", 40, "patient", "deleted-patient", "Encounter -> unknown Patient")
corrupt(C2, "Condition", 40, "system", "http://hl7.org/fhir/sid/icd-10-cm", "Code not SNOMED CT")
corrupt(O2, "Observation", 40, "system", "local-lab-code", "Code not LOINC")
hr = O2[O2["code"] == "8867-4"].index
for i in random.sample(list(hr), min(40, len(hr))):
    O2.at[i, "value"] = 999                                  # unit/typo error: HR of 999 bpm
    injected.append(("Observation", O2.at[i, "id"], "Heart rate out of range"))
dup = E2.sample(20, random_state=42).copy()                   # resent messages -> duplicates
E2 = pd.concat([E2, dup], ignore_index=True)
injected += [("Encounter", rid, "Duplicate Encounter id") for rid in dup["id"]]

after = run_checks(P2, E2, O2, C2)
inj = pd.DataFrame(injected, columns=["resource", "id", "rule"]).drop_duplicates()
caught = inj.merge(after[["resource", "id", "rule"]].drop_duplicates(), how="inner")
detect = len(caught) / len(inj)

# ---------- 4. Outputs for Tableau ----------
after.to_csv("dq_issues.csv", index=False)
totals = {"Patient": len(P2), "Encounter": len(E2), "Observation": len(O2), "Condition": len(C2)}
summary = after.groupby(["resource", "dimension", "rule"]).size().rename("issues").reset_index()
summary["records"] = summary["resource"].map(totals)
summary["error_rate_pct"] = (100 * summary["issues"] / summary["records"]).round(3)
summary.to_csv("dq_summary.csv", index=False)

total_records = sum(totals.values())
print("\n================ RESULTS ================")
print(f"FHIR resources checked: {total_records:,} across 4 resource types | Rules: 19 across 5 dimensions")
print(f"Issues found in ORIGINAL Synthea data: {len(baseline)}")
if len(baseline):
    print(baseline.groupby(["dimension", "rule"]).size().to_string())
print(f"\nInjected errors: {len(inj)} | Caught: {len(caught)} | Detection rate: {detect:.1%}")
print("\nIssues by rule after injection:")
print(summary.sort_values("issues", ascending=False).to_string(index=False))
print("\nSaved dq_issues.csv and dq_summary.csv (load into Tableau Public)")
