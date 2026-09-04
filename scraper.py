import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)


# 설정

NAVER_WEBTOON_URL = "https://comic.naver.com/webtoon"

SELECTORS = {
    # 메인 웹툰 목록
    "main_item": "li[class*='DailyListItem__item']",
    "main_title": "a[class*='ContentTitle__title_area'] span.text",
    "main_link": "a[class*='Poster__link']",
    # 별점
    "rating": "span[class*='Rating__star_area'] span",
    # 회차목록
    "episode_item": "li[class*='EpisodeListList__item']",
    "episode_title": "span[class*='EpisodeListList__title']",
    "episode_link": "a[class*='EpisodeListList__link']",
    # 관심
    "interest": "span[class*='UserAction__count']",
    # 좋아요
    "likes": "em.u_cnt._count",
    # 좋아요 영역
    "like_button": "a.u_likeit_list_btn[data-type='like']",
}


# 크롬 설정


def create_driver():
    options = Options()

    options.page_load_strategy = "eager"

    # 백그라운드 실행
    options.add_argument("--headless=new")

    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # 불필요한 요소 제거
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(20)

    return driver


# Selenium
def get_text(driver, selector, default="N/A"):
    try:
        element = driver.find_element(By.CSS_SELECTOR, selector)

        text = (element.text or "").strip()

        if text:
            return text

        # .text가 비어 있을 경우 JS로 가져오기
        text = driver.execute_script("return arguments[0].textContent || '';", element)

        text = (text or "").strip()

        return text if text else default

    except (NoSuchElementException, WebDriverException):
        return default


def get_first_text(driver, selectors, default="N/A"):
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)

            for element in elements:
                text = (element.text or "").strip()

                if text:
                    return text

                text = driver.execute_script(
                    "return arguments[0].textContent || '';", element
                )

                text = (text or "").strip()

                if text:
                    return text

        except Exception:
            continue

    return default


def get_attribute(driver, selector, attribute, default=""):
    try:
        element = driver.find_element(By.CSS_SELECTOR, selector)
        value = element.get_attribute(attribute)

        if value is None:
            return default

        return value.strip()

    except Exception:
        return default


# 좋아요 수


def get_like_count(driver, default="N/A"):
    """
    네이버 좋아요 수를 가져온다.

    현재 HTML:
    <em class="u_cnt _count">7,250</em>

    Selenium의 .text가 비어 있는 경우가 있어서
    textContent까지 확인한다.
    """

    selectors = [
        "em.u_cnt._count",
        "em[class*='u_cnt'][class*='_count']",
        "a.u_likeit_list_btn[data-type='like'] em._count",
    ]

    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)

            for element in elements:
                # 1. Selenium text
                text = (element.text or "").strip()

                if text:
                    return text

                # 2. JS textContent
                text = driver.execute_script(
                    """
                    return arguments[0].textContent || '';
                    """,
                    element,
                )

                text = (text or "").strip()

                if text:
                    return text

                # 3. innerText
                text = driver.execute_script(
                    """
                    return arguments[0].innerText || '';
                    """,
                    element,
                )

                text = (text or "").strip()

                if text:
                    return text

        except Exception:
            continue

    return default


# 페이지 이동
def safe_get(driver, url, wait_selector=None, timeout=10):
    try:
        driver.get(url)

        if wait_selector:
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except TimeoutException:
                pass

        return True

    except Exception:
        return False


# 웹툰 목록
def collect_webtoon_list(driver, max_count):
    print_log("웹툰 목록을 불러오는 중...")

    if not safe_get(driver, NAVER_WEBTOON_URL, SELECTORS["main_item"], timeout=10):
        print_log("웹툰 목록 페이지 접속 실패")
        return []

    items = driver.find_elements(By.CSS_SELECTOR, SELECTORS["main_item"])

    result = []

    for item in items:
        if len(result) >= max_count:
            break

        try:
            title_element = item.find_element(By.CSS_SELECTOR, SELECTORS["main_title"])

            link_element = item.find_element(By.CSS_SELECTOR, SELECTORS["main_link"])

            title = (title_element.text or "").strip()

            url = (link_element.get_attribute("href") or "").strip()

            if not title or not url:
                continue

            result.append(
                {
                    "title": title,
                    "url": url,
                }
            )

        except Exception:
            continue

    print_log(f"웹툰 {len(result)}개 발견")

    return result


# 회차 목록
def collect_webtoon_info(driver, webtoon, episode_count):
    title = webtoon["title"]
    url = webtoon["url"]

    print_log(f"[{title}] 시작")

    if not safe_get(driver, url, SELECTORS["episode_item"], timeout=10):
        print_log(f"[{title}] 페이지 접속 실패")

        return {"웹툰명": title, "URL": url, "평점": "N/A", "회차": []}

    # 평점

    rating = get_text(driver, SELECTORS["rating"], "N/A")

    # 회차 목록

    episode_items = driver.find_elements(By.CSS_SELECTOR, SELECTORS["episode_item"])

    episodes = []

    for item in episode_items:
        if len(episodes) >= episode_count:
            break

        try:
            title_element = item.find_element(
                By.CSS_SELECTOR, SELECTORS["episode_title"]
            )

            link_element = item.find_element(By.CSS_SELECTOR, SELECTORS["episode_link"])

            episode_title = (title_element.text or "").strip()

            episode_url = (link_element.get_attribute("href") or "").strip()

            if not episode_title or not episode_url:
                continue

            episodes.append(
                {
                    "회차": episode_title,
                    "URL": episode_url,
                }
            )

        except Exception:
            continue

    print_log(f"[{title}] 평점={rating}, 회차={len(episodes)}개")

    return {
        "웹툰명": title,
        "URL": url,
        "평점": rating,
        "회차": episodes,
    }


# 회차 상세정보


def collect_episode_data(driver, webtoon_title, episode):
    episode_title = episode["회차"]
    episode_url = episode["URL"]

    try:
        # 좋아요 버튼 로딩 대기
        if not safe_get(driver, episode_url, SELECTORS["like_button"], timeout=8):
            return {
                "웹툰명": webtoon_title,
                "회차": episode_title,
                "URL": episode_url,
                "관심수": "N/A",
                "좋아요수": "N/A",
            }

        # 좋아요 수 로딩 대기
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["likes"]))
            )
        except TimeoutException:
            pass

        # 관심 수

        interest = get_first_text(
            driver,
            [
                SELECTORS["interest"],
                "span[class*='UserAction'][class*='count']",
            ],
            "N/A",
        )

        # 좋아요 수
        likes = get_like_count(driver)

        return {
            "웹툰명": webtoon_title,
            "회차": episode_title,
            "URL": episode_url,
            "관심수": interest,
            "좋아요수": likes,
        }

    except Exception as e:
        print_log(f"[{webtoon_title}] {episode_title} 오류: {str(e)[:100]}")

        return {
            "웹툰명": webtoon_title,
            "회차": episode_title,
            "URL": episode_url,
            "관심수": "N/A",
            "좋아요수": "N/A",
        }


# 처리


def process_webtoon(webtoon, episode_count):
    driver = None

    try:
        driver = create_driver()

        info = collect_webtoon_info(driver, webtoon, episode_count)

        result = []

        episodes = info["회차"]

        for index, episode in enumerate(episodes, 1):
            data = collect_episode_data(driver, info["웹툰명"], episode)

            result.append(data)

            print_log(
                f"[{info['웹툰명']}] "
                f"{index}/{len(episodes)} "
                f"{episode['회차']} "
                f"좋아요={data['좋아요수']}"
            )

        return {
            "웹툰명": info["웹툰명"],
            "URL": info["URL"],
            "평점": info["평점"],
            "회차": result,
        }

    except Exception as e:
        print_log(f"[{webtoon['title']}] 전체 오류: {e}")

        return {
            "웹툰명": webtoon["title"],
            "URL": webtoon["url"],
            "평점": "N/A",
            "회차": [],
        }

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# 전체 크롤링
def crawl_webtoon(max_webtoons, episodes_per_webtoon, worker_count, output_file):

    start_time = time.time()

    try:
        # 웹툰 목록 수집용
        list_driver = create_driver()

        try:
            webtoons = collect_webtoon_list(list_driver, max_webtoons)

        finally:
            list_driver.quit()

        if not webtoons:
            raise Exception("웹툰 목록을 가져오지 못했습니다.")

        # 병렬처리
        results = []

        total = len(webtoons)

        print_log(f"병렬 크롤링 시작 (작업자 {worker_count}개)")

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(process_webtoon, webtoon, episodes_per_webtoon): webtoon
                for webtoon in webtoons
            }

            completed = 0

            for future in as_completed(futures):
                webtoon = futures[future]

                try:
                    result = future.result()

                    results.append(result)

                except Exception as e:
                    print_log(f"[{webtoon['title']}] 처리 실패: {e}")

                completed += 1

                update_progress(completed, total)

        # json 형식 저장

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        elapsed = time.time() - start_time

        print_log("")
        print_log("================================")
        print_log("크롤링 완료")
        print_log(f"웹툰 수: {len(results)}")
        print_log(f"소요 시간: {elapsed:.1f}초")
        print_log(f"저장 파일: {output_file}")
        print_log("================================")

        set_running(False)

        messagebox.showinfo(
            "완료",
            f"크롤링이 완료되었습니다.\n\n"
            f"웹툰: {len(results)}개\n"
            f"소요 시간: {elapsed:.1f}초\n"
            f"파일: {output_file}",
        )

    except Exception as e:
        print_log(f"치명적 오류: {e}")

        set_running(False)

        messagebox.showerror("오류", str(e))


# 로그
log_queue = Queue()


def print_log(message):
    log_queue.put(str(message))


def process_log_queue():
    try:
        while True:
            message = log_queue.get_nowait()

            log_text.insert(tk.END, message + "\n")

            log_text.see(tk.END)

    except Empty:
        pass

    root.after(100, process_log_queue)


# 진행률
def update_progress(current, total):

    def update():
        progress["maximum"] = total
        progress["value"] = current

        progress_label.config(text=f"{current} / {total}")

    root.after(0, update)


# 실행상태


def set_running(running):

    def update():

        if running:
            start_button.config(state=tk.DISABLED)

        else:
            start_button.config(state=tk.NORMAL)

    root.after(0, update)


# 크롤링 시작
def start_crawling():

    try:
        max_webtoons = int(webtoon_count_entry.get())

        episode_count = int(episode_count_entry.get())

        workers = int(worker_count_entry.get())

        output_file = output_file_entry.get().strip()

        if max_webtoons <= 0:
            raise ValueError("웹툰 개수는 1 이상이어야 합니다.")

        if episode_count <= 0:
            raise ValueError("회차 개수는 1 이상이어야 합니다.")

        if workers <= 0:
            raise ValueError("작업자 수는 1 이상이어야 합니다.")

        if workers > 5:
            raise ValueError("작업자 수는 최대 5개까지 권장합니다.")

        if not output_file:
            raise ValueError("JSON 파일명을 입력하세요.")

    except ValueError as e:
        messagebox.showerror("입력 오류", str(e))

        return

    log_text.delete("1.0", tk.END)

    progress["value"] = 0
    progress_label.config(text="0 / 0")

    set_running(True)

    thread = threading.Thread(
        target=crawl_webtoon,
        args=(
            max_webtoons,
            episode_count,
            workers,
            output_file,
        ),
        daemon=True,
    )

    thread.start()


# GUI
root = tk.Tk()

root.title("네이버 웹툰 크롤러")

root.geometry("700x600")

root.resizable(True, True)


# 설정 프레임
config_frame = ttk.LabelFrame(root, text="크롤링 설정", padding=10)

config_frame.pack(fill=tk.X, padx=10, pady=10)


# 웹툰 개수
ttk.Label(config_frame, text="웹툰 개수:").grid(
    row=0, column=0, padx=5, pady=5, sticky=tk.W
)

webtoon_count_entry = ttk.Entry(config_frame, width=10)

webtoon_count_entry.insert(0, "10")

webtoon_count_entry.grid(row=0, column=1, padx=5, pady=5)


# 회차 개수
ttk.Label(config_frame, text="웹툰당 회차:").grid(
    row=1, column=0, padx=5, pady=5, sticky=tk.W
)

episode_count_entry = ttk.Entry(config_frame, width=10)

episode_count_entry.insert(0, "10")

episode_count_entry.grid(row=1, column=1, padx=5, pady=5)


# 작업자 수
ttk.Label(config_frame, text="동시 작업자:").grid(
    row=2, column=0, padx=5, pady=5, sticky=tk.W
)

worker_count_entry = ttk.Entry(config_frame, width=10)

worker_count_entry.insert(0, "3")

worker_count_entry.grid(row=2, column=1, padx=5, pady=5)


# JSON 파일명
ttk.Label(config_frame, text="JSON 파일:").grid(
    row=3, column=0, padx=5, pady=5, sticky=tk.W
)

output_file_entry = ttk.Entry(config_frame, width=40)

output_file_entry.insert(0, "naver_webtoon_data.json")

output_file_entry.grid(row=3, column=1, columnspan=2, padx=5, pady=5, sticky=tk.W)


# 시작버튼

start_button = ttk.Button(root, text="크롤링 시작", command=start_crawling)

start_button.pack(pady=5)


# 진행률

progress_frame = ttk.Frame(root)

progress_frame.pack(fill=tk.X, padx=10, pady=5)

progress = ttk.Progressbar(progress_frame, mode="determinate")

progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

progress_label = ttk.Label(progress_frame, text="0 / 0", width=10)

progress_label.pack(side=tk.RIGHT, padx=5)


# 로그

log_frame = ttk.LabelFrame(root, text="로그", padding=5)

log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

log_text = tk.Text(log_frame, wrap=tk.WORD)

log_text.pack(fill=tk.BOTH, expand=True)


# 로그 업데이트

root.after(100, process_log_queue)

root.mainloop()
