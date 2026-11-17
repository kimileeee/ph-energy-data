"""Checks for the parsing rules that would silently corrupt the data if they broke.

Run: uv run python -m pipeline.test_pipeline   (also works under pytest)
"""

from pipeline.billcalc import bill
from pipeline.coverage import acronym_key, area_items, full_key, match_areas, norm
from pipeline.doe import month_key, parse_value, slug
from pipeline.extract import check_sums, ground
from pipeline.outages import Places, barangay_mentions, check_notice, key, list_items, window
from pipeline.verify import celcor_bills, meralco_bills, tei_generation


def test_doe_cells():
    assert parse_value(" 12.31 ") == (12.31, "")
    assert parse_value("-0.73") == (-0.73, "")
    assert parse_value("NDA") == (None, "NDA")
    assert parse_value("") == (None, "blank")
    assert parse_value("Jan-16") == (None, "unparsed:Jan-16")  # a date typed into a rate cell
    assert month_key("Jan-18") == "2018-01" and month_key("Nov-26") == "2026-11"


def test_ids():
    assert slug("NEECO II  AREA I") == "neeco-ii-area-i"
    assert slug("LEYECO 1/DORELCO") == "leyeco-1-dorelco"
    assert slug("MORE Power") == "more-power"
    # DOE writes CASURECO1, the coverage source CASURECO I
    assert acronym_key("CASURECO1") == acronym_key("CASURECO I")
    assert acronym_key("SORECO2") == acronym_key("SORECO II")


def test_area_items():
    block = """| {{Collapsible list
 | title = List (3)
 | [[Bucay, Abra|Bucay]]
 | [[Kitaotao]]{{efn|name=some}}
 | [[Samal, Davao del Norte|Samal]]{{efn|Talikud Island is [[Off-grid|off the grid]].}}
}}"""
    assert area_items(block) == [("Bucay, Abra", ""), ("Kitaotao", "Some areas only"),
                                 ("Samal, Davao del Norte", "Talikud Island is off the grid.")]
    one_line = "| title = List (2)|[[Alitagtag]]|[[Batangas City]]{{efn|name=San Jose Sico}}"
    assert area_items(one_line) == [("Alitagtag", ""), ("Batangas City", "San Jose Sico")]


def test_matching():
    provinces = {"P1": "Bataan", "P2": "Davao del Norte"}
    samal_bataan = {"code": "A", "name": "Samal", "province": "P1", "region": "R3"}
    samal_dn = {"code": "B", "name": "Island Garden City of Samal", "province": "P2", "region": "R11"}
    by_key = {"samal": [samal_bataan, samal_dn]}
    assert norm("Island Garden City of Samal") == "samal"
    matched, unmatched = match_areas([("Samal, Davao del Norte", "")], provinces, by_key)
    assert [m["psgc_code"] for m in matched] == ["B"] and not unmatched
    # a province hint that rules out every candidate is a miss, never a guess elsewhere
    matched, unmatched = match_areas([("Samal, Davao del Norte", "")], provinces, {"samal": [samal_bataan]})
    assert not matched and unmatched == [("Samal, Davao del Norte", "")]
    assert full_key("Quezon City") == full_key("City of Quezon") != full_key("Quezon")


def test_meralco_pdf_row():
    text = ("RESIDENTIAL BILLS AT TYPICAL CONSUMPTION LEVELS\n"
            "200 2 ,126.98 142.86 3 ,110.19\n"
            "200 1 0.6349 0.7143 ( 0.0002) 1.2420 - 0 .0100 1 5.5510\n")
    got = meralco_bills(text)
    assert (got["residential"], got["generation"]) == (15.551, 10.6349)
    # the raw tokens the viewer looks for on the page, as printed (spaces removed)
    assert got["_find"] == {"residential": ("15.5510", "last"), "generation": ("10.6349", "first")}
    assert got["_levels"] == {200: 15.551}  # the pesos row (3,110.19) has no 4-decimal total, so only the PHP/kWh row counts
    assert meralco_bills("SUMMARY SCHEDULE OF RATES") is None


def test_celcor_pdf_row():
    text = ("RESIDENTIAL BILLS AT TYPICAL CONSUMPTION LEVELS (OCTOBER 2026)\n"
            "100 750.05 59.54 61.49 - 1,292.68\n"
            "200 1,500.10 119.08 122.98 241.62 98.58 83.78 5.00 - 0.02 4.30 17.91 242.17 77.00 67.18 - 2,579.72\n")
    got = celcor_bills(text)
    assert (got["residential"], got["generation"]) == (12.8986, 7.5005)  # pesos at 200 kWh / 200
    assert got["_levels"] == {100: 12.9268, 200: 12.8986}


def test_bill_formula_reproduces_celcor():
    # CELCOR, Oct 2026: its Schedule of Rates (residential column) against its own 200 kWh typical bill
    per_kwh = {"generation": 7.5005, "transmission": 0.5954, "system loss": 0.6149, "distribution": 1.2081,
               "supply": 0.4929, "metering": 0.4189, "senior citizen subsidy": 0.0001, "RPT": 0.0215}
    b = bill(per_kwh, {"metering per customer": 5.00}, 200, franchise_tax_pct=0.825)
    # every line the bill prints: 1,500.10 / 119.08 / 122.98 / 241.62 / 98.58 / 83.78 / 0.02 / 4.30 / 5.00
    assert b["items"]["generation"] == 1500.10 and b["items"]["distribution"] == 241.62 and b["items"]["RPT"] == 4.30
    assert b["subtotal"] == 2175.46
    # its franchise tax is 0.825% of the charges before it, excluding RPT: 17.91 on the bill
    assert bill({k: v for k, v in per_kwh.items() if k not in ("RPT", "senior citizen subsidy")},
                {"metering per customer": 5.00}, 200, franchise_tax_pct=0.825)["franchise_tax"] == 17.91


def test_image_reader_checks():
    # AEC's October 2026 notice: five component changes and the stated change in the residential rate
    fact = lambda m, v, p: {"measure": m, "value": v, "printed": p, "customer_class": "residential", "subclass": None,
                            "period": "2026-10", "kind": "change_from_previous_month", "consumption_kwh": None, "unit": "PHP/kWh"}
    facts = [fact("effective_rate", 0.4697, "PHP 0.4697"), fact("generation_charge", 0.5147, "0.5147"),
             fact("transmission_charge", -0.0020, "(0.0020)"), fact("ancillary_charge", -0.1075, "(0.1075)"),
             fact("taxes", 0.0038, "0.0038"), fact("other_charges", 0.0606, "0.0606")]
    assert [c["ok"] for c in check_sums(facts)] == [True]  # 0.4696 vs 0.4697: rounding
    facts[1] = fact("generation_charge", 0.6147, "0.6147")  # a misread digit
    assert [c["ok"] for c in check_sums(facts)] == [False]
    # grounding: the printed value must be on the page, in any format the OCR returns it
    lines = [{"text": "PHP 0.4697", "box": [0.1, 0.7, 0.2, 0.72]}, {"text": "(0.1075)", "box": [0.5, 0.7, 0.56, 0.73]}]
    assert ground(fact("effective_rate", 0.4697, "PHP 0.4697"), lines)["box"] == [0.1, 0.7, 0.2, 0.72]
    assert ground(fact("ancillary_charge", -0.1075, "(0.1075)"), lines) is not None
    assert ground(fact("taxes", 0.0038, "0.0038"), lines) is None  # not on the page: flagged "model only"


def test_tei_pdf_row():
    assert tei_generation("1Generation Charge1 PhP/kWh 6.74890 0.71120 6.74890") == 6.7489


def test_outage_windows():
    from datetime import datetime as dt
    # Meralco prints the dates only when a window crosses midnight
    assert window("BETWEEN 11:00PM (WED., 06/10/26) AND 4:00AM (THU., 06/11/26) – PORTION OF CIRCUIT DILIMAN 434VU",
                  "2026-06-10") == (dt(2026, 6, 10, 23), dt(2026, 6, 11, 4))
    assert window("BETWEEN 9:00AM AND 2:00PM", "2026-11-18") == (dt(2026, 11, 18, 9), dt(2026, 11, 18, 14))
    assert window("BETWEEN 10:00PM AND 4:00AM", "2026-11-18")[1] == dt(2026, 11, 19, 4)
    assert window("BETWEEN 12:00 NN AND 3:00 PM", "2026-01-02")[0] == dt(2026, 1, 2, 12)
    try:
        window("FROM 9 TO 5", "2026-01-02")
        raise AssertionError("a window that doesn't parse must raise, not vanish")
    except ValueError:
        pass


def test_outage_barangays():
    assert key("Bgy. Sto. Niño") == key("Santo Niño") and key("Amaya 1") == key("Amaya I")
    assert key("Gen. Trias City") == key("City of General Trias") and key("Garita 1-A") == key("Garita I-A")
    assert list_items("Bucandala I, II, III & IV") == ["Bucandala I", "Bucandala II", "Bucandala III", "Bucandala IV"]
    assert list_items("Bayan Luma 7 – 8") == ["Bayan Luma 7", "Bayan Luma 8"]
    p = Places("meralco")
    got = {n: p.match(n, h, [])["psgc_code"] for n, h, _ in barangay_mentions(
        "in Bgy. B. F. International Village, Las Piñas City to Calcutta St.; Phases 1 & 3 in Bgy. B. F. Homes, Parañaque City.", p)}
    assert got == {"B. F. International Village": "1380200011", "B. F. Homes": "1381000007"}
    # a town name in the middle of a list is a barangay ('San Mateo' in Dasmariñas), not a city that binds the rest
    names = [n for n, h, _ in barangay_mentions("in Bgys. Burol III, San Mateo, San Roque and Santa Fe. Line works", p)]
    assert names == ["Burol III", "San Mateo", "San Roque", "Santa Fe"]
    # a city outside the franchise list binds the list too: nothing in it may match elsewhere
    (n, h, _), = barangay_mentions("facilities in Bgy. Tibag, Baliwag City, Bulacan.", p)
    assert n == "Tibag" and p.match(n, h, list(p.cities))["match"] == "not found"


def test_outage_notice_checks():
    lines = [{"text": "November 13, 2026", "box": [0, 0, 1, 1]}, {"text": "Brgy. Capaya and Brgy. Mining", "box": [0, 0, 1, 1]}]
    read = {"notice_type": "scheduled", "areas": [{"barangay_printed": "Capaya"}, {"barangay_printed": "Mining"}],
            "windows": [{"date": "2026-11-13", "start": "05:00", "end": "10:00", "date_printed": "November 13, 2026", "time_printed": ""}]}
    assert check_notice(read, lines, "11-13-2026 Mining, Capaya") == []
    read["areas"].append({"barangay_printed": "Cutcut"})  # a barangay the page doesn't print
    read["windows"][0]["date"] = "2026-11-14"  # a misread day
    assert len(check_notice(read, lines, "11-13-2026 Mining, Capaya")) == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
