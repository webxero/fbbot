from concurrent.futures import thread
import re
import threading
from typing import Tuple
from webbrowser import Chrome
import click
import asyncio
import undetected_chromedriver as uc
import time
import sys
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
import selenium.webdriver.support.expected_conditions as EC  # noqa
from selenium.webdriver import ActionChains
from selenium.webdriver.support.wait import WebDriverWait
import logging
from selenium.webdriver.common.keys import Keys
from misc import (
    CustomFormatter,
    Account,
    extract_group_id,
    get2fa,
    read_account_csv,
    set_title,
)
from undetected_chromedriver import Patcher
import queue
import os
import zipfile
import itertools

logger = logging.getLogger("FB")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

SCRAPER_ACCOUNT = None

ACCOUNTS: list[Account] = []
PROXIES = []
PROXY_CYCLE = None
SCRIPT: str = ""
GROUPS: list[str] = []
QUEUE: queue.Queue[str] = queue.Queue()
STOP_FLAG = False

COMPLETED_TASKS = 0
COMPLETED_TASKS_LOCK = threading.Lock()

FAILED = 0
FAILED_LOCK = threading.Lock()

TOTAL_TASKS = 0

# Create a lock to ensure thread-safe access to the counters
task_lock = threading.Lock()

logger.info("Patching chrome...")
options = uc.ChromeOptions()
driver = uc.Chrome(options=options, headless=True, patcher_force_close=True)

driver.quit()

time.sleep(2)

PATCHER: Patcher = Patcher(force=True, user_multi_procs=True)


@click.command()
@click.option(
    "--groups",
    help="TXT file with groups",
    required=True,
    type=click.Path(exists=True, file_okay=True, readable=True),
)
@click.option(
    "--script",
    help="TXT file with the script to send",
    required=True,
    type=click.Path(exists=True, file_okay=True, readable=True),
)
@click.option(
    "--accounts",
    help="CSV file with accounts. 1st account is scraper",
    required=True,
    type=click.Path(exists=True, file_okay=True, readable=True),
)
@click.option(
    "--proxies",
    help="TXT file of proxies in this format: username:pass@ip:port",
    required=True,
    type=click.Path(exists=True, file_okay=True, readable=True),
)
def startup(groups, script, accounts, proxies):
    """CLI tool that messages people from facebook groups"""
    set_title("FB Group MASS DM")

    global ACCOUNTS, GROUPS, SCRIPT, PROXIES, PROXY_CYCLE

    ACCOUNTS = read_account_csv(accounts)
    if ACCOUNTS.__len__() == 0:
        logger.fatal("No accounts found! Exiting...")
        sys.exit()

    with open(groups, "r") as file:
        GROUPS = [line.strip() for line in file]

    if GROUPS.__len__() == 0:
        logger.fatal("No groups found! Exiting...")
        sys.exit()

    with open(script, "r") as file:
        SCRIPT = file.read()

    if SCRIPT.__len__() == 0:
        logger.fatal("Script is empty! Exiting...")
        sys.exit()

    with open(proxies, "r") as file:
        for line in file:
            user_pass, ip_port = line.strip().split("@")
            username, password = user_pass.split(":")
            ip, port = ip_port.split(":")

            PROXIES.append(
                {
                    "username": username,
                    "password": password,
                    "ip": ip,
                    "port": int(port),
                }
            )

    if PROXIES.__len__() == 0:
        logger.fatal("No proxies! Exiting...")
        sys.exit()

    PROXY_CYCLE = itertools.cycle(PROXIES)

    PATCHER.patch() or quit(1)  # type: ignore
    asyncio.run(main())


def join_group(driver: webdriver.Chrome, group: str):
    driver.get(group)
    time.sleep(1)
    element = driver.find_element(By.TAG_NAME, "body")

    # Move to the element and then by the desired offset
    ActionChains(driver).move_to_element(element).click().perform()

    try:
        join = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//div[@aria-label='Join group']")
            )
        )
        if join.__len__() == 2:
            ActionChains(driver).move_to_element_with_offset(
                join[0], 10, 10
            ).click().perform()
    except:
        pass


# def scroll(driver: webdriver.Chrome) -> bool:
#     """True if reached bottom, otherwise false"""
#     scroll_script = """
#     var callback = arguments[0];
#     var lastHeight = document.body.scrollHeight;
#     var scrollAmount = 900; // Change this value to adjust the scroll increment

#     function smoothScrollAndCheck(newScrollAmount) {
#         window.scrollBy({
#             top: newScrollAmount,
#             left: 0,
#             behavior: 'smooth'
#         });

#         setTimeout(function() {
#             if (document.body.scrollHeight > lastHeight) {
#                 lastHeight = document.body.scrollHeight;
#                 callback(false);  // Not reached the bottom and content has increased
#             } else if (newScrollAmount < scrollAmount) {
#                 // If more scrolling steps are needed
#                 smoothScrollAndCheck(500);  // Next scroll step
#             } else {
#                 callback(true);  // Reached the bottom or no new content
#             }
#         }, 5000);  // Wait for 5 seconds after each scroll
#     }

#     smoothScrollAndCheck(scrollAmount);
# """

#     return driver.execute_async_script(scroll_script)


def scroll(driver: webdriver.Chrome, scroll_amount: int = 900):
    """Scrolls the page down by a fixed amount."""
    scroll_script = """
    window.scrollBy({ 
        top: arguments[0],  // Vertical scroll amount
        left: 0, 
        behavior: 'smooth' 
    });
    """
    driver.execute_script(scroll_script, scroll_amount)


def scraper():
    global QUEUE, STOP_FLAG, COMPLETED_TASKS, FAILED, TOTAL_TASKS, ACCOUNTS
    driver_names = {driver_info.name for driver_info in ACCOUNTS}

    for group in GROUPS:
        if SCRAPER_ACCOUNT is None:
            return

        driver = login(SCRAPER_ACCOUNT)

        if driver is None:
            logger.fatal("Scraper account (1st) is disabled, cant proceed")
            return
        
        with driver as driver:
            # start scraping, input values into the queue
            join_group(driver, group)

            with COMPLETED_TASKS_LOCK:
                COMPLETED_TASKS = 0
            with FAILED_LOCK:
                FAILED = 0

            TOTAL_TASKS = 0

            for _ in range(len(ACCOUNTS) - 1):
                QUEUE.put(group)

            ActionChains(driver).move_to_element_with_offset(
                wait(driver, (By.XPATH, "//div/span[text()='People']")), 10, -20
            ).pause(1).click().perform()

            element = wait(
                driver,
                (
                    By.XPATH,
                    "//a[contains(@href, '/groups/') and contains(@href, '/members/')]",
                ),
            )

            time.sleep(3)

            processed_members = set()

            # first time
            counter = 0  # needs to get to 25

            scroll(driver)
            time.sleep(3)
            scroll(driver)

            group_id = extract_group_id(element.get_attribute("href"))

            members = scrape_wait(
                driver, (By.CSS_SELECTOR, f'a[href*="/groups/{group_id}/user/"]')
            )

            filtered_members: list[str] = []
            for member in members:
                member_name = member.text
                member_url = str(member.get_attribute("href"))

                if (
                    member_name != ""
                    and "contributions/" not in member_url
                    and member_name not in driver_names
                ):
                    member_identifier = f"{member_name}|{member_url}"

                    if member_identifier not in processed_members and counter > 25:
                        filtered_members.append(member_url)
                        processed_members.add(member_identifier)
                    else:
                        processed_members.add(member_identifier)
                        counter += 1

            for member in filtered_members:
                QUEUE.put(member)

            TOTAL_TASKS += len(filtered_members)

            set_title(f"FB Group MASS DM: {COMPLETED_TASKS}/{TOTAL_TASKS}")

            while not STOP_FLAG:
                scroll(driver)

                time.sleep(5)
                scroll(driver)
                time.sleep(5)

                members = scrape_wait(
                    driver,
                    (By.CSS_SELECTOR, f'a[href*="/groups/{group_id}/user/"]'),
                )

                filtered_members: list[str] = []
                for member in members:
                    member_name = member.text
                    member_url = str(member.get_attribute("href"))

                    if (
                        member_name != ""
                        and "contributions/" not in member_url
                        and member_name not in driver_names
                    ):
                        member_identifier = f"{member_name}|{member_url}"

                        if member_identifier not in processed_members:
                            filtered_members.append(member_url)
                            processed_members.add(member_identifier)

                if len(filtered_members) == 0:
                    break
                
                for member in filtered_members:
                    QUEUE.put(member)

                TOTAL_TASKS += len(filtered_members)

                set_title(f"FB Group MASS DM: {COMPLETED_TASKS}/{TOTAL_TASKS}")

                time.sleep(4)
                time.sleep(4)

            logger.info(f"Done: {COMPLETED_TASKS}; Failed: {FAILED}")
            driver.quit()
            while TOTAL_TASKS != COMPLETED_TASKS:
                time.sleep(1)

    logger.info("Done!")


def messager(data: Account):
    global COMPLETED_TASKS, FAILED, QUEUE, STOP_FLAG, SCRIPT

    while not STOP_FLAG:
        task = QUEUE.get()

        driver = login(data)

        if driver is None:
            logger.error(f"{data.email} IS DISABLED")
            return
        
        with driver as driver:

            if "user" in task:
                driver.get(task)

                time.sleep(5)

                if "/checkpoint/" in driver.current_url:
                    logger.error(f"{data.name} suspended! returning..")
                    return

                element = driver.find_element(By.TAG_NAME, "body")

                # Move to the element and then by the desired offset
                ActionChains(driver).move_to_element(element).click().perform()
                time.sleep(2)

                try:
                    ActionChains(driver).move_to_element_with_offset(
                        wait(
                            driver,
                            (
                                By.XPATH,
                                "//*[contains(text(), 'Message') and not(name()='script')]",
                            ),
                        ),
                        5,
                        -5,
                    ).pause(1).click().perform()

                except:
                    logger.warning(f"{task} no message button!!")
                    with FAILED_LOCK:
                        FAILED += 1
                    QUEUE.task_done()
                    time.sleep(180)
                    continue

                time.sleep(7)
                try:
                    try:
                        cant_message = wait(
                            driver,
                            (
                                By.XPATH,
                                """//*[contains(text(), \"You can't message this account\")]""",
                            ),
                        )
                        if cant_message:
                            raise Exception
                    except:
                        pass
                    input_box = wait(driver, (By.XPATH, "//div[@contenteditable='true']"))
                    input_box.send_keys(SCRIPT)

                    time.sleep(5)

                    ActionChains(driver).send_keys_to_element(
                        input_box, Keys.ENTER
                    ).perform()

                    time.sleep(5)

                    try:
                        wait(driver, (By.XPATH, "//span[contains(text(), 'Sent')][last()]"))

                        with COMPLETED_TASKS_LOCK:
                            COMPLETED_TASKS += 1
                            set_title(f"FB Group MASS DM: {COMPLETED_TASKS}/{TOTAL_TASKS}")

                        logger.info(f"{task} successfully sent message!")
                    except:
                        logger.warning(f"{task} failed to send message!")
                        with FAILED_LOCK:
                            FAILED += 1

                    driver.get("https://facebook.com")

                    time.sleep(5)

                    driver.quit()

                    for _ in range(600):  # 1200 seconds total, in steps of 2 seconds
                        if STOP_FLAG:
                            break
                        time.sleep(2)

                    if STOP_FLAG:
                        print("Consumer stopping early.")
                except:
                    logger.warning(f"{task} no message button!!")
                    #driver.quit()
            else:
                join_group(driver, task)
                time.sleep(10)

            QUEUE.task_done()

        if STOP_FLAG is True:
            driver.quit()

    


def login(data: Account):
    global PROXY_CYCLE
    proxy = next(PROXY_CYCLE)  # type: ignore
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {
            "scripts": ["background.js"]
        },
        "minimum_chrome_version":"22.0.0"
    }
    """

    background_js = """
    var config = {
            mode: "fixed_servers",
            rules: {
            singleProxy: {
                scheme: "http",
                host: "%s",
                port: %d
            },
            bypassList: ["localhost"]
            }
        };

    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

    function callbackFn(details) {
        return {
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        };
    }

    chrome.webRequest.onAuthRequired.addListener(
                callbackFn,
                {urls: ["<all_urls>"]},
                ['blocking']
    );
    """ % (
        proxy["ip"],
        proxy["port"],
        proxy["username"],
        proxy["password"],
    )

    time.sleep(1)
    service = webdriver.ChromeService(executable_path=PATCHER.executable_path)
    options = webdriver.ChromeOptions()
    options.add_argument("--headles=new")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    pluginfile = f"proxy_auth_{data.email[:4]}.zip"

    with zipfile.ZipFile(pluginfile, "w") as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    options.add_extension(pluginfile)

    driver = webdriver.Chrome(service=service, options=options)

    driver.get("https://mbasic.facebook.com")
    driver.maximize_window()
    time.sleep(1.5)

    os.remove(pluginfile)

    try:
        consent = wait(
            driver,
            (By.XPATH, "//div/button[@name='accept_only_essential' and @value='1']"),
        )

        ActionChains(driver).move_to_element(consent).perform()
        time.sleep(1)
        consent.click()
        time.sleep(3)

    except:  # if no consent
        pass

    wait(driver, (By.NAME, "email")).send_keys(data.email)
    wait(driver, (By.NAME, "pass")).send_keys(data.password)
    time.sleep(1)
    wait(driver, (By.XPATH, "(//input[@type='submit'])[1]")).click()

    if "suspended" in driver.page_source:
        driver.quit()
        return None


    try:
        wait(driver, (By.NAME, "submit[Continue]")).click()
        driver.quit()
        return None
    except:
        pass  # acc locked

    try:
        input = wait(driver, (By.NAME, "approvals_code"))
        print(input)
        token = get2fa(data.twofa_secret)
        input.send_keys(token)
        time.sleep(5)
        wait(driver, (By.NAME, "submit[Submit Code]")).click()
        time.sleep(1)
        wait(driver, (By.NAME, "submit[Continue]")).click()
        time.sleep(1)
        wait(driver, (By.NAME, "submit[Continue]")).click()
        time.sleep(1)
        wait(driver, (By.NAME, "submit[This was me]")).click()
        time.sleep(1)
        wait(driver, (By.NAME, "submit[Continue]")).click()
        time.sleep(1)
    except Exception as exc:
        # doesn't have 2fa / acc not locked afterwards
        logger.error(exc)

    if "suspended" in driver.page_source:
        driver.quit()
        return None

    # name = wait(
    #     driver, (By.XPATH, """//*[contains(text(), "You're now interacting as")]""")
    # ).text.split("You're now interacting as ")[-1]
    return driver


def scrape_wait(driver: webdriver.Chrome, locator: Tuple[str, str]):
    return WebDriverWait(driver, 60 * 3).until(
        EC.presence_of_all_elements_located(locator)
    )


def wait(driver: webdriver.Chrome, locator: Tuple[str, str]):
    return WebDriverWait(driver, 10).until(EC.presence_of_element_located(locator))


async def main():
    global ACCOUNTS, STOP_FLAG, SCRAPER_ACCOUNT
    # with concurrent.futures.ThreadPoolExecutor() as executor:
    #     future_to_account = {executor.submit(login, acc): acc for acc in ACCOUNTS}
    #     for future in concurrent.futures.as_completed(future_to_account):
    #         acc = future_to_account[future]
    #         try:
    #             res = future.result()
    #             if res is None:
    #                 logging.warning(f"can't login to {res}!")
    #                 ACCOUNTS = [obj for obj in ACCOUNTS if obj.name != res]
    #                 continue
    #         except Exception as exc:
    #             print(f"{acc} generated an exception: {exc}")

    SCRAPER_ACCOUNT = ACCOUNTS[0]

    producer_thread = threading.Thread(target=scraper)
    producer_thread.start()

    for acc in ACCOUNTS[1:]:
        t = threading.Thread(target=messager, args=(acc,))
        t.daemon = True
        t.start()

    logger.info(f"Accounts {ACCOUNTS.__len__()}; Groups: {GROUPS.__len__()}")

    try:
        # Wait for the producer thread indefinitely
        while producer_thread.is_alive():
            producer_thread.join(timeout=1)
    except KeyboardInterrupt:
        print("Stopping...")
        STOP_FLAG = True

    input("back in main thread, messager threads probs still running")

    click.echo("main group scraper thread ended")


if __name__ == "__main__":
    startup()
