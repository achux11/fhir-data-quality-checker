# FHIR Clinical Data Quality Checker

Reviews HL7 FHIR R4 clinical data for completeness, validity, referential integrity, consistency, and uniqueness — the kind of checks a health information exchange runs on incoming EMR feeds.

**Data:** 593 synthetic patients generated with [Synthea](https://github.com/synthetichealth/synthea) (no real PHI) — **415,939 FHIR R4 resources** (Patient, Encounter, Observation, Condition).

## Checks (19 rules, 5 dimensions)
| Dimension | Examples |
|---|---|
| Completeness | missing birthDate, gender, encounter start, observation code/value |
| Validity | observation codes must be LOINC, condition codes SNOMED CT; vital signs in plausible ranges |
| Referential integrity | encounters/observations pointing to unknown patients or encounters |
| Consistency | encounter ends before it starts; encounter before patient's birth |
| Uniqueness | duplicate encounter IDs (re-sent messages) |

## Results
- Original Synthea data: **786** observations flagged as coded outside LOINC
- Injected **230** realistic EMR-feed errors (missing fields, non-standard codes, HR = 999, orphaned and duplicate encounters): **230 caught (100%)**

*Note: the injected errors match the rule set, so 100% confirms the rules work on known error types — real feeds would surface new ones.*

## Run (Google Colab)
```bash
wget -q https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar -O synthea.jar
java -jar synthea.jar -p 500 -s 42 --exporter.fhir.export=true --exporter.hospital.fhir.export=false --exporter.practitioner.fhir.export=false
python fhir_data_quality_project.py
```
Outputs `dq_issues.csv` and `dq_summary.csv` (Tableau-ready).
