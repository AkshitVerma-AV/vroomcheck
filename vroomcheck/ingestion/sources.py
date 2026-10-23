"""Catalog of the NHTSA public datasets Vroomcheck ingests.

Column layouts come from NHTSA's published field definitions
(docs/nhtsa_layouts/*.txt). Every flat file is tab-delimited, has no header
row and stores dates as YYYYMMDD strings, so raw tables are loaded as VARCHAR
and typed later in dbt staging models.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BASE = "https://static.nhtsa.gov/odi/ffdd"

COMPLAINT_COLUMNS = [
    "CMPLID", "ODINO", "MFR_NAME", "MAKETXT", "MODELTXT", "YEARTXT", "CRASH", "FAILDATE",
    "FIRE", "INJURED", "DEATHS", "COMPDESC", "CITY", "STATE", "VIN", "DATEA", "LDATE",
    "MILES", "OCCURENCES", "CDESCR", "CMPL_TYPE", "POLICE_RPT_YN", "PURCH_DT",
    "ORIG_OWNER_YN", "ANTI_BRAKES_YN", "CRUISE_CONT_YN", "NUM_CYLS", "DRIVE_TRAIN",
    "FUEL_SYS", "FUEL_TYPE", "TRANS_TYPE", "VEH_SPEED", "DOT", "TIRE_SIZE", "LOC_OF_TIRE",
    "TIRE_FAIL_TYPE", "ORIG_EQUIP_YN", "MANUF_DT", "SEAT_TYPE", "RESTRAINT_TYPE",
    "DEALER_NAME", "DEALER_TEL", "DEALER_CITY", "DEALER_STATE", "DEALER_ZIP", "PROD_TYPE",
    "REPAIRED_YN", "MEDICAL_ATTN", "VEHICLES_TOWED_YN", "STATE_OF_INCIDENT", "VEHICLE_OPERATOR",
]

RECALL_COLUMNS = [
    "RECORD_ID", "CAMPNO", "MAKETXT", "MODELTXT", "YEARTXT", "MFGCAMPNO", "COMPNAME",
    "MFGNAME", "BGMAN", "ENDMAN", "RCLTYPECD", "POTAFF", "ODATE", "INFLUENCED_BY", "MFGTXT",
    "RCDATE", "DATEA", "RPNO", "FMVSS", "DESC_DEFECT", "CONEQUENCE_DEFECT",
    "CORRECTIVE_ACTION", "NOTES", "RCL_CMPT_ID", "MFR_COMP_NAME", "MFR_COMP_DESC",
    "MFR_COMP_PTNO", "DO_NOT_DRIVE", "PARK_OUTSIDE",
]

INVESTIGATION_COLUMNS = [
    "ACTION_NUMBER", "MAKE", "MODEL", "YEAR", "COMPNAME", "MFR_NAME", "ODATE", "CDATE",
    "CAMPNO", "SUBJECT", "SUMMARY",
]

TSB_COLUMNS = [
    "NHTSA_ID", "REPLACEMENT_BULLETIN_NO", "DATE_ADDED", "TSB_DOCUMENT_ID", "MFR_COMM_DATE",
    "MFR_CAMPAIGN_ID", "COMM_TYPE", "MAKE", "MODEL", "MODEL_YEAR", "NHTSA_COMPONENTS",
    "MFR_COMPONENT_SYSTEM", "MFR_COMPONENT_SUBSYSTEM", "SUMMARY",
]


@dataclass(frozen=True)
class Dataset:
    name: str                 # raw table name
    role: str                 # what the dataset contributes (from the pitch)
    files: list[str]          # paths under BASE
    columns: list[str]
    primary_key: list[str]
    date_columns: list[str] = field(default_factory=list)

    def urls(self) -> list[str]:
        return [f"{BASE}/{f}" for f in self.files]


DATASETS: dict[str, Dataset] = {
    "complaints": Dataset(
        name="raw_complaints",
        role="EARLY SIGNAL - owner narratives since 1995",
        files=[
            "cmpl/COMPLAINTS_RECEIVED_2015-2019.zip",
            "cmpl/COMPLAINTS_RECEIVED_2020-2024.zip",
            "cmpl/COMPLAINTS_RECEIVED_2025-2026.zip",
        ],
        columns=COMPLAINT_COLUMNS,
        primary_key=["CMPLID"],
        date_columns=["LDATE", "FAILDATE", "DATEA"],
    ),
    "recalls": Dataset(
        name="raw_recalls",
        role="GROUND TRUTH - every official recall campaign",
        files=["rcl/FLAT_RCL_POST_2010.zip"],
        columns=RECALL_COLUMNS,
        primary_key=["RECORD_ID"],
        date_columns=["RCDATE", "ODATE"],
    ),
    "investigations": Dataset(
        name="raw_investigations",
        role="TIMELINE CHECK - NHTSA defect probes",
        files=["inv/FLAT_INV.zip"],
        columns=INVESTIGATION_COLUMNS,
        primary_key=["ACTION_NUMBER", "MAKE", "MODEL", "YEAR", "COMPNAME"],
        date_columns=["ODATE"],
    ),
    "tsbs": Dataset(
        name="raw_tsbs",
        role="EXTRA EVIDENCE - manufacturer service bulletins",
        files=[
            "tsbs/TSBS_RECEIVED_2015-2019.zip",
            "tsbs/TSBS_RECEIVED_2020-2024.zip",
            "tsbs/TSBS_RECEIVED_2025-2026.zip",
        ],
        columns=TSB_COLUMNS,
        primary_key=["NHTSA_ID"],
        date_columns=["MFR_COMM_DATE"],
    ),
}
