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
from playwright.sync_api import sync_playwright

OUT = Path("cfo-source-native-pdfs")
OUT.mkdir(exist_ok=True)
UA = "AntonioCalistoPato-CFO-source-research/1.0 (github.com/apato1978/agent-scripts)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
errors: dict[str, str] = {}
records: dict[str, dict] = {}


def get(url: str) -> requests.Response:
    last = None
    for i in range(5):
        try:
            r = S.get(url, timeout=180, allow_redirects=True)
            if r.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"GET failed for {url}: {last}")


def save_pdf(url: str, name: str) -> str:
    r = get(url)
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"not a PDF: {r.url} {r.headers.get('content-type')} {r.content[:30]!r}")
    (OUT / name).write_bytes(r.content)
    return r.url


def discover_pdf(page_url: str, terms: tuple[str, ...]) -> str:
    r = get(page_url)
    soup = BeautifulSoup(r.text, "html.parser")
    choices: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(r.url, a["href"])
        label = " ".join(a.get_text(" ", strip=True).split())
        if ".pdf" in href.lower() or "pdf" in label.lower():
            hay = (href + " " + label).lower()
            choices.append((sum(t.lower() in hay for t in terms), href))
    for m in re.finditer(r"https?://[^\"'<>\s]+\.pdf(?:\?[^\"'<>\s]*)?", r.text, re.I):
        href = html.unescape(m.group(0).replace("\\/", "/"))
        choices.append((sum(t.lower() in href.lower() for t in terms), href))
    choices.sort(reverse=True)
    if not choices or choices[0][0] == 0:
        raise RuntimeError(f"no matching PDF on {page_url}; choices={choices[:10]}")
    return choices[0][1]


def softwareone() -> None:
    name = "04_SoftwareOne_Annual_Report_2025_Page_199_Source_Native.pdf"
    try:
        full = OUT / "_softwareone_full.pdf"
        resolved = save_pdf("https://report.softwareone.com/ar25/app/uploads/SWO-Annual-Report-2025-en.pdf", full.name)
        reader = PdfReader(str(full))
        idx = None
        for i, p in enumerate(reader.pages):
            t = re.sub(r"\s+", " ", p.extract_text() or "")
            if "Rodolfo Savitzky" in t and ("Executive Board" in t or "Aggregate amount" in t):
                idx = i
                break
        if idx is None and len(reader.pages) > 198 and "Rodolfo Savitzky" in (reader.pages[198].extract_text() or ""):
            idx = 198
        if idx is None:
            raise RuntimeError("compensation page not found")
        w = PdfWriter(); w.add_page(reader.pages[idx])
        with (OUT / name).open("wb") as f: w.write(f)
        full.unlink()
        records[name] = {"source_url": resolved, "native_pdf_page": idx + 1}
    except Exception as e:
        errors[name] = repr(e)


def native_pdf(name: str, landing: str, direct: list[str], terms: tuple[str, ...]) -> None:
    attempts = []
    urls = list(direct)
    try: urls.append(discover_pdf(landing, terms))
    except Exception as e: attempts.append(repr(e))
    for url in dict.fromkeys(urls):
        try:
            resolved = save_pdf(url, name)
            records[name] = {"source_url": resolved, "landing": landing}
            return
        except Exception as e: attempts.append(f"{url}: {e!r}")
    errors[name] = " | ".join(attempts)


def inject_base(source: str, url: str) -> str:
    base = f'<base href="{html.escape(url, quote=True)}">'
    if re.search(r"<head\b", source, re.I):
        return re.sub(r"(<head\b[^>]*>)", r"\1" + base, source, count=1, flags=re.I)
    return f"<html><head>{base}</head><body>{source}</body></html>"


def load_page(page, url: str, requests_first: bool) -> tuple[str, str]:
    methods = ["requests", "browser"] if requests_first else ["browser", "requests"]
    failures = []
    for method in methods:
        try:
            if method == "browser":
                page.goto(url, wait_until="domcontentloaded", timeout=120000)
                page.wait_for_timeout(6000)
                resolved = page.url
            else:
                r = get(url)
                page.set_content(inject_base(r.text, r.url), wait_until="domcontentloaded", timeout=120000)
                page.wait_for_timeout(3000)
                resolved = r.url
            text = page.locator("body").inner_text(timeout=20000)
            if len(text.strip()) < 100:
                raise RuntimeError(f"only {len(text)} body characters")
            return resolved, method
        except Exception as e:
            failures.append(f"{method}: {e!r}")
    raise RuntimeError("; ".join(failures))


def isolate(page, marker: str) -> bool:
    return bool(page.evaluate("""marker => {
      const els=[...document.querySelectorAll('body *')];
      let e=els.find(x => (x.innerText||'').includes(marker));
      if(!e) return false;
      let t=e;
      while(t.parentElement && t.parentElement!==document.body){
        const s=(t.parentElement.innerText||'').length;
        if(s>250 && s<8000){t=t.parentElement; if(['SECTION','ARTICLE','MAIN'].includes(t.tagName)) break;}
        else if(s>=8000) break; else t=t.parentElement;
      }
      const c=t.cloneNode(true); document.body.innerHTML='';
      const m=document.createElement('main'); m.style='max-width:900px;margin:auto;padding:28px';
      m.appendChild(c); document.body.appendChild(m); return true;
    }""", marker))


def browser_pdfs() -> None:
    jobs = [
      ("01_Guess_SEC_Exhibit_10.2_Source_Native_Browser_Print.pdf", "https://www.sec.gov/Archives/edgar/data/912463/000091246325000018/employmentagreement-cfogue.htm", True, None),
      ("07_PMI_SEC_Exhibit_10.1_Source_Native_Browser_Print.pdf", "https://www.sec.gov/Archives/edgar/data/1413329/000162828026043531/employmentcontract-massimo.htm", True, None),
      ("08B_ISL_Company_Sponsored_Students_Source_Native_Browser_Print.pdf", "https://www.isl.ch/admissions/school-fees/", False, "Company-sponsored students"),
      ("10A_RealAdvisor_Lausanne_Source_Native_Browser_Print.pdf", "https://realadvisor.ch/en/property-prices/city-lausanne", False, None),
      ("10B_FOPH_2026_Premiums_Source_Native_Browser_Print.pdf", "https://www.bag.admin.ch/en/premiums-and-costs-answers-to-frequently-asked-questions", False, None),
      ("10D_KFF_2025_EHBS_Source_Native_Browser_Print.pdf", "https://www.kff.org/health-costs/annual-family-premiums-for-employer-coverage-rise-6-in-2025-nearing-27000-with-workers-paying-6850-toward-premiums-out-of-their-paychecks/", False, None),
      ("10E_Sirelo_Moving_to_Switzerland_Source_Native_Browser_Print.pdf", "https://sirelo.com/moving-abroad/moving-to-switzerland/", False, None),
    ]
    css = """@media print{[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[class*=modal i],[class*=popup i],[class*=overlay i],iframe,noscript{display:none!important}*{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}body{overflow:visible!important}}"""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        c = b.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36", locale="en-US", viewport={"width":1440,"height":1200}, ignore_https_errors=True)
        for name, url, req_first, section in jobs:
            page = c.new_page()
            try:
                resolved, method = load_page(page, url, req_first)
                isolated = isolate(page, section) if section else False
                page.add_style_tag(content=css); page.emulate_media(media="print")
                u = html.escape(resolved)
                page.pdf(path=str(OUT/name), format="A4", print_background=True, display_header_footer=True,
                    header_template=f'<div style="font-size:6px;color:#555;width:100%;padding:0 8mm;white-space:nowrap;overflow:hidden">Official source: {u}</div>',
                    footer_template='<div style="font-size:7px;color:#555;width:100%;padding:0 8mm;display:flex;justify-content:space-between"><span>Browser-generated PDF from official source</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
                    margin={"top":"16mm","right":"10mm","bottom":"16mm","left":"10mm"}, tagged=True, outline=True)
                records[name] = {"source_url": url, "resolved_url": resolved, "method": method, "isolated_section": isolated}
            except Exception as e:
                errors[name] = repr(e)
            finally: page.close()
        b.close()


def audit() -> None:
    markers = {
      "01_Guess_SEC_Exhibit_10.2_Source_Native_Browser_Print.pdf": ["EMPLOYMENT AGREEMENT", "Article 12"],
      "04_SoftwareOne_Annual_Report_2025_Page_199_Source_Native.pdf": ["Rodolfo Savitzky", "Executive Board"],
      "07_PMI_SEC_Exhibit_10.1_Source_Native_Browser_Print.pdf": ["Annual Base Salary", "Stock Award Program"],
      "08A_ISL_2026_27_Fee_Schedule_Source_Native.pdf": ["SCHOOL FEES", "Tuition"],
      "08B_ISL_Company_Sponsored_Students_Source_Native_Browser_Print.pdf": ["Company-sponsored students", "invoiced directly"],
      "10A_RealAdvisor_Lausanne_Source_Native_Browser_Print.pdf": ["Lausanne", "rent"],
      "10B_FOPH_2026_Premiums_Source_Native_Browser_Print.pdf": ["Premiums in 2026"],
      "10C_AHV_Leaflet_2.01_Source_Native.pdf": ["contribution rates"],
      "10D_KFF_2025_EHBS_Source_Native_Browser_Print.pdf": ["26,993"],
      "10E_Sirelo_Moving_to_Switzerland_Source_Native_Browser_Print.pdf": ["Switzerland", "cost"],
    }
    data = {}
    for path in sorted(OUT.glob("*.pdf")):
        try:
            r=PdfReader(str(path)); text="\n".join((p.extract_text() or "") for p in r.pages)
            data[path.name] = {"pages":len(r.pages),"bytes":path.stat().st_size,"text_chars":len(text),"markers":{m:m.lower() in text.lower() for m in markers.get(path.name,[])},"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"record":records.get(path.name,{})}
        except Exception as e: data[path.name]={"audit_error":repr(e)}
    (OUT/"SOURCE_AUDIT.json").write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
    (OUT/"SOURCE_ERRORS.json").write_text(json.dumps(errors,indent=2,ensure_ascii=False)+"\n")
    (OUT/"SOURCE_PROVENANCE.txt").write_text("Native publisher PDFs: SoftwareOne page, ISL fee schedule, AHV Leaflet 2.01.\nBrowser-generated PDFs: official SEC HTML filings and official publisher webpages, retaining selectable text and displaying the official URL.\nISL employer billing is a separate browser print of the company-sponsored-students section.\n")
    with (OUT/"SHA256SUMS.txt").open("w") as f:
        for p in sorted(OUT.glob("*.pdf")): f.write(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n")


softwareone()
native_pdf("08A_ISL_2026_27_Fee_Schedule_Source_Native.pdf", "https://www.isl.ch/admissions/school-fees/", ["https://www.isl.ch/hubfs/School-Fees-2026-2027.pdf", "https://www.isl.ch/hubfs/School%20Fees%202026-2027.pdf"], ("school","fees","2026","2027"))
native_pdf("10C_AHV_Leaflet_2.01_Source_Native.pdf", "https://www.ahv-iv.ch/p/2.01.e", ["https://www.ahv-iv.ch/p/2.01.e"], ("2.01","salary","contribution"))
browser_pdfs()
audit()
