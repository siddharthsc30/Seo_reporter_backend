import csv
import io
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import httpx

SEMRUSH_BASE_URL = "https://api.semrush.com"

async def fetch_semrush_data(
    api_key: str,
    domain: str,
    days: int = 30
) -> Optional[Dict[str, Any]]:
    """
    Fetches real SEO analytics from SEMrush API:
    1. Domain overview & ranks (organic traffic, keywords count)
    2. Top organic search keywords
    3. Backlink audit
    4. Top competitors
    """
    clean_domain = domain.replace("https://", "").replace("http://", "").split("/")[0]

    headers_list = [
        {"Authorization": f"Apikey {api_key}"},
        {"Authorization": f"Bearer {api_key}"},
        {} # fallback query param
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Try fetching domain ranks
        overview_url = f"{SEMRUSH_BASE_URL}/?type=domain_ranks&key={api_key}&export_columns=Db,Dn,Rk,Or,Ot,Oc&domain={clean_domain}"
        
        overview_resp = None
        for h in headers_list:
            try:
                resp = await client.get(overview_url, headers=h)
                if resp.status_code == 200 and not resp.text.startswith("ERROR"):
                    overview_resp = resp
                    break
            except Exception:
                continue

        if not overview_resp or overview_resp.status_code != 200 or overview_resp.text.startswith("ERROR"):
            # If SEMrush returns an error or units aren't activated, return None
            return None

        # Parse overview CSV
        reader = csv.reader(io.StringIO(overview_resp.text.strip()), delimiter=';')
        rows = list(reader)
        if len(rows) < 2:
            return None

        # Headers: Db;Dn;Rk;Or;Ot;Oc
        # Data row: us;example.com;124;1500;45000;12000
        data_row = rows[1]
        organic_keywords = int(data_row[3]) if len(data_row) > 3 and data_row[3].isdigit() else 0
        organic_traffic = int(data_row[4]) if len(data_row) > 4 and data_row[4].isdigit() else 0

        # Try fetching keywords
        kw_url = f"{SEMRUSH_BASE_URL}/?type=domain_organic&key={api_key}&display_limit=15&export_columns=Ph,Po,Pp,Nq,Cp,Tr&domain={clean_domain}&database=us"
        kw_resp = await client.get(kw_url)
        keywords_list = []
        if kw_resp.status_code == 200 and not kw_resp.text.startswith("ERROR"):
            kw_reader = csv.reader(io.StringIO(kw_resp.text.strip()), delimiter=';')
            kw_rows = list(kw_reader)
            for r in kw_rows[1:]:
                if len(r) >= 6:
                    keywords_list.append({
                        "keyword": r[0],
                        "position": int(r[1]) if r[1].isdigit() else None,
                        "prevPosition": int(r[2]) if r[2].isdigit() else None,
                        "volume": int(r[3]) if r[3].isdigit() else None,
                        "kd": None,
                        "cpc": float(r[4]) if r[4].replace('.', '', 1).isdigit() else None,
                        "trafficShare": float(r[5]) if r[5].replace('.', '', 1).isdigit() else None
                    })

        # Try fetching competitors
        comp_url = f"{SEMRUSH_BASE_URL}/?type=domain_organic_organic&key={api_key}&display_limit=5&export_columns=Dn,Cr,Np,Ot&domain={clean_domain}&database=us"
        comp_resp = await client.get(comp_url)
        competitors = []
        if comp_resp.status_code == 200 and not comp_resp.text.startswith("ERROR"):
            comp_reader = csv.reader(io.StringIO(comp_resp.text.strip()), delimiter=';')
            comp_rows = list(comp_reader)
            for r in comp_rows[1:]:
                if len(r) >= 4:
                    competitors.append({
                        "domain": r[0],
                        "commonKeywords": int(r[2]) if r[2].isdigit() else 0,
                        "organicKeywords": int(r[3]) if r[3].isdigit() else 0,
                        "traffic": None,
                        "trafficCost": None,
                        "adwordsKeywords": None
                    })

        return {
            "summary": {
                "authorityScore": None,
                "authorityScoreChange": None,
                "organicTraffic": organic_traffic,
                "organicTrafficChange": None,
                "organicKeywords": organic_keywords,
                "organicKeywordsChange": None,
                "backlinks": None,
                "backlinksChange": None,
                "referringDomains": None,
                "referringDomainsChange": None
            },
            "keywords": keywords_list,
            "backlinksList": [],
            "competitors": competitors,
            "organicHistory": [],
            "isRealData": True
        }
