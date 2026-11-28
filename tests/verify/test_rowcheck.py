"""Row check: a record against the Wikipedia table row it cites (offline)."""

from app.verify import promote, rowcheck
from app.verify.rowcheck import CONFIRM, CONTRADICT, INSUFFICIENT, NOPAGE, NOTFOUND, PageRef

SOCKETS = """
<h2>List</h2>
<table class="wikitable">
<tr><th>Socket name</th><th>Year of introduction</th><th>CPU families supported</th>
    <th>Package</th><th>Pin count</th></tr>
<tr><td>Socket AM2</td><td>2006</td><td>Athlon 64</td><td>PGA</td><td>940</td></tr>
<tr><td>Socket AM2+</td><td>2007</td><td>Phenom</td><td>PGA</td><td>940</td></tr>
<tr><td>LGA 1150 / Socket H3</td><td>2013</td><td>Haswell</td><td>LGA</td><td>1150</td></tr>
<tr><td>DIP</td><td>1970s</td><td>Intel 8086</td><td>DIP</td><td>40</td></tr>
</table>
"""

OPTERON = """
<h2>Opteron 100 series</h2>
<table class="wikitable">
<tr><th>Model number</th><th>Clock</th><th>TDP (W)</th><th>Socket</th><th>Release date</th>
    <th>Release price (USD)</th></tr>
<tr><td>140</td><td>1400 MHz</td><td>82.1</td><td>Socket 940</td><td>June 30, 2003</td>
    <td>$229</td></tr>
<tr><td>140</td><td>1400 MHz</td><td>55</td><td>Socket 939</td><td>August 2, 2005</td>
    <td></td></tr>
</table>
"""

CORE = """
<h2>Arrandale</h2>
<table class="wikitable">
<tr><th>Processor branding</th><th>Model</th><th>Clock rate (GHz)</th>
    <th>Integrated GPU / Model</th><th>TDP</th><th>Release date</th></tr>
<tr><td>Core i5</td><td>520E</td><td>2.40</td><td>HD Graphics</td><td>35 W</td>
    <td>January 2010</td></tr>
</table>
"""

CHIPSETS = """
<h2>Northbridges</h2>
<table class="wikitable">
<tr><th>Model</th><th>Codename</th><th>Released</th><th>Fab (nm)</th></tr>
<tr><td>AMD 480X chipset (originally CrossFire Xpress 1600)</td><td>RD480</td>
    <td>Oct 2006</td><td>110</td></tr>
</table>
"""


def page(html: str, title: str, oldid: int | None = None) -> rowcheck.Page:
    return rowcheck.Page(PageRef(title, oldid), 111, tuple(rowcheck.tables_from_html(html)))


def socket(**fields: object) -> dict[str, object]:
    return {"name": "Socket AM2", "manufacturer": "amd", "release_date": "2006",
            "package_type": "PGA", "pin_count": 940, **fields}


def decide(rec: dict[str, object], category: str, pg: rowcheck.Page) -> rowcheck.Decision:
    return rowcheck.check_record(rec, category, pg)


# --- which article ---------------------------------------------------------------


def test_page_ref_reads_plain_and_pinned_urls():
    assert rowcheck.page_ref("https://en.wikipedia.org/wiki/CPU_socket#List") == PageRef(
        "CPU_socket")
    assert rowcheck.page_ref(
        "https://en.wikipedia.org/w/index.php?title=CPU_socket&oldid=1362637560"
    ) == PageRef("CPU_socket", 1362637560)


def test_page_ref_ignores_other_hosts():
    assert rowcheck.page_ref("https://de.wikipedia.org/wiki/CPU") is None
    assert rowcheck.page_ref("https://www.wikidata.org/wiki/Q1") is None
    assert rowcheck.page_ref(None) is None


# --- names -----------------------------------------------------------------------


def test_plus_is_part_of_the_name():
    pg = page(SOCKETS, "CPU_socket")
    assert decide(socket(), "socket", pg).decision == CONFIRM
    # AM2+ is a different row with a different year; it must not answer for AM2.
    assert decide(socket(name="Socket AM2+"), "socket", pg).decision == CONTRADICT
    assert decide(socket(name="Socket AM2+", release_date="2007"), "socket", pg).decision == CONFIRM


def test_unknown_name_is_notfound():
    assert decide(socket(name="Socket AM9"), "socket", page(SOCKETS, "CPU_socket")).decision \
        == NOTFOUND


def test_slash_separated_names_and_aliases():
    pg = page(SOCKETS, "CPU_socket")
    rec = {"name": "LGA 1150", "aliases": ["Socket H3"], "release_date": "2013",
           "package_type": "LGA", "pin_count": 1150}
    assert decide(rec, "socket", pg).decision == CONFIRM
    assert decide({**rec, "name": "Socket H3", "aliases": []}, "socket", pg).decision == CONFIRM


def test_model_under_the_article_family():
    pg = page(OPTERON, "List_of_AMD_Opteron_processors")
    rec = {"name": "AMD Opteron 140", "manufacturer": "amd", "release_date": "2003-06-30",
           "tdp_w": 82.1, "base_clock_ghz": 1.4, "msrp_usd": 229}
    got = decide(rec, "cpu", pg)
    assert got.decision == CONFIRM and got.rows_matched == 2
    assert set(got.agreements) == {"release_date", "tdp_w", "base_clock_ghz", "msrp_usd"}
    # The bare model number of another family is not this row.
    assert decide({**rec, "name": "AMD Athlon 140"}, "cpu", pg).decision == NOTFOUND


def test_branding_and_model_columns_read_together():
    pg = page(CORE, "List_of_Intel_Core_embedded_processors")
    rec = {"name": "Intel Core i5 520E", "manufacturer": "intel",
           "release_date": "2010-01-01", "tdp_w": 35, "base_clock_ghz": 2.4}
    got = decide(rec, "cpu", pg)
    assert got.decision == CONFIRM
    assert set(got.agreements) == {"release_date", "tdp_w", "base_clock_ghz"}


def test_trailing_class_word_and_section():
    pg = page(CHIPSETS, "List_of_AMD_chipsets")
    rec = {"name": "AMD 480X", "manufacturer": "amd", "release_date": "2006-10",
           "series": "Northbridges"}
    got = decide(rec, "chipset", pg)
    assert got.decision == CONFIRM and set(got.agreements) == {"release_date", "series"}
    # A renamed section is no evidence either way.
    assert decide({**rec, "series": "Bridges"}, "chipset", pg).decision == INSUFFICIENT


SOCS = """
<h2>RK33xx</h2>
<table class="wikitable">
<tr><th colspan="2">General</th><th rowspan="2">Fab</th><th rowspan="2">Released</th></tr>
<tr><th>Name</th><th>Code name</th></tr>
<tr><td>RK3368 PX5</td><td>Maple</td><td>28 nm</td><td>2015</td></tr>
<tr><td>OMAP3430</td><td>Maple</td><td>65 nm</td><td>2010</td></tr>
</table>
"""


def test_grouped_name_header_part_code_lists_and_two_word_makers():
    pg = page(SOCS, "Some_chips")
    px5 = {"name": "Rockchip PX5", "manufacturer": "rockchip", "process_nm": 28.0,
           "release_date": "2015-01-01"}
    assert decide(px5, "soc", pg).decision == CONFIRM
    assert decide({**px5, "name": "Rockchip RK3368"}, "soc", pg).decision == CONFIRM
    omap = {"name": "Texas Instruments OMAP 3430", "manufacturer": "texas-instruments",
            "process_nm": 65.0, "release_date": "2010-01-01"}
    assert decide(omap, "soc", pg).decision == CONFIRM
    # A code name is shared by several models and never names the row.
    assert decide({**px5, "name": "Rockchip Maple"}, "soc", pg).decision == NOTFOUND


def test_a_device_goes_by_its_code_name():
    html = """<table class="wikitable">
    <tr><th>Code name</th><th>Market name</th><th>Release</th><th>Weight</th></tr>
    <tr><td>Alpine</td><td>XDA II Pro</td><td>2004</td><td>210 g</td></tr></table>"""
    rec = {"name": "HTC Alpine", "brand": "htc", "release_date": "2004-01-01", "weight_g": 210}
    assert decide(rec, "smartphone", page(html, "Comparison_of_HTC_devices")).decision == CONFIRM


def test_part_codes_only_when_the_cell_is_nothing_else():
    assert rowcheck.part_codes("RK3368 PX5") == ["RK3368", "PX5"]
    assert rowcheck.part_codes("Helio P23 MT6763") == []
    assert rowcheck.part_codes("RK3368") == []


# --- fields ----------------------------------------------------------------------


def test_conflict_blocks_and_is_reported():
    got = decide(socket(pin_count=939), "socket", page(SOCKETS, "CPU_socket"))
    assert got.decision == CONTRADICT
    assert got.conflicts == (("pin_count", "939", "940"),)


def test_one_agreement_is_insufficient():
    rec = socket(package_type=None, pin_count=None)
    assert decide(rec, "socket", page(SOCKETS, "CPU_socket")).decision == INSUFFICIENT


def test_decade_is_not_a_year():
    rec = {"name": "DIP", "release_date": "1970", "package_type": "DIP", "pin_count": 40}
    got = decide(rec, "socket", page(SOCKETS, "CPU_socket"))
    assert got.decision == CONFIRM and "release_date" not in got.agreements


def test_release_precision():
    cmp = rowcheck.cmp_release
    assert cmp("2003-06-30", "June 30, 2003", "") == rowcheck.AGREE
    assert cmp("2003-06-29", "June 30, 2003", "") == rowcheck.CONFLICT
    # First of the month is a month or a year written out, not a claimed day.
    assert cmp("2014-01-01", "September 9, 2014", "") == rowcheck.AGREE
    assert cmp("2012-01-01", "2010", "") == rowcheck.CONFLICT
    assert cmp("2006-10", "Oct 2006", "") == rowcheck.AGREE
    assert cmp(None, "2006", "") == rowcheck.NA
    assert cmp("2006", "?", "") == rowcheck.NA


def test_units():
    assert rowcheck.cmp_ghz(0.1, "100 MHz", "Frequency") == rowcheck.AGREE
    assert rowcheck.cmp_ghz(2.4, "2.40", "Clock rate (GHz)") == rowcheck.AGREE
    assert rowcheck.cmp_ghz(2.4, "2.40", "Clock rate") == rowcheck.NA  # no unit anywhere
    assert rowcheck.cmp_ghz(2.4, "2.66 GHz", "Clock rate") == rowcheck.CONFLICT
    assert rowcheck.cmp_nm(110, "110", "Fab (nm)") == rowcheck.AGREE
    assert rowcheck.cmp_memory_gb(0.5, "256, 512", "Memory / Size (MiB)") == rowcheck.AGREE
    assert rowcheck.cmp_price(229, "$229", "Release price") == rowcheck.AGREE
    assert rowcheck.cmp_price(229, "229", "Notes") == rowcheck.NA


def test_ranges_rounding_and_package_prefix():
    assert rowcheck.cmp_watt(35, "25–45 W", "TDP") == rowcheck.AGREE
    assert rowcheck.cmp_watt(55, "25–45 W", "TDP") == rowcheck.CONFLICT
    assert rowcheck.cmp_watt(11, "11.5 W", "TDP") == rowcheck.AGREE  # record rounded
    assert rowcheck.cmp_watt(54, "53 W", "TDP") == rowcheck.CONFLICT
    assert rowcheck.cmp_text("FCLGA1700", "LGA 1700", "Socket") == rowcheck.AGREE
    assert rowcheck.cmp_text("FCLGA1700", "LGA 1200", "Socket") == rowcheck.CONFLICT


PHONES = """
<table class="wikitable">
<tr><th>Model</th><th>Release date</th><th>Operating system</th><th>RAM</th><th>Battery</th>
    <th>Display</th><th>Weight</th></tr>
<tr><td>Zenfone 5Z</td><td>June 2018</td><td>Android 8.0</td><td>4/6/8 GB LPDDR4</td>
    <td>3300 mAh</td><td>6.2 in, 1080 × 2246</td><td>165 g</td></tr>
</table>
"""


def test_device_rows():
    pg = page(PHONES, "List_of_Asus_smartphones")
    rec = {"name": "Asus Zenfone 5Z", "brand": "asus", "release_date": "2018-06-01",
           "os": "Android", "ram_gb": 6, "battery_mah": 3300, "weight_g": 165,
           "display": {"size_inch": 6.2, "resolution": "2246x1080"}}
    got = decide(rec, "smartphone", pg)
    assert got.decision == CONFIRM
    assert set(got.agreements) == {"release_date", "os", "ram_gb", "battery_mah", "weight_g",
                                   "display.resolution"}
    assert decide({**rec, "ram_gb": 12}, "smartphone", pg).decision == CONTRADICT


def test_a_weak_field_does_not_count():
    pg = page(PHONES, "List_of_Asus_smartphones")
    rec = {"name": "Asus Zenfone 5Z", "brand": "asus", "release_date": "2018-06-01",
           "os": "Android"}
    got = decide(rec, "smartphone", pg)
    assert got.decision == INSUFFICIENT and set(got.agreements) == {"release_date", "os"}
    # ... and a different system is no conflict either: the cell is free text.
    assert decide({**rec, "os": "Tizen", "weight_g": 165}, "smartphone", pg).decision == CONFIRM


def test_storage_and_slash_lists():
    assert rowcheck.cmp_storage_gb(1024, "1 TB, 2 TB or 4 TB SSD", "Storage") == rowcheck.AGREE
    assert rowcheck.cmp_memory_gb(6, "4/6/8 GB LPDDR4 RAM", "RAM") == rowcheck.AGREE
    assert rowcheck.cmp_memory_gb(12, "4/6/8 GB LPDDR4 RAM", "RAM") == rowcheck.CONFLICT


INFOBOX = """
<table class="infobox">
<tr><th colspan="2" class="infobox-above">Galaxy Nexus</th></tr>
<tr><th>Manufacturer</th><td>Samsung</td></tr>
<tr><th>First released</th><td>17 November 2011<sup>[1]</sup></td></tr>
<tr><th>Mass</th><td>135 g (4.8 oz)</td></tr>
<tr><th>Battery</th><td>1,750 mAh</td></tr>
<tr><th>Display</th><td>4.65 in (118 mm), 1280×720</td></tr>
<tr><th>Memory</th><td>1 GB</td></tr>
</table>
"""


def test_infobox_of_the_records_own_article():
    pg = page(INFOBOX, "Galaxy_Nexus")
    rec = {"name": "Samsung Galaxy Nexus", "brand": "samsung", "release_date": "2011-11-17",
           "weight_g": 135, "battery_mah": 1750, "ram_gb": 1,
           "display": {"size_inch": 4.65, "resolution": "720x1280"}}
    got = decide(rec, "smartphone", pg)
    assert got.decision == CONFIRM
    assert set(got.agreements) == {"release_date", "weight_g", "battery_mah", "ram_gb",
                                   "display.size_inch", "display.resolution"}
    assert decide({**rec, "weight_g": 140}, "smartphone", pg).decision == CONTRADICT
    # Another model that merely cites this article is not its subject.
    assert decide({**rec, "name": "Samsung Galaxy Nexus 2"}, "smartphone", pg).decision \
        == NOTFOUND


def test_infobox_subject_named_by_the_article_title():
    html = INFOBOX.replace("Galaxy Nexus</th>", "Google phone</th>")
    rec = {"name": "Galaxy Nexus", "brand": "samsung", "weight_g": 135, "battery_mah": 1750}
    assert decide(rec, "smartphone", page(html, "Galaxy_Nexus_(phone)")).decision == CONFIRM


def test_one_model_of_many_is_not_the_subject_of_the_family_article():
    pg = page(INFOBOX, "Galaxy_Nexus")
    base = {"brand": "samsung", "weight_g": 135, "battery_mah": 1750}
    assert decide({**base, "name": "Samsung Galaxy Nexus (1 GB RAM)", "ram_gb": 1},
                  "smartphone", pg).decision == CONFIRM
    assert decide({**base, "name": "Samsung Galaxy Nexus (Verizon, Late 2011)"},
                  "smartphone", pg).decision == NOTFOUND


def test_or_lists():
    assert rowcheck.cmp_memory_gb(4, "4 or 6 GB LPDDR4 RAM", "Memory") == rowcheck.AGREE
    assert rowcheck.cmp_memory_gb(8, "4 or 6 GB LPDDR4 RAM", "Memory") == rowcheck.CONFLICT


def test_free_text_mismatch_is_not_a_conflict():
    html = """<table class="wikitable">
    <tr><th>Model number</th><th>Fab</th><th>GPU</th><th>Released</th></tr>
    <tr><td>MT6595</td><td>28 nm</td><td>PowerVR G6200 (600 MHz)</td><td>2014</td></tr>
    </table>"""
    rec = {"name": "MediaTek MT6595", "manufacturer": "mediatek", "process_nm": 28.0,
           "release_date": "2014-01-01", "gpu_name": "IMG PowerVR Series6"}
    got = decide(rec, "soc", page(html, "List_of_MediaTek_systems_on_chips"))
    assert got.decision == CONFIRM and "gpu_name" not in got.agreements
    assert decide({**rec, "process_nm": 14.0}, "soc", page(html, "X")).decision == CONTRADICT


# --- fetching and promotion ------------------------------------------------------


def test_each_article_is_fetched_once_and_unreadable_is_no_verdict():
    calls: list[str] = []

    def fetch(ref: PageRef) -> rowcheck.Page | None:
        calls.append(ref.key)
        return page(SOCKETS, ref.title, ref.oldid) if ref.title == "CPU_socket" else None

    store = rowcheck.PageStore(fetch, sleep=lambda _s: None)
    url = "https://en.wikipedia.org/w/index.php?title=CPU_socket&oldid=5"
    for _ in range(3):
        decision, pg = rowcheck.check(socket(source_urls=[url]), "socket", store)
    assert decision.decision == CONFIRM and pg is not None and pg.revid == 111
    assert calls == ["CPU_socket@5"]

    gone = socket(source_urls=["https://en.wikipedia.org/wiki/Gone"])
    assert rowcheck.check(gone, "socket", store) == (rowcheck.Decision(NOPAGE), None)
    assert rowcheck.check(socket(source_urls=["https://example.com"]), "socket", store)[0] \
        .decision == NOPAGE


def test_promotion_follows_the_row_check():
    def decide_(row: str | None, xref: str | None = None, band: str = "yellow"):
        return promote.decide(band=band, source_urls=[], url_cache={},
                              crossref_decision=xref, rowcheck_decision=row)

    assert decide_("confirm") == promote.PromotionDecision(True, "row-confirm")
    assert decide_("contradict", xref="confirm").promote is False
    assert decide_("confirm", xref="contradict").promote is False
    assert decide_("insufficient").promote is False
    assert decide_(None, xref="confirm").reason == "crossref-confirm"
