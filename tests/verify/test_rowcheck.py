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
