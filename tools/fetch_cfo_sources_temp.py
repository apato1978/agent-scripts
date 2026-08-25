from __future__ import annotations

import hashlib
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader, PdfWriter
from playwright.sync_api import Page, sync_playwright

OUT = Path("cfo-source-native-pdfs")
if OUT.exists():
    for child in OUT.iterdir():
        if child.is_file():
            child.unlink()
OUT.mkdir(exist_ok=True)

UA = (
    "AntonioCalistoPato-CFO-source-research/2.0 "
    "(apato1978@users.noreply.github.com)"
)
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
S = requests.Session()
S.headers.update(
    {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
)
errors: dict[str, str] = {}
records: dict[str, dict] = {}


def get(url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = S.get(url, timeout=180, allow_redirects=True)
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"GET failed for {url}: {last}")


def save_pdf(url: str, name: str) -> str:
    response = get(url)
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"not a PDF: {response.url} {response.headers.get('content-type')} "
            f"{response.content[:50]!r}"
        )
    (OUT / name).write_bytes(response.content)
    return response.url


def discover_pdf(page_url: str, terms: tuple[str, ...]) -> str:
    response = get(page_url)
    if response.content.startswith(b"%PDF"):
        return response.url
    soup = BeautifulSoup(response.text, "html.parser")
    choices: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, anchor["href"])
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if ".pdf" in href.lower() or "pdf" in label.lower():
            haystack = (href + " " + label).lower()
            choices.append((sum(term.lower() in haystack for term in terms), href))
    for match in re.finditer(
        r"https?://[^\"'<>\s]+\.pdf(?:\?[^\"'<>\s]*)?", response.text, re.I
    ):
        href = html.unescape(match.group(0).replace("\\/", "/"))
        choices.append((sum(term.lower() in href.lower() for term in terms), href))
    choices.sort(reverse=True)
    if not choices or choices[0][0] == 0:
        raise RuntimeError(f"no matching PDF on {page_url}; choices={choices[:10]}")
    return choices[0][1]


def softwareone() -> None:
    name = "04_SoftwareOne_Annual_Report_2025_Page_199_Source_Native.pdf"
    try:
        full = OUT / "_softwareone_full.pdf"
        source_url = (
            "https://report.softwareone.com/ar25/app/uploads/"
            "SWO-Annual-Report-2025-en.pdf"
        )
        resolved = save_pdf(source_url, full.name)
        reader = PdfReader(str(full))
        index: int | None = None
        for page_index, page in enumerate(reader.pages):
            text_value = re.sub(r"\s+", " ", page.extract_text() or "")
            if "Rodolfo Savitzky" in text_value and (
                "Executive Board" in text_value or "Aggregate amount" in text_value
            ):
                index = page_index
                break
        if index is None:
            raise RuntimeError("compensation page not found")
        writer = PdfWriter()
        writer.add_page(reader.pages[index])
        with (OUT / name).open("wb") as output_file:
            writer.write(output_file)
        full.unlink()
        records[name] = {
            "source_url": resolved,
            "native_pdf_page_position": index + 1,
            "note": "native extraction from original publisher PDF",
        }
    except Exception as exc:  # noqa: BLE001
        errors[name] = repr(exc)


def native_pdf(
    name: str,
    landing: str,
    direct_urls: list[str],
    terms: tuple[str, ...],
) -> None:
    attempts: list[str] = []
    urls = list(direct_urls)
    try:
        urls.append(discover_pdf(landing, terms))
    except Exception as exc:  # noqa: BLE001
        attempts.append(repr(exc))
    for url in dict.fromkeys(urls):
        try:
            resolved = save_pdf(url, name)
            records[name] = {
                "source_url": resolved,
                "landing": landing,
                "note": "original publisher PDF",
            }
            return
        except Exception as exc:  # noqa: BLE001
            attempts.append(f"{url}: {exc!r}")
    errors[name] = " | ".join(attempts)


def inject_base(source: str, base_url: str) -> str:
    base = f'<base href="{html.escape(base_url, quote=True)}">'
    if re.search(r"<head\b", source, re.I):
        return re.sub(
            r"(<head\b[^>]*>)", r"\1" + base, source, count=1, flags=re.I
        )
    return f"<html><head>{base}</head><body>{source}</body></html>"


def extract_sec_document(
    submission_url: str,
    target_filename: str,
) -> str:
    response = get(submission_url)
    submission = response.content.decode("utf-8", "replace")
    for block in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", submission, re.I | re.S):
        filename_match = re.search(r"<FILENAME>\s*([^\r\n<]+)", block, re.I)
        if not filename_match:
            continue
        filename = filename_match.group(1).strip()
        if filename.lower() != target_filename.lower():
            continue
        text_match = re.search(r"<TEXT>(.*)</TEXT>", block, re.I | re.S)
        if not text_match:
            raise RuntimeError(f"TEXT block missing for {target_filename}")
        source = text_match.group(1).strip()
        if len(source) < 1000:
            raise RuntimeError(f"SEC document unexpectedly short: {len(source)}")
        return source
    raise RuntimeError(f"{target_filename} not found in {submission_url}")


PRINT_CSS = """
@media print {
  [id*='cookie' i], [class*='cookie' i], [id*='consent' i],
  [class*='consent' i], [class*='modal' i], [class*='popup' i],
  [class*='overlay' i], iframe, noscript { display: none !important; }
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
  body { overflow: visible !important; }
}
"""


def print_page(
    page: Page,
    output_name: str,
    displayed_source_url: str,
) -> None:
    page.add_style_tag(content=PRINT_CSS)
    page.emulate_media(media="print")
    safe_url = html.escape(displayed_source_url)
    page.pdf(
        path=str(OUT / output_name),
        format="A4",
        print_background=True,
        display_header_footer=True,
        header_template=(
            '<div style="font-size:6px;color:#555;width:100%;padding:0 8mm;'
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
            f"Official source: {safe_url}</div>"
        ),
        footer_template=(
            '<div style="font-size:7px;color:#555;width:100%;padding:0 8mm;'
            'display:flex;justify-content:space-between;">'
            "<span>Browser-generated PDF from official source</span>"
            '<span><span class="pageNumber"></span> / '
            '<span class="totalPages"></span></span></div>'
        ),
        margin={"top": "16mm", "right": "10mm", "bottom": "16mm", "left": "10mm"},
        tagged=True,
        outline=True,
    )


def isolate_section(page: Page, marker: str) -> bool:
    return bool(
        page.evaluate(
            """marker => {
              const els = [...document.querySelectorAll('body *')];
              let e = els
                .filter(x => (x.innerText || '').includes(marker))
                .sort((a,b) => (a.innerText || '').length - (b.innerText || '').length)[0];
              if (!e) return false;
              let target = e;
              while (target.parentElement && target.parentElement !== document.body) {
                const parent = target.parentElement;
                const length = (parent.innerText || '').length;
                if (length >= 220 && length <= 7000) {
                  target = parent;
                  if (['SECTION','ARTICLE','MAIN'].includes(target.tagName)) break;
                } else if (length > 7000) {
                  break;
                } else {
                  target = parent;
                }
              }
              const clone = target.cloneNode(true);
              document.body.innerHTML = '';
              const main = document.createElement('main');
              main.style = 'max-width:900px;margin:0 auto;padding:24px;font-family:Arial,sans-serif';
              main.appendChild(clone);
              document.body.appendChild(main);
              return true;
            }""",
            marker,
        )
    )


def scroll_page(page: Page) -> None:
    page.evaluate(
        """async () => {
          await new Promise(resolve => {
            let total = 0;
            const distance = 700;
            const timer = setInterval(() => {
              window.scrollBy(0, distance);
              total += distance;
              if (total >= document.body.scrollHeight + 1400) {
                clearInterval(timer);
                window.scrollTo(0, 0);
                resolve();
              }
            }, 80);
          });
        }"""
    )


def click_isl_company_section(page: Page) -> None:
    locator = page.get_by_text("Company-sponsored students", exact=True).first
    locator.scroll_into_view_if_needed(timeout=30000)
    try:
        locator.click(force=True, timeout=30000)
    except Exception:  # noqa: BLE001
        locator.evaluate("el => el.click()")
    page.wait_for_timeout(1800)


def browser_sources() -> None:
    jobs = [
        {
            "name": "08B_ISL_Company_Sponsored_Students_Source_Native_Browser_Print.pdf",
            "urls": ["https://www.isl.ch/admissions/school-fees/"],
            "marker": "Company-sponsored students",
            "extra_marker": "to be invoiced directly",
            "click_isl": True,
            "isolate": True,
        },
        {
            "name": "10A_RealAdvisor_Lausanne_Source_Native_Browser_Print.pdf",
            "urls": [
                "https://realadvisor.ch/en/property-prices/city-lausanne/avenue-de-cour",
                "https://realadvisor.ch/en/property-prices/city-lausanne/rue-de-la-borde",
                "https://realadvisor.ch/en/property-prices/city-lausanne/avenue-des-figuiers",
            ],
            "marker": "Average monthly rents in Lausanne",
            "extra_marker": "Apartment",
            "scroll": True,
            "isolate": True,
        },
        {
            "name": "10B_FOPH_2026_Premiums_Source_Native_Browser_Print.pdf",
            "urls": [
                "https://www.bag.admin.ch/en/premiums-and-costs-answers-to-frequently-asked-questions"
            ],
            "marker": "Premiums in 2026",
            "extra_marker": "4.4",
            "isolate": True,
        },
        {
            "name": "10D_KFF_2025_EHBS_Source_Native_Browser_Print.pdf",
            "urls": [
                "https://www.kff.org/health-costs/annual-family-premiums-for-employer-coverage-rise-6-in-2025-nearing-27000-with-workers-paying-6850-toward-premiums-out-of-their-paychecks/"
            ],
            "marker": "Annual Family Premiums",
            "extra_marker": "26,993",
            "isolate": True,
        },
        {
            "name": "10E_Sirelo_Moving_to_Switzerland_Source_Native_Browser_Print.pdf",
            "urls": ["https://sirelo.com/moving-abroad/moving-to-switzerland/"],
            "marker": "How Much Does It Cost to Move to Switzerland",
            "extra_marker": "cost",
            "isolate": True,
        },
    ]

    sec_jobs = [
        {
            "name": "01_Guess_SEC_Exhibit_10.2_Source_Native_Browser_Print.pdf",
            "submission_url": (
                "https://www.sec.gov/Archives/edgar/data/912463/000091246325000018/"
                "0000912463-25-000018.txt"
            ),
            "official_url": (
                "https://www.sec.gov/Archives/edgar/data/912463/000091246325000018/"
                "employmentagreement-cfogue.htm"
            ),
            "target_filename": "employmentagreement-cfogue.htm",
            "markers": ["EMPLOYMENT AGREEMENT", "Article 12"],
        },
        {
            "name": "07_PMI_SEC_Exhibit_10.1_Source_Native_Browser_Print.pdf",
            "submission_url": (
                "https://www.sec.gov/Archives/edgar/data/1413329/000162828026043531/"
                "0001628280-26-043531.txt"
            ),
            "official_url": (
                "https://www.sec.gov/Archives/edgar/data/1413329/000162828026043531/"
                "employmentcontract-massimo.htm"
            ),
            "target_filename": "employmentcontract-massimo.htm",
            "markers": ["Annual Base Salary", "Stock Award Program"],
        },
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=BROWSER_UA,
            locale="en-US",
            viewport={"width": 1440, "height": 1200},
            ignore_https_errors=True,
        )

        for job in sec_jobs:
            page = context.new_page()
            try:
                source = extract_sec_document(
                    job["submission_url"], job["target_filename"]
                )
                page.set_content(
                    inject_base(source, job["official_url"]),
                    wait_until="domcontentloaded",
                    timeout=120000,
                )
                page.wait_for_timeout(1500)
                body_text = page.locator("body").inner_text(timeout=30000)
                missing = [m for m in job["markers"] if m.lower() not in body_text.lower()]
                if missing:
                    raise RuntimeError(f"missing markers {missing}; preview={body_text[:600]!r}")
                print_page(page, job["name"], job["official_url"])
                records[job["name"]] = {
                    "source_url": job["official_url"],
                    "submission_url": job["submission_url"],
                    "method": "official SEC submission HTML rendered to PDF",
                }
            except Exception as exc:  # noqa: BLE001
                errors[job["name"]] = repr(exc)
            finally:
                page.close()

        for job in jobs:
            page = context.new_page()
            try:
                last_error: Exception | None = None
                resolved_url: str | None = None
                body_text: str | None = None
                for url in job["urls"]:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=120000)
                        page.wait_for_timeout(7000)
                        if job.get("scroll"):
                            scroll_page(page)
                            page.wait_for_timeout(2500)
                        if job.get("click_isl"):
                            click_isl_company_section(page)
                        body_text = page.locator("body").inner_text(timeout=30000)
                        required = [job["marker"], job["extra_marker"]]
                        missing = [m for m in required if m.lower() not in body_text.lower()]
                        if missing:
                            raise RuntimeError(
                                f"missing markers {missing}; title={page.title()!r}; "
                                f"preview={body_text[:900]!r}"
                            )
                        resolved_url = page.url or url
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        page.goto("about:blank")
                if resolved_url is None or body_text is None:
                    raise RuntimeError(f"all official URL candidates failed: {last_error!r}")
                isolated = False
                if job.get("isolate"):
                    isolated = isolate_section(page, job["marker"])
                    if not isolated:
                        raise RuntimeError(f"could not isolate section {job['marker']!r}")
                    body_text = page.locator("body").inner_text(timeout=30000)
                    required = [job["marker"], job["extra_marker"]]
                    missing = [m for m in required if m.lower() not in body_text.lower()]
                    if missing:
                        raise RuntimeError(
                            f"isolated section missing markers {missing}; preview={body_text[:900]!r}"
                        )
                print_page(page, job["name"], resolved_url)
                records[job["name"]] = {
                    "source_url": resolved_url,
                    "method": "direct browser PDF from official webpage",
                    "isolated_section": isolated,
                }
            except Exception as exc:  # noqa: BLE001
                errors[job["name"]] = repr(exc)
            finally:
                page.close()
        browser.close()


def audit() -> None:
    marker_map = {
        "01_Guess_SEC_Exhibit_10.2_Source_Native_Browser_Print.pdf": [
            "EMPLOYMENT AGREEMENT",
            "Article 12",
        ],
        "04_SoftwareOne_Annual_Report_2025_Page_199_Source_Native.pdf": [
            "Rodolfo Savitzky",
            "Executive Board",
        ],
        "07_PMI_SEC_Exhibit_10.1_Source_Native_Browser_Print.pdf": [
            "Annual Base Salary",
            "Stock Award Program",
        ],
        "08A_ISL_2026_27_Fee_Schedule_Source_Native.pdf": ["SCHOOL FEES", "Tuition"],
        "08B_ISL_Company_Sponsored_Students_Source_Native_Browser_Print.pdf": [
            "Company-sponsored students",
            "to be invoiced directly",
        ],
        "10A_RealAdvisor_Lausanne_Source_Native_Browser_Print.pdf": [
            "Average monthly rents in Lausanne",
            "Apartment",
        ],
        "10B_FOPH_2026_Premiums_Source_Native_Browser_Print.pdf": [
            "Premiums in 2026",
            "4.4",
        ],
        "10C_AHV_Leaflet_2.01_Source_Native.pdf": ["contribution rates"],
        "10D_KFF_2025_EHBS_Source_Native_Browser_Print.pdf": [
            "Annual Family Premiums",
            "26,993",
        ],
        "10E_Sirelo_Moving_to_Switzerland_Source_Native_Browser_Print.pdf": [
            "How Much Does It Cost to Move to Switzerland",
            "cost",
        ],
    }
    data: dict[str, dict] = {}
    for path in sorted(OUT.glob("*.pdf")):
        try:
            reader = PdfReader(str(path))
            text_value = "\n".join((page.extract_text() or "") for page in reader.pages)
            markers = marker_map.get(path.name, [])
            data[path.name] = {
                "pages": len(reader.pages),
                "bytes": path.stat().st_size,
                "text_chars": len(text_value),
                "markers": {
                    marker: marker.lower() in text_value.lower() for marker in markers
                },
                "text_preview": re.sub(r"\s+", " ", text_value[:900]),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "record": records.get(path.name, {}),
            }
        except Exception as exc:  # noqa: BLE001
            data[path.name] = {"audit_error": repr(exc)}
    (OUT / "SOURCE_AUDIT.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "SOURCE_ERRORS.json").write_text(
        json.dumps(errors, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "SOURCE_PROVENANCE.txt").write_text(
        "Native publisher PDFs: SoftwareOne annual-report page, ISL fee schedule, "
        "and AHV Leaflet 2.01.\n"
        "Browser-generated PDFs: official SEC filing HTML and official publisher "
        "webpages, retaining selectable text and displaying the official URL.\n"
        "The ISL company-sponsored-students section is embedded separately from "
        "the native 2026/27 fee schedule.\n",
        encoding="utf-8",
    )
    with (OUT / "SHA256SUMS.txt").open("w", encoding="utf-8") as checksums:
        for path in sorted(OUT.glob("*.pdf")):
            checksums.write(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            )


softwareone()
native_pdf(
    "08A_ISL_2026_27_Fee_Schedule_Source_Native.pdf",
    "https://www.isl.ch/admissions/school-fees/",
    [
        "https://www.isl.ch/hubfs/School-Fees-2026-2027.pdf",
        "https://www.isl.ch/hubfs/School%20Fees%202026-2027.pdf",
    ],
    ("school", "fees", "2026", "2027"),
)
native_pdf(
    "10C_AHV_Leaflet_2.01_Source_Native.pdf",
    "https://www.ahv-iv.ch/p/2.01.e",
    ["https://www.ahv-iv.ch/p/2.01.e"],
    ("2.01", "salary", "contribution"),
)
browser_sources()
audit()

required_files = {
    "01_Guess_SEC_Exhibit_10.2_Source_Native_Browser_Print.pdf",
    "04_SoftwareOne_Annual_Report_2025_Page_199_Source_Native.pdf",
    "07_PMI_SEC_Exhibit_10.1_Source_Native_Browser_Print.pdf",
    "08A_ISL_2026_27_Fee_Schedule_Source_Native.pdf",
    "08B_ISL_Company_Sponsored_Students_Source_Native_Browser_Print.pdf",
    "10A_RealAdvisor_Lausanne_Source_Native_Browser_Print.pdf",
    "10B_FOPH_2026_Premiums_Source_Native_Browser_Print.pdf",
    "10C_AHV_Leaflet_2.01_Source_Native.pdf",
    "10D_KFF_2025_EHBS_Source_Native_Browser_Print.pdf",
    "10E_Sirelo_Moving_to_Switzerland_Source_Native_Browser_Print.pdf",
}
missing_files = sorted(name for name in required_files if not (OUT / name).exists())
if errors or missing_files:
    raise RuntimeError(
        "Source generation incomplete: "
        + json.dumps({"errors": errors, "missing_files": missing_files}, indent=2)
    )
