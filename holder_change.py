import datetime
import io
import os
import random
import re
import shutil
import tempfile
import time
import unicodedata
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from urllib3.util.retry import Retry
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["WDM_SSL_VERIFY"] = "0"

# ============================================================
# GitHub/Local configuration
# ============================================================
TARGET_FOLDER = "./data_holder_change/"
OUTPUT_FILE = "holder_change.csv"
LOOKBACK_DAYS = 2000
RESET_OUTPUT_FILES = True


def create_session():
    """สร้าง requests session พร้อม retry สำหรับ Local และ GitHub Actions"""
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


def extract_web_table():
    # --- ส่วนที่ 1: กำหนด Save Path ใหม่ ---
    # ใช้ r นำหน้าเพื่อบอกว่าเป็น Raw String สำหรับ Windows Path
    target_folder = TARGET_FOLDER

    # สร้างโฟลเดอร์ถ้ายังไม่มี (รวมถึงโฟลเดอร์ย่อยทั้งหมด)
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)
        print(f"สร้างโฟลเดอร์สำเร็จที่: {target_folder}")

    # --- ส่วนที่ 2: ตั้งค่าวันที่และ Session ---
    date_to = datetime.datetime.today().strftime("%Y%m%d")
    date_from = (datetime.datetime.today() - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")

    url = f"https://market.sec.or.th/public/idisc/th/Viewmore/r246-2?DateType=1&DateFrom={date_from}&DateTo={date_to}"
    print(f"กำลังดึงข้อมูลจาก: {url}")

    # --- ส่วนที่ 2: ตั้งค่า Session เพื่อป้องกันการโดนตัดการเชื่อมต่อ ---
    session = create_session()

    # Headers ที่เลียนแบบ Chrome Browser จริงๆ
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://market.sec.or.th/",
        "Connection": "keep-alive"
    }

    # --- ส่วนที่ 3: เริ่มการดึงข้อมูล ---
    try:
        # ดึงข้อมูลพร้อมตั้งค่า timeout สำหรับ GitHub Actions
        response = session.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        response.encoding = 'utf-8-sig'
        
        if response.status_code == 200:
            # ใช้ BeautifulSoup แปลง HTML
            soup = BeautifulSoup(response.text, "html.parser")
            
            # ค้นหาตาราง
            html_io = io.StringIO(str(soup))
            tables = pd.read_html(html_io)
            
            if tables:
                df = tables[0]

                # pd.read_html() ไม่เก็บ href ที่ฝังอยู่ในไอคอน PDF
                # ค้นหาตำแหน่งคอลัมน์ PDF จากชื่อหัวตาราง เพื่อไม่ต้อง fix เลขคอลัมน์
                pdf_column_index = next(
                    (index for index, column in enumerate(df.columns) if "PDF" in str(column).upper()),
                    None
                )

                if pdf_column_index is None:
                    raise ValueError("ไม่พบคอลัมน์ PDF ในตาราง SEC")

                pdf_column = df.columns[pdf_column_index]
                source_table = soup.find_all("table")[0]
                pdf_links = []

                for row in source_table.find_all("tr"):
                    cells = row.find_all("td")

                    # ข้ามแถวหัวตาราง และเลือกเฉพาะแถวข้อมูลที่จำนวนคอลัมน์ตรงกับ DataFrame
                    if len(cells) != len(df.columns):
                        continue

                    # ดึง href จากไอคอนในคอลัมน์ PDF ของแถวนั้น
                    link_tag = cells[pdf_column_index].find("a", href=True)
                    if link_tag:
                        pdf_links.append(urljoin(response.url, link_tag["href"].strip()))
                    else:
                        pdf_links.append(None)

                if len(pdf_links) != len(df):
                    raise ValueError(
                        f"จำนวนลิงก์ PDF ({len(pdf_links)}) ไม่ตรงกับจำนวนข้อมูล ({len(df)})"
                    )

                # เก็บ URL เต็มลงในคอลัมน์ PDF เดิม
                df[pdf_column] = pdf_links
                
                # --- ส่วนที่ 4: บันทึกไฟล์ลง Path ที่กำหนด ---
                file_name = OUTPUT_FILE
                full_path = os.path.join(target_folder, file_name)
                
                df.to_csv(full_path, index=False, encoding='utf-8-sig')
                
                print("-" * 30)
                print(f"บันทึกไฟล์สำเร็จ!")
                print(f"ที่อยู่ไฟล์: {full_path}")
                print(f"จำนวนข้อมูล: {len(df)} แถว")
                print(f"จำนวนลิงก์ PDF: {df[pdf_column].notna().sum()} ลิงก์")
            else:
                print("ไม่พบตารางข้อมูลในหน้านี้")
        else:
            print(f"ไม่สามารถเข้าถึงเว็บไซต์ได้ Status Code: {response.status_code}")

    except Exception as error:
        print(f"เกิดข้อผิดพลาด: {error}")
        raise
    finally:
        session.close()

# ============================================================
# อ่าน PDF ในคอลัมน์ PDF แล้วสร้าง 1 PDF = 1 row
# ============================================================

INPUT_CSV = os.path.join(TARGET_FOLDER, "holder_change.csv")
OUTPUT_CSV = os.path.join(TARGET_FOLDER, "holder_change_pdf_extracted.csv")
CHECKPOINT_CSV = os.path.join(TARGET_FOLDER, "holder_change_pdf_checkpoint.csv")
ERROR_CSV = os.path.join(TARGET_FOLDER, "holder_change_pdf_errors.csv")

# ใช้ None เพื่อรันทั้งหมด หรือใส่ตัวเลข เช่น 10 สำหรับทดสอบ 10 ไฟล์แรก
MAX_FILES = None
# เลือกเปิดใช้งานเพียง 1 แบบ โดยใส่ # ปิดอีกแบบหนึ่งไว้

# ============================================================
# อ่าน URL เฉพาะหลักทรัพย์ที่กำหนด (เทียบแบบไม่สนตัวพิมพ์เล็ก/ใหญ่)
TARGET_SECURITIES = {"BCP", "BCPG", "BBGI"}
# อ่าน URL ทั้งหมด
# TARGET_SECURITIES = None
# ============================================================

SAVE_EVERY = 20
REQUEST_DELAY = (0.20, 0.50)
SELENIUM_DOWNLOAD_TIMEOUT = 45


OUTPUT_COLUMNS = [
    "source_row", "source_pdf_url", "source_reference_no",
    "reference_no", "form_type", "company_name", "security_symbol",
    "transaction_date", "transaction_type", "transaction_channel",
    "broker_name", "biglot", "section_2_2", "section_2_3", "submission_date",
    "highest_price_90_days", "highest_price_date",
    "reporter_name", "contact_person", "report_purpose",
    "section_8_1_stock_type", "section_8_2_convertible_type",
    "section_8_3_other_type",
    "common_before_units", "common_before_voting_rights", "common_before_percent",
    "common_transaction_units", "common_transaction_voting_rights", "common_transaction_percent",
    "common_after_units", "common_after_voting_rights", "common_after_percent",
    "preferred_before_units", "preferred_before_voting_rights", "preferred_before_percent",
    "preferred_transaction_units", "preferred_transaction_voting_rights", "preferred_transaction_percent",
    "preferred_after_units", "preferred_after_voting_rights", "preferred_after_percent",
    "section_10", "section_11", "section_12",
    "reference_match", "extraction_status", "error_message"
]


def clean_text(value):
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", str(value))
    value = re.sub(r"\(cid:\d+\)", "", value)
    value = value.replace("\u200b", "").replace("\xa0", " ")
    # แก้ลำดับสระ/วรรณยุกต์ที่มักผิดจาก wkhtmltopdf
    value = value.replace("ํา", "ำ").replace("า่", "่า").replace("า้", "้า")
    value = value.replace("หน่า ย", "หน่าย").replace("ผ่า น", "ผ่าน").replace("หน่ว ย", "หน่วย")
    return re.sub(r"\s+", " ", value).strip()


def compact_text(value):
    return re.sub(r"\s+", "", clean_text(value))


def first_match(pattern, text, flags=re.S):
    match = re.search(pattern, text, flags)
    return clean_text(match.group(1)) if match else ""


def selected(label, section):
    pattern = rf"\(\s*[✓✔√]\s*\)\s*{label}"
    return bool(re.search(pattern, section, re.I))


def section_between(text, start_no, end_no=None):
    if end_no is None:
        pattern = rf"(?ms)^\s*{start_no}\.\s*(.*)$"
    else:
        pattern = rf"(?ms)^\s*{start_no}\.\s*(.*?)(?=^\s*{end_no}\.\s*)"
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def numeric(value, percent=False):
    value = clean_text(value).replace(",", "")
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    number = float(match.group())
    return number if percent else int(number)


def find_holding_rows(pdf):
    """รวมรายการในข้อ 9 แยกหุ้นสามัญ/หุ้นบุริมสิทธิ โดยไม่ใช้แถวรวมซ้ำ"""
    buckets = {
        "common": [0, 0, 0.0, 0, 0, 0.0, 0, 0, 0.0],
        "preferred": [0, 0, 0.0, 0, 0, 0.0, 0, 0, 0.0],
    }
    found = {"common": False, "preferred": False}

    for page in pdf.pages:
        for table in page.extract_tables() or []:
            for row in table or []:
                if not row or len(row) < 11:
                    continue
                security_type = compact_text(row[1])
                security_type_lower = security_type.lower()
                if "หุ้นสามัญ" in security_type or "commonshares" in security_type_lower:
                    key = "common"
                elif "หุ้นบุริมสิทธิ" in security_type or "preferredshares" in security_type_lower:
                    key = "preferred"
                else:
                    continue

                values = []
                for index, cell in enumerate(row[2:11]):
                    values.append(numeric(cell, percent=index in (2, 5, 8)))
                if any(value is None for value in values):
                    continue

                found[key] = True
                buckets[key] = [a + b for a, b in zip(buckets[key], values)]

    # แบบฟอร์มมีทั้งสองแถวเสมอ ถ้าอ่านไม่เจอจึงปล่อยว่างเพื่อแยกจากค่าศูนย์จริง
    return {
        key: (buckets[key] if found[key] else [None] * 9)
        for key in buckets
    }


def extract_checked_labels(section, labels):
    section = clean_text(section)
    return ", ".join(label for label in labels if selected(re.escape(label), section))


def extract_selected_detail(section, label_pattern):
    """อ่านข้อความที่กรอกต่อท้ายตัวเลือกซึ่งถูกติ๊ก จนถึง checkbox/ข้อถัดไป"""
    section = clean_text(section)
    pattern = (
        rf"\(\s*[✓✔√]\s*\)\s*{label_pattern}\s*(.*?)"
        rf"(?=\(\s*(?:[✓✔√])?\s*\)|\b2\.[23]\b|$)"
    )
    match = re.search(pattern, section, re.I)
    return clean_text(match.group(1)) if match else ""


def parse_pdf(pdf_bytes, source_row, source_url, source_reference):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages_text = [page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages]
        text = "\n".join(pages_text)
        flat = clean_text(text)

        sec2 = section_between(text, 2, 3)
        sec7 = section_between(text, 7, 8)
        sec8 = section_between(text, 8, 9)
        sec10 = section_between(text, 10, 11)
        sec12 = section_between(text, 12, None)

        holdings = find_holding_rows(pdf)
        common = holdings["common"]
        preferred = holdings["preferred"]

        reference_no = first_match(r"เลขอ้างอิง\s*([0-9-]+)", flat)
        company_name = first_match(
            r"1\.\s*ช[^\s]*อกิจการ\s*บมจ\.\s*(.*?)\s*ช[^\s]*อย[^\s]*\s*หลักทรัพย์", flat
        )
        security_symbol = first_match(
            r"ช[^\s]*อย[^\s]*\s*หลักทรัพย์\s*([^\s]+).*?2\.\s*วัน", flat
        )

        transaction_type = extract_checked_labels(sec2, ["การได้มา", "การจำหน่าย"])
        # ข้อ 2.1: เก็บชื่อช่องทางเต็มตามแบบฟอร์ม
        channel_options = [
            ("ผ่าน ต.ล.ท. โดยผ่านบริษัทหลักทรัพย์", r"ผ่าน\s*ต\.ล\.ท\.\s*โดยผ่านบริษัทหลักทรัพย์"),
            ("ซื้อขายกันโดยตรง", r"ซื?้?อขายกันโดยตรง"),
            ("ซื้อหุ้นเพิ่มทุนเกินสิทธิ", r"ซื?้?อหุ้นเพิ?่?มทุนเกินสิทธิ"),
            ("ใช้สิทธิแปลงสภาพ", r"ใช้สิทธิแปลงสภาพ"),
            ("ทางมรดก", r"ทางมรดก"),
            ("อื่นๆ", r"อื?่?น\s*ๆ"),
        ]
        sec2_clean = clean_text(sec2)
        transaction_channel = ", ".join(
            name for name, pattern in channel_options if selected(pattern, sec2_clean)
        )

        broker_name = ""
        biglot = ""
        broker_match = re.search(
            r"บริษัทหลักทรัพย์(ไทย|ต่างประเทศ)\s+(.+?)(?=\n\s*\(\s*\)|\n\s*ซื้อขายกันโดยตรง)",
            sec2, re.S
        )
        if broker_match:
            broker_name = clean_text(broker_match.group(2))

        # ถ้าเลือกซื้อขายกันโดยตรงหรืออื่นๆ ให้นำรายละเอียดที่กรอกไปไว้ broker_name
        direct_detail = extract_selected_detail(sec2, r"ซื?้?อขายกันโดยตรง")
        other_detail = extract_selected_detail(sec2, r"อื?่?น\s*ๆ\s*\(โปรดระบุ\)")
        option_detail = direct_detail or other_detail
        if option_detail:
            broker_name = option_detail

        # แยกข้อความหลังคำว่า Biglot ออกจาก broker_name ทุกช่องทาง
        biglot_match = re.search(r"\bBiglot\b\s*(.*)$", broker_name, re.I)
        if biglot_match:
            biglot = clean_text(biglot_match.group(1))
            broker_name = clean_text(broker_name[:biglot_match.start()])

        # ข้อ 2.2 และ 2.3: ไม่เลือกให้เป็นค่าว่าง
        section_2_2_options = [
            ("การเริ่มต้นความเป็นบุคคลที่กระทำการร่วมกัน (concert party)",
             r"การเริ?มต้นความเป.?นบุคคลที?กระทำการร.?วมกัน\s*\(concert party\)"),
            ("การสิ้นสุดความเป็น concert party",
             r"การสิ?นสุดความเป.?น\s*concert party"),
        ]
        section_2_3_options = [
            ("การได้มาซึ่งนิติบุคคลตามมาตรา 258",
             r"การได้มาซึ?งนิติบุคคลตามมาตรา\s*258"),
            ("การสิ้นสุดความเป็นนิติบุคคลตามมาตรา 258",
             r"การสิ?นสุดความเป.?นนิติบุคคลตามมาตรา\s*258"),
        ]
        section_2_2 = ", ".join(
            name for name, pattern in section_2_2_options if selected(pattern, sec2_clean)
        )
        section_2_3 = ", ".join(
            name for name, pattern in section_2_3_options if selected(pattern, sec2_clean)
        )

        report_purpose = extract_checked_labels(
            sec7,
            ["รายงานตามมาตรา 246", "แก้ไขหรือเพิ่มเติมแบบ 246-2", "รายงานตามมาตรา 247"]
        )

        stock_types = extract_checked_labels(sec8, ["หุ้นสามัญ", "หุ้นบุริมสิทธิ"])
        convertible_types = extract_checked_labels(
            sec8,
            ["ใบสำคัญแสดงสิทธิที่จะซื้อหุ้น (warrant)", "หุ้นกู้แปลงสภาพ (CD)",
             "ใบแสดงสิทธิในการซื้อหุ้นเพิ่มทุนที่โอนสิทธิได้ (TSR)",
             "ใบสำคัญแสดงสิทธิอนุพันธ์ (DW)"]
        )
        other_type = first_match(r"8\.3\s*อื่นๆ\s*(?:\(ระบุ\))?\s*([^\n]*)", text)

        section_10 = extract_checked_labels(
            sec10,
            ["ความเป็นบุคคลที่กระทำการร่วมกัน (concert party)",
             "ความเป็นนิติบุคคลตามมาตรา 258",
             "การเริ่มต้นความเป็นบุคคลที่กระทำการร่วมกัน (concert party)",
             "การได้มาซึ่งนิติบุคคลตามมาตรา 258",
             "การสิ้นสุดความเป็นบุคคลที่กระทำการร่วมกัน (concert party)",
             "การสิ้นสุดความเป็นนิติบุคคลตามมาตรา 258"]
        )
        section_11 = "chain principle" if "มาตรา 247" in report_purpose else ""
        section_12 = extract_checked_labels(
            sec12,
            ["จะทำคำเสนอซื้อหลักทรัพย์ทั้งหมดของกิจการ",
             "จะลดสัดส่วนการถือหุ้นของกิจการลงให้ต่ำกว่าจุดที่ต้องทำคำเสนอซื้อ",
             "ได้รับผ่อนผันการทำคำเสนอซื้อจากสำนักงาน หรือจากคณะอนุกรรมการวินิจฉัยการเข้าถือหลักทรัพย์เพื่อครอบงำกิจการ",
             "ได้รับยกเว้นการทำคำเสนอซื้อหลักทรัพย์", "อื่นๆ"]
        )

        transaction_date = first_match(r"2\.\s*วัน.*?รายงาน\s*(\d{2}/\d{2}/\d{4})", flat)
        submission_date = first_match(r"3\.\s*วัน.*?ก\.ล\.ต\.\s*(\d{2}/\d{2}/\d{4})", flat)
        highest_price = first_match(r"เป.?นวันแรก\)\s*([0-9,.]+)\s*บาท", flat)
        highest_price_date = first_match(r"เป.?นวันแรก\).*?บาท.*?(\d{2}/\d{2}/\d{4})", flat)
        reporter_name = first_match(r"5\.\s*ข้อมูล.*?รายงาน\s*ช[^\s]*อ\s*(.*?)\s*6\.", flat)
        contact_person = first_match(r"6\.\s*บุคคล.*?ก\.ล\.ต\.\(ถ้ามี\)\s*(.*?)\s*7\.", flat)

        # ========================================================
        # แบบภาษาอังกฤษ: ใช้โครงสร้างคอลัมน์เดียวกับแบบภาษาไทย
        # ========================================================
        is_english = bool(re.search(r"Report of the Acquisition|The business", flat, re.I))
        if is_english:
            reference_no = first_match(r"Reference\s*([0-9-]+)", flat)
            company_name = first_match(
                r"1\.\s*The business[’']?\s*(.*?)\s*Securities Code\s*:", flat, re.I
            )
            security_symbol = first_match(
                r"Securities Code\s*:\s*([^\s]+)", flat, re.I
            )
            transaction_date = first_match(
                r"2\.\s*Date of action resulting in reporting\s*(\d{2}/\d{2}/\d{4})", flat, re.I
            )
            submission_date = first_match(
                r"3\.\s*Date of filing this report to the SEC\s*(\d{2}/\d{2}/\d{4})", flat, re.I
            )

            transaction_type = extract_checked_labels(sec2, ["Acquisition", "Disposition"])
            english_channels = [
                ("Through the Stock Exchange of Thailand via a securities company",
                 r"Through the Stock Exchange of Thailand via a securities company"),
                ("Direct sale/purchase", r"Direct sale/purchase"),
                ("Subscription in excess of rights offering", r"Subscription in excess of rights offering"),
                ("Exercise of conversion rights", r"Exercise of conversion rights"),
                ("By way of inheritance", r"By way of inheritance"),
                ("Other", r"Other\s*\(please\s*specify\)"),
            ]
            transaction_channel = ", ".join(
                name for name, pattern in english_channels if selected(pattern, sec2_clean)
            )

            broker_name = ""
            biglot = ""
            english_broker = re.search(
                r"(?:Thai|Foreign)\s+Securities company\s+(.+?)"
                r"(?=\n\s*\(\s*\)|\n\s*Direct sale/purchase)",
                sec2, re.I | re.S
            )
            if english_broker:
                broker_name = clean_text(english_broker.group(1))

            direct_detail = extract_selected_detail(sec2, r"Direct sale/purchase")
            other_detail = extract_selected_detail(sec2, r"Other\s*\(please\s*specify\)")
            if direct_detail or other_detail:
                broker_name = direct_detail or other_detail

            biglot_match = re.search(r"\bBiglot\b\s*(.*)$", broker_name, re.I)
            if biglot_match:
                biglot = clean_text(biglot_match.group(1))
                broker_name = clean_text(broker_name[:biglot_match.start()])

            section_2_2 = extract_checked_labels(
                sec2,
                ["Commencement of status of a concert party",
                 "Termination of status of a concert party"]
            )
            section_2_3 = extract_checked_labels(
                sec2,
                ["Acquisition of a juristic person under Section 258",
                 "Termination of status of a juristic person under Section 258"]
            )

            report_purpose = extract_checked_labels(
                sec7,
                ["to file a report in accordance with Section 246",
                 "to amend or supplement Form 246-2",
                 "to file a report in accordance with Section 247"]
            )
            english_stock_options = [
                ("Common shares", r"Common(?:\s+shares)?"),
                ("Preferred shares", r"Preferred shares"),
            ]
            sec8_clean = clean_text(sec8)
            stock_types = ", ".join(
                name for name, pattern in english_stock_options if selected(pattern, sec8_clean)
            )
            convertible_types = extract_checked_labels(
                sec8,
                ["Warrants", "Convertible debentures (CD)",
                 "Transferable subscription rights (TSR)", "Derivative warrants (DW)"]
            )
            other_type = extract_selected_detail(sec8, r"Others\s*\(please\s*specify\)")

            section_10 = extract_checked_labels(
                sec10,
                ["concert party", "juristic person under Section 258",
                 "Commencement of status of concert party",
                 "Acquisition of a juristic person under Section 258",
                 "Termination of status of a concert party",
                 "Termination of status of a juristic person under Section 258"]
            )
            section_11 = "chain principle" if "Section 247" in report_purpose else ""
            section_12 = extract_checked_labels(
                sec12,
                ["will make a tender offer for all the securities of the business",
                 "will reduce its shareholdings in the business",
                 "has been granted a waiver from making the tender offer",
                 "is exempted from making a tender offer", "Others"]
            )

            sec4 = clean_text(section_between(text, 4, 5))
            highest_price = first_match(r"reporting\s+([0-9,.]+)\s+obligation", sec4, re.I)
            if not highest_price:
                highest_price = first_match(r"obligation\)\s*([0-9,.]+)\s*Baht", sec4, re.I)
            if not highest_price:
                price_match = re.search(r"([0-9,.]+)\s*Baht/Unit", sec4, re.I)
                highest_price = clean_text(price_match.group(1)) if price_match else ""
            highest_price_date = first_match(r"(\d{2}/\d{2}/\d{4})", sec4)
            reporter_name = first_match(
                r"5\.\s*Information about the reporting person\s*(.*?)\s*name\s*6\.", flat, re.I
            )
            contact_person = first_match(
                r"6\.\s*Person authorised to contact with the SEC\s*\(if\s*(.*?)\s*any\)\s*7\.", flat, re.I
            )

        result = {
            "source_row": source_row,
            "source_pdf_url": source_url,
            "source_reference_no": clean_text(source_reference),
            "reference_no": reference_no,
            "form_type": "246-2",
            "company_name": company_name,
            "security_symbol": security_symbol,
            "transaction_date": transaction_date,
            "transaction_type": transaction_type,
            "transaction_channel": transaction_channel,
            "broker_name": broker_name,
            "biglot": biglot,
            "section_2_2": section_2_2,
            "section_2_3": section_2_3,
            "submission_date": submission_date,
            "highest_price_90_days": highest_price,
            "highest_price_date": highest_price_date,
            "reporter_name": reporter_name,
            "contact_person": contact_person,
            "report_purpose": report_purpose,
            "section_8_1_stock_type": stock_types,
            "section_8_2_convertible_type": convertible_types,
            "section_8_3_other_type": other_type,
            "section_10": section_10,
            "section_11": section_11,
            "section_12": section_12,
            "reference_match": str(clean_text(source_reference) == reference_no) if source_reference else "",
            "extraction_status": "success",
            "error_message": "",
        }

        holding_names = [
            "before_units", "before_voting_rights", "before_percent",
            "transaction_units", "transaction_voting_rights", "transaction_percent",
            "after_units", "after_voting_rights", "after_percent"
        ]
        for prefix, values in (("common", common), ("preferred", preferred)):
            for name, value in zip(holding_names, values):
                result[f"{prefix}_{name}"] = value

        return result


def make_pdf_session():
    pdf_session = requests.Session()
    retries = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    pdf_session.mount("https://", HTTPAdapter(max_retries=retries))
    pdf_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119 Safari/537.36",
        "Accept": "application/pdf,*/*",
        "Referer": "https://market.sec.or.th/"
    })
    return pdf_session


def make_pdf_browser(download_folder):
    """เปิด Chrome เพียงครั้งเดียว ใช้เป็น fallback เมื่อ requests ถูก SEC ตอบ 403"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1280,900")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": os.path.abspath(download_folder),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True,
    })

    browser = None
    chrome_error = None

    try:
        chrome_service = ChromeService(ChromeDriverManager().install())
        browser = webdriver.Chrome(service=chrome_service, options=chrome_options)
        print("เปิด Chrome สำเร็จ")
    except Exception as error:
        chrome_error = str(error)
        print(f"เปิด Chrome ไม่สำเร็จ กำลังลอง Microsoft Edge: {error}")

    if browser is None:
        edge_options = EdgeOptions()
        edge_options.add_argument("--headless=new")
        edge_options.add_argument("--disable-gpu")
        edge_options.add_argument("--no-sandbox")
        edge_options.add_argument("--disable-dev-shm-usage")
        edge_options.add_argument("--window-size=1280,900")
        edge_options.add_experimental_option("prefs", {
            "download.default_directory": os.path.abspath(download_folder),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "safebrowsing.enabled": True,
        })
        try:
            edge_service = EdgeService(EdgeChromiumDriverManager().install())
            browser = webdriver.Edge(service=edge_service, options=edge_options)
            print("เปิด Microsoft Edge สำเร็จ")
        except Exception as edge_error:
            raise RuntimeError(
                "ไม่สามารถเปิดได้ทั้ง Chrome และ Edge | "
                f"Chrome: {chrome_error} | Edge: {edge_error}"
            ) from edge_error

    browser.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": os.path.abspath(download_folder),
    })
    return browser


def download_pdf_with_browser(browser, pdf_url, download_folder):
    """ดาวน์โหลด PDF ผ่าน Chrome และคืนค่าเป็น bytes"""
    # เก็บสถานะเดิมไว้แทนการลบไฟล์ เพราะ Chrome อาจล็อก downloads.htm บน Windows
    before = {}
    for name in os.listdir(download_folder):
        path = os.path.join(download_folder, name)
        try:
            if os.path.isfile(path):
                before[path] = (os.path.getmtime(path), os.path.getsize(path))
        except (PermissionError, OSError):
            pass

    browser.get(pdf_url)
    deadline = time.time() + SELENIUM_DOWNLOAD_TIMEOUT

    while time.time() < deadline:
        names = os.listdir(download_folder)
        downloading = any(name.endswith((".crdownload", ".tmp")) for name in names)
        completed = []
        for name in names:
            if name.lower().endswith((".crdownload", ".tmp", ".htm", ".html")):
                continue
            path = os.path.join(download_folder, name)
            try:
                current = (os.path.getmtime(path), os.path.getsize(path))
                if path not in before or current != before[path]:
                    completed.append(path)
            except (PermissionError, OSError):
                continue

        if completed and not downloading:
            completed.sort(key=os.path.getmtime, reverse=True)
            for candidate in completed:
                try:
                    with open(candidate, "rb") as file:
                        content = file.read()
                    if content.startswith(b"%PDF"):
                        return content
                except (PermissionError, OSError):
                    # ไฟล์อาจยังถูก Chrome ล็อกอยู่ รอแล้วลองใหม่
                    continue
        time.sleep(0.5)

    raise TimeoutError(f"Chrome ดาวน์โหลด PDF ไม่สำเร็จภายใน {SELENIUM_DOWNLOAD_TIMEOUT} วินาที")



def extract_pdf_data():
    df_source = pd.read_csv(INPUT_CSV, dtype=str, encoding="utf-8-sig")
    required_source_columns = {"หลักทรัพย์", "PDF"}
    missing_source_columns = required_source_columns - set(df_source.columns)
    if missing_source_columns:
        raise ValueError(
            f"ไม่พบคอลัมน์ที่จำเป็นใน holder_change.csv: {sorted(missing_source_columns)}"
        )

    reference_column = "หมายเลข" if "หมายเลข" in df_source.columns else None
    security_normalized = df_source["หลักทรัพย์"].fillna("").str.strip().str.upper()
    pdf_available = df_source["PDF"].notna() & df_source["PDF"].str.strip().ne("")

    if TARGET_SECURITIES is None:
        # รันทุกหลักทรัพย์ที่มี URL ในคอลัมน์ PDF
        work = df_source[pdf_available].copy()
        selected_mode = "ทุกหลักทรัพย์"
    else:
        # ปรับค่าที่ระบุให้เป็นตัวพิมพ์ใหญ่และตัดช่องว่างก่อนเทียบ
        target_normalized = {
            str(security).strip().upper()
            for security in TARGET_SECURITIES
        }
        work = df_source[
            security_normalized.isin(target_normalized) & pdf_available
        ].copy()
        selected_mode = f"เฉพาะ {sorted(target_normalized)}"
    if MAX_FILES is not None:
        work = work.head(MAX_FILES)

    selected_urls = set(work["PDF"].map(clean_text))
    print(
        f"โหมดการรัน: {selected_mode} | "
        f"พบ {len(work):,} แถวที่มี PDF จากทั้งหมด {len(df_source):,} แถว"
    )

    # อ่าน checkpoint เดิมเพื่อรันต่อได้
    results = []
    completed_urls = set()
    if os.path.exists(CHECKPOINT_CSV):
        checkpoint = pd.read_csv(CHECKPOINT_CSV, dtype=str, encoding="utf-8-sig")
        required_schema = {
            "source_pdf_url", "extraction_status",
            "biglot", "section_2_2", "section_2_3"
        }
        if required_schema.issubset(checkpoint.columns):
            # เก็บ checkpoint เฉพาะ URL ของ BCP/BCPG ที่อยู่ในงานรอบนี้
            checkpoint = checkpoint[
                checkpoint["source_pdf_url"].fillna("").map(clean_text).isin(selected_urls)
            ].copy()
            results = checkpoint.to_dict("records")
            completed_urls = set(checkpoint.loc[checkpoint["extraction_status"] == "success", "source_pdf_url"])
            print(f"พบ checkpoint เดิม {len(checkpoint):,} แถว; สำเร็จแล้ว {len(completed_urls):,} URL")
        else:
            print("โครงสร้าง checkpoint เดิมไม่ตรงกับเวอร์ชันใหม่: จะ Extract ใหม่ทั้งหมด")

    pdf_session = make_pdf_session()
    browser = None
    browser_download_folder = tempfile.mkdtemp(prefix="sec_pdf_")
    total = len(work)

    try:
      for count, (source_index, row) in enumerate(work.iterrows(), start=1):
        pdf_url = clean_text(row["PDF"])
        source_reference = row.get(reference_column, "") if reference_column else ""

        if pdf_url in completed_urls:
            continue

        try:
            response = pdf_session.get(pdf_url, timeout=(20, 90), verify=False)

            if response.status_code == 403:
                if browser is None:
                    print("SEC ตอบ 403: เริ่มใช้ Chrome/Selenium เป็น fallback")
                    browser = make_pdf_browser(browser_download_folder)
                pdf_bytes = download_pdf_with_browser(browser, pdf_url, browser_download_folder)
            else:
                response.raise_for_status()
                pdf_bytes = response.content

            if not pdf_bytes.startswith(b"%PDF"):
                raise ValueError(f"URL ไม่ได้ส่งกลับไฟล์ PDF; content-type={response.headers.get('content-type', '')}")

            parsed = parse_pdf(pdf_bytes, source_index, pdf_url, source_reference)
            results.append(parsed)
            completed_urls.add(pdf_url)
            print(f"[{count:,}/{total:,}] OK {parsed['reference_no']} {parsed['security_symbol']}")

        except Exception as error:
            error_row = {column: "" for column in OUTPUT_COLUMNS}
            error_row.update({
                "source_row": source_index,
                "source_pdf_url": pdf_url,
                "source_reference_no": source_reference,
                "extraction_status": "error",
                "error_message": str(error)[:500]
            })
            # ลบ error เดิมของ URL เดียวกันก่อนเพิ่มอันล่าสุด
            results = [item for item in results if item.get("source_pdf_url") != pdf_url]
            results.append(error_row)
            print(f"[{count:,}/{total:,}] ERROR {source_reference}: {error}")

        if count % SAVE_EVERY == 0:
            pd.DataFrame(results).reindex(columns=OUTPUT_COLUMNS).to_csv(
                CHECKPOINT_CSV, index=False, encoding="utf-8-sig"
            )
            print(f"บันทึก checkpoint แล้ว {len(results):,} แถว")

        time.sleep(random.uniform(*REQUEST_DELAY))

    finally:
        if browser is not None:
            browser.quit()
        shutil.rmtree(browser_download_folder, ignore_errors=True)


    df_output = pd.DataFrame(results).reindex(columns=OUTPUT_COLUMNS)
    df_output = df_output.drop_duplicates(subset=["source_pdf_url"], keep="last")
    df_output = df_output.sort_values("source_row", na_position="last")
    df_output.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    df_output.to_csv(CHECKPOINT_CSV, index=False, encoding="utf-8-sig")

    df_errors = df_output[df_output["extraction_status"] != "success"]
    df_errors.to_csv(ERROR_CSV, index=False, encoding="utf-8-sig")

    print("-" * 60)
    print(f"บันทึกผลลัพธ์: {OUTPUT_CSV}")
    print(f"ทั้งหมด: {len(df_output):,} | สำเร็จ: {(df_output['extraction_status'] == 'success').sum():,} | ผิดพลาด: {len(df_errors):,}")

def reset_output_files():
    """ลบไฟล์ผลลัพธ์เดิมเพื่อเริ่มจากข้อมูลชุดปัจจุบัน"""
    if not RESET_OUTPUT_FILES:
        return
    os.makedirs(TARGET_FOLDER, exist_ok=True)
    for path in (INPUT_CSV, OUTPUT_CSV, CHECKPOINT_CSV, ERROR_CSV):
        if os.path.exists(path):
            os.remove(path)
            print(f"ลบไฟล์เดิม: {path}")


def main():
    os.makedirs(TARGET_FOLDER, exist_ok=True)
    reset_output_files()
    extract_web_table()
    extract_pdf_data()


if __name__ == "__main__":
    main()
