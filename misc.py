import logging
import csv
import os
import httpx
from selenium import webdriver
import re


def extract_group_id(url):
    match = re.search(r"/groups/(\d+)/", url)
    if match:
        return match.group(1)
    else:
        return None


def get2fa(token: str):
    url = f"https://2fa.live/tok/{token.replace(' ', '')}"

    headers = {
        "accept": "*/*",
        "accept-language": "de,de-DE;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-requested-with": "XMLHttpRequest",
        "referrer": "https://2fa.live/",
    }

    headers[
        "user-agent"
    ] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"  # Replace with your desired user-agent

    response = httpx.get(url, headers=headers)
    return response.json()["token"]


def set_title(title):
    if os.name == "nt":  # Windows
        os.system(f"title {title}")
    elif os.name == "posix":  # Linux, Unix, MacOS
        print(f"\033]0;{title}\007")  # ANSI escape code for setting the terminal title


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = "%(asctime)s - %(levelname)s - %(message)s"  # type: ignore # (%(filename)s:%(lineno)d)

    FORMATS = {
        logging.DEBUG: grey + format + reset,  # type: ignore
        logging.INFO: grey + format + reset,  # type: ignore
        logging.WARNING: yellow + format + reset,  # type: ignore
        logging.ERROR: red + format + reset,  # type: ignore
        logging.CRITICAL: bold_red + format + reset,  # type: ignore
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%H:%M:%S")
        return formatter.format(record)


class Account:
    def __init__(
        self,
        email="",
        password="",
        twofa_secret="",
        status="",
        verification="",
        name="",
    ):
        self.email = email
        self.password = password
        self.twofa_secret = twofa_secret
        self.status = status
        self.verification = verification
        self.name = name


def read_account_csv(file_path):
    accs = []
    default_header = [
        "email",
        "password",
        "twofa_secret",
        "status",
        "verification",
        "name",
    ]

    try:
        with open(file_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            try:
                first_row = next(reader)
            except StopIteration:
                # File is empty
                return []

            # Check if the first row is a header
            if any(field.strip().lower() in default_header for field in first_row):
                header = [h.strip().lower() for h in first_row]
            else:
                # No header, use the default and reprocess the first row as data
                header = default_header
                accs.append(Account(*first_row[: len(default_header)]))

            for row in reader:
                # Handle rows with too many fields
                if len(row) > len(default_header):
                    print(f"Warning: Row with unexpected number of fields: {row}")
                    continue

                # Fill missing fields with empty strings
                row += [""] * (len(default_header) - len(row))
                user_data = dict(zip(header, row))
                user = Account(**user_data)
                accs.append(user)

    except Exception as e:
        print(f"An error occurred: {e}")
        return []

    return accs
