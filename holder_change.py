import datetime
import io
import os
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ใช้ relative path เพื่อให้ทำงานได้ทั้ง Local และ GitHub Actions
TARGET_FOLDER = "./data_holder_change/"
OUTPUT_FILE = "holder_change.csv"
LOOKBACK_DAYS = 2000


def create_session():
    """สร้าง requests session พร้อม retry สำหรับการรันบน GitHub Actions."""
    session = requests.Session()

    retry_strategy = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def main():
    os.makedirs(TARGET_FOLDER, exist_ok=True)

    date_to = datetime.datetime.today().strftime("%Y%m%d")
    date_from = (
        datetime.datetime.today() - datetime.timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y%m%d")

    url = (
        "https://market.sec.or.th/public/idisc/th/Viewmore/r246-2"
        f"?DateType=1&DateFrom={date_from}&DateTo={date_to}"
    )

    print(f"Fetching data from: {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/119.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://market.sec.or.th/",
        "Connection": "keep-alive",
    }

    session = create_session()

    try:
        response = session.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        response.encoding = "utf-8-sig"

        soup = BeautifulSoup(response.text, "html.parser")
        html_tables = soup.find_all("table")

        if not html_tables:
            raise ValueError("No HTML table found on the SEC page.")

        tables = pd.read_html(io.StringIO(response.text))

        if not tables:
            raise ValueError("No data table found on the SEC page.")

        df = tables[0]

        # ค้นหาตำแหน่งคอลัมน์ PDF โดยไม่ fix เลขคอลัมน์
        pdf_column_index = next(
            (
                index
                for index, column in enumerate(df.columns)
                if "PDF" in str(column).upper()
            ),
            None,
        )

        if pdf_column_index is None:
            raise ValueError("PDF column was not found in the SEC table.")

        pdf_column = df.columns[pdf_column_index]
        source_table = html_tables[0]
        pdf_links = []

        for row in source_table.find_all("tr"):
            cells = row.find_all("td")

            # ข้ามหัวตารางและแถวที่โครงสร้างไม่ตรงกับ DataFrame
            if len(cells) != len(df.columns):
                continue

            link_tag = cells[pdf_column_index].find("a", href=True)

            if link_tag:
                pdf_links.append(
                    urljoin(response.url, link_tag["href"].strip())
                )
            else:
                pdf_links.append(None)

        if len(pdf_links) != len(df):
            raise ValueError(
                f"PDF link count ({len(pdf_links)}) does not match "
                f"data row count ({len(df)})."
            )

        # แทนค่าไอคอน/ข้อความในคอลัมน์ PDF ด้วย URL เต็ม
        df[pdf_column] = pdf_links

        output_path = os.path.join(TARGET_FOLDER, OUTPUT_FILE)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")

        print("-" * 50)
        print(f"Success! Saved {len(df)} rows to {output_path}")
        print(f"Total PDF links: {df[pdf_column].notna().sum()}")

    except requests.exceptions.RequestException as error:
        print(f"Request error: {error}")
        raise
    except Exception as error:
        print(f"Error occurred: {error}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
