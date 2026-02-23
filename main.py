import argparse
import json
import logging
import os
import re
import sys
import time
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import selenium.webdriver.support.expected_conditions as EC
import yt_dlp
from selenium.common import TimeoutException
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.webdriver import By
from selenium.webdriver.support.wait import WebDriverWait
from seleniumbase import Driver


def create_folder(course_title, base_path):
    course_path = os.path.join(base_path, "courses", course_title)
    os.makedirs(course_path, exist_ok=True)
    return course_path


_CLEAN_RE = re.compile(r'[^\x00-\x7F]|[\n :/|?<>"\\]')

def clean_string(data: str) -> str:
    logging.debug("Cleaning string: " + data)
    data = data.encode('ascii', 'ignore').decode('ascii')
    return _CLEAN_RE.sub('-', data.replace('*', ''))


def truncate_title_to_fit_file_name(title, max_file_name_length=250):
    # the file name length should not be too long
    # truncate the title to accommodate the max used file extension length and lecture index prefix
    max_title_length = max_file_name_length - len(".mp4.part-Frag0000.part") - 3
    if len(title) > max_title_length:
        turncated_title = title[:max_title_length]
        logging.warning("Truncating title: " + turncated_title)
        return turncated_title
    return title


class TeachableDownloader:
    def __init__(self, verbose_arg=False, complete_lecture_arg=False, user_agent_arg=None, timeout_arg=10, output_dir_arg=None):
        self.driver = Driver(uc=True, headed=True)
        self.headers = {
            "User-Agent": user_agent_arg,
            "Origin": "https://player.hotmart.com",
            "Referer": "https://player.hotmart.com"
        }
        self.verbose = verbose_arg
        self._complete_lecture = complete_lecture_arg
        self.global_timeout = timeout_arg
        self.output_dir = os.path.abspath(output_dir_arg) if output_dir_arg else os.path.join(os.path.expanduser('~'), 'Downloads', 'Teachable-dl')

    def _load_progress(self, course_path):
        progress_file = os.path.join(course_path, ".progress.json")
        if os.path.exists(progress_file):
            with open(progress_file) as f:
                return set(json.load(f))
        return set()

    def _save_progress(self, course_path, completed_links):
        progress_file = os.path.join(course_path, ".progress.json")
        with open(progress_file, "w") as f:
            json.dump(list(completed_links), f)

    def check_elem_exists(self, by, selector, timeout):
        try:
            WebDriverWait(self.driver, timeout=timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except NoSuchElementException:
            return False
        except TimeoutException:
            return False
        except Exception:
            return False
        else:
            return True

    def bypass_cloudflare(self):
        if int(self.driver.capabilities["browserVersion"].split(".")[0]) < 115:
            return
        logging.info("Bypassing cloudflare")
        time.sleep(1)
        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            try:
                self.driver.find_element(
                    By.ID, "challenge-stage"
                ).click()  # make sure the challenge is focused
                self.driver.execute_script(
                    '''window.open("''' + self.driver.current_url + """","_blank");"""
                )  # open page in new tab
                input(
                    "\033[93mWarning: Bypassing Cloudflare\nplease click on the captcha checkbox if not done already "
                    "and press enter to continue (do not close any of the tabs)\033[0m"
                )
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch to first tab
                self.driver.close()  # close first tab
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch back to new tab
            except Exception as e:
                logging.error("Could not bypass cloudflare: " + str(e))
                return
        else:
            logging.info("No need to bypass cloudflare")
            return

    def _do_login(self, start_url, email, password, login_url, man_login_url):
        if man_login_url is None:
            if login_url:
                self.driver.get(login_url)
            else:
                try:
                    self.find_login(start_url)
                except Exception as e:
                    logging.error(f"Could not find login: {e}", exc_info=self.verbose)
            try:
                self.login(email, password)
            except Exception as e:
                logging.error(f"Could not login: {e}", exc_info=self.verbose)
                return False
        else:
            self.driver.get(start_url)
            while self.driver.current_url != man_login_url:
                time.sleep(3)
                logging.info(f"Waiting for manual navigation to: {man_login_url}")
        return True

    def run(self, course_url, email, password, login_url, man_login_url):
        logging.info("Starting login")
        if not self._do_login(course_url, email, password, login_url, man_login_url):
            return

        logging.info("Starting download of course: " + course_url)
        try:
            self.pick_course_downloader(course_url)
        except Exception as e:
            logging.error("Could not download course: " + course_url + " cause: " + str(e))

    def download_all_from_dashboard(self, dashboard_url, email, password, login_url, man_login_url):
        logging.info("Starting login for dashboard all-course extraction")
        if not self._do_login(dashboard_url, email, password, login_url, man_login_url):
            return

        # Now on dashboard, grab courses
        if self.driver.current_url != dashboard_url:
            self.driver.get(dashboard_url)
            
        time.sleep(5)
        # Handle "Load More" scrolling if necessary
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        anchors = self.driver.find_elements(By.TAG_NAME, 'a')
        course_links = []
        for a in anchors:
            href = a.get_attribute('href')
            if href and ('/courses/enrolled/' in href or '/courses/' in href and '/lectures/' not in href):
                course_links.append(href.split('/lectures/')[0]) 
                
        # Clean duplicates
        course_links = list(set(course_links))
        
        # Filter for actual enrolled course links
        enrolled = [link for link in course_links if '/courses/enrolled/' in link or '/courses/' in link]
        # TCM dashboards sometimes put /courses/1152300 directly
        
        logging.info(f"Found {len(enrolled)} enrolled courses on dashboard.")
        
        for url in enrolled:
            try:
                logging.info(f"Starting batch download process for extracted course: {url}")
                self.pick_course_downloader(url)
            except Exception as e:
                logging.error(f"Could not download course {url} cause: " + str(e))

    def run_batch(self, url_array, email, password, login_url, man_login_url):
        """
        This method handles batch downloading of courses.
        """
        logging.info("Starting login")
        if not self._do_login(url_array[0], email, password, login_url, man_login_url):
            return

        logging.info("Running batch download of courses ")
        for url in url_array:
            try:
                self.pick_course_downloader(url)
            except Exception as e:
                logging.error("Could not download course: " + url + " cause: " + str(e))

    def construct_sign_in_url(self, course_url):
        parsed_url = urlparse(course_url)
        # Replace the path with '/sign_in'
        sign_in_path = '/sign_in'
        fallback_url = urlunparse((parsed_url.scheme, parsed_url.netloc, sign_in_path, '', '', ''))
        return fallback_url

    def find_login(self, course_url):
        logging.info("Trying to find login")

        self.driver.implicitly_wait(self.global_timeout)
        self.driver.get(course_url)

        try:
            login_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.LINK_TEXT, "Login"))
            )
        except TimeoutException:
            logging.warning("Login button not found, navigating to fallback URL")
            fallback_url = self.construct_sign_in_url(course_url)
            self.driver.get(fallback_url)
        else:
            login_element.click()

    def login(self, email, password):
        logging.info("Logging in")

        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=15).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        # NEW LOGIC: Intercept SSO "Log in with password" step if it exists
        logging.debug("Checking for Teachable SSO 'Log in with password' button...")
        try:
            # First, give it a moment to render
            time.sleep(2)
            
            # The button is an anchor with href containing 'login/password'
            sso_anchors = self.driver.find_elements(By.TAG_NAME, 'a')
            sso_button = next((a for a in sso_anchors if a.get_attribute('href') and 'login/password' in a.get_attribute('href')), None)
            
            if sso_button:
                logging.info("Detected Teachable SSO login flow. Clicking 'Log in with a password'...")
                sso_button.click()
                
                # Wait for the next body stage to render
                WebDriverWait(self.driver, timeout=15).until(
                    EC.presence_of_element_located((By.TAG_NAME, 'body')))
                time.sleep(2)
        except Exception as e:
            logging.debug(f"SSO login intercept step skipped or failed: {e}")
            pass

        try:
            email_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.ID, "email")))
            password_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.ID, "password")))
            commit_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.NAME, "commit")))
                
            logging.debug("Filling in login form")
            email_element.click()
            email_element.clear()
            self.driver.execute_script(f"document.getElementById('email').value={{json.dumps(email)}}")

            password_element.click()
            password_element.clear()
            self.driver.execute_script(f"document.getElementById('password').value={{json.dumps(password)}}")

            commit_element.click()
            
        except TimeoutException:
            # Save debug information
            logging.error(f"Failed to find login elements on {self.driver.current_url}")
            try:
                debug_html = os.path.join(os.getcwd(), "login_error_debug.html")
                with open(debug_html, 'w', encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                logging.error(f"Saved page source to {debug_html} for debugging.")
                
                debug_img = os.path.join(os.getcwd(), "login_error_debug.png")
                self.driver.save_screenshot(debug_img)
                logging.error(f"Saved screenshot to {debug_img} for debugging.")
            except Exception:
                pass
            raise

        # Check for login error due to incorrect credentials
        logging.debug("Checking for login error")
        try:
            error_elements = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.toast, span.text-with-icon"))
            )
            for element in error_elements:
                if "Your email or password is incorrect" in element.text:
                    logging.error("Login failed: Incorrect email or password.")
                    return False
        except TimeoutException:
            # No error message found, continue
            pass

        # Check for new device challenge
        # input with name otp_code
        if self.check_elem_exists(By.NAME, "otp_code", timeout=self.global_timeout):
            # wait for user to enter code
            input(
                "\033[93mWarning: New device challenge\nplease enter the code sent to your email and press enter to "
                "continue\033[0m"
            )
        logging.info("Logged in, switching to course page")
        time.sleep(3)

    def pick_course_downloader(self, course_url):
        # Check if we are already on the course page
        if not self.driver.current_url == course_url:
            logging.info("Switching to course page")
            self.driver.get(course_url)
            if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
                self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=self.global_timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        # https://support.teachable.com/hc/en-us/articles/360058715732-Course-Design-Templates
        logging.info("Picking course downloader")
        if self.driver.find_elements(By.ID, "__next"):
            logging.info('Choosing __next format')
            self.download_course_simple(course_url)
        elif self.driver.find_elements(By.CLASS_NAME, "course-mainbar"):
            logging.info('Choosing course-mainbar format')
            self.download_course_classic(course_url)
        elif self.driver.find_elements(By.CSS_SELECTOR, ".block__curriculum"):
            logging.info('Choosing .block__curriculum format')
            self.download_course_colossal(course_url)
        else:
            logging.error("Downloader does not support this course template. Please open an issue on github.")

    def download_course_colossal(self, course_url):
        logging.info("Detected block course format")
        try:
            logging.info("Getting course title")
            course_title = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".course__title"))
            ).text
        except Exception as e:
            logging.warning("Could not get course title, using tab title instead")
            course_title = self.driver.title

        course_title = clean_string(course_title)
        course_path = create_folder(course_title, self.output_dir)

        logging.info("Saving course html")
        try:
            output_file = os.path.join(course_path, "course.html")
            with open(output_file, 'w+', encoding='utf-8') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Unhide all elements
        logging.info("Unhiding all elements")
        self.driver.execute_script('[...document.querySelectorAll(".hidden")].map(e=>e.classList.remove("hidden"))')

        chapter_idx = 1
        video_list = []
        sections = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".block__curriculum__section"))
        )

        for section in sections:
            chapter_title = section.find_element(By.CSS_SELECTOR, ".block__curriculum__section__title").text
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            chapter_idx += 1
            idx = 1

            section_items = section.find_elements(By.CSS_SELECTOR, ".block__curriculum__section__list__item__link")
            for section_item in section_items:
                lecture_link = section_item.get_attribute("href")

                lecture_title = section_item.find_element(By.CSS_SELECTOR,
                                                          ".block__curriculum__section__list__item__lecture-name").text
                lecture_title = clean_string(lecture_title)
                logging.info("Found lecture: " + lecture_title)

                truncated_lecture_title = truncate_title_to_fit_file_name(lecture_title)

                video_entity = {"link": lecture_link, "title": truncated_lecture_title, "idx": idx,
                                "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def download_course_classic(self, course_url):
        # self.driver.find_elements(By.CLASS_NAME, "course-mainbar")
        logging.info("Detected _mainbar course format")
        logging.debug("Getting course title")
        for selector in [
            ".course-title",
            "h1.heading",
            ".course-sidebar h1",
            "body > section > div.course-sidebar > div > h1",
            "body > section > div.course-sidebar > div > h2",
        ]:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, selector)
                if el.text.strip():
                    course_title = el.text.strip()
                    break
            except Exception:
                continue
        else:
            logging.warning("Could not get course title, using tab title instead")
            course_title = self.driver.title

        course_id = urlparse(course_url).path.split('/')[-1]
        if "TCM Security" in course_title or course_title == self.driver.title:
            course_title = f"{course_title}-{course_id}"

        logging.debug("Found course title: \"" + course_title + "\" starting cleaning of title string")
        course_title = clean_string(course_title)
        logging.info("Found course title: " + course_title)
        course_path = create_folder(course_title, self.output_dir)

        try:
            logging.debug("Saving course html")
            output_file = os.path.join(course_path, "course.html")
            with open(output_file, 'w+', encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Get course image

        try:
            image_element = self.driver.find_elements(By.CLASS_NAME, "course-image")
            logging.info("Found course image")
            image_link = image_element[0].get_attribute("src")
            image_link_hd = re.sub(r"/resize=.+?/", "/", image_link)
            # try to download the image using the modified link first
            response = requests.get(image_link_hd)
            if response.ok:
                # save the image to disk
                image_path = os.path.join(course_path, "course-image.jpg")
                with open(image_path, "wb") as f:
                    f.write(response.content)
                logging.info("Image downloaded successfully.")
            else:
                # try to download the image using the original link
                response = requests.get(image_link)
                if response.ok:
                    # save the image to disk
                    image_path = os.path.join(course_path, "course-image.jpg")
                    with open(image_path, "wb") as f:
                        f.write(response.content)
                    logging.info("Image downloaded successfully.")
                else:
                    # print a message indicating that the image download failed
                    logging.warning("Failed to download image.")
        except Exception as e:
            logging.warning("Could not find course image: " + str(e))
            pass

        chapter_idx = 1
        video_list = []
        sections = WebDriverWait(self.driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".course-section"))
        )
        for section in sections:
            chapter_title = section.find_element(By.CSS_SELECTOR, ".section-title").text
            chapter_title = clean_string(chapter_title)
            chapter_title = chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            chapter_idx += 1
            idx = 1

            section_items = section.find_elements(By.CSS_SELECTOR, ".section-item")
            for section_item in section_items:
                lecture_link = section_item.find_element(By.CLASS_NAME, "item").get_attribute("href")

                lecture_title = section_item.find_element(By.CLASS_NAME, "lecture-name").text
                lecture_title = clean_string(lecture_title)
                logging.info("Found lecture: " + lecture_title)

                truncated_lecture_title = truncate_title_to_fit_file_name(lecture_title)

                video_entity = {"link": lecture_link, "title": truncated_lecture_title, "idx": idx,
                                "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def get_course_title_next(self, course_url):
        if self.driver.current_url != course_url:
            self.driver.get(course_url)

        heading = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".heading")))
        course_title = heading.text

        course_title = clean_string(course_title)
        return course_title

    def download_course_simple(self, course_url):
        self.driver.implicitly_wait(2)
        logging.info("Detected next course format")
        course_title = self.get_course_title_next(course_url)
        logging.info("Found course title: " + course_title)
        course_path = create_folder(course_title, self.output_dir)

        output_file = os.path.join(course_path, "course.html")
        try:
            with open(output_file, 'w+', encoding='utf-8') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Download course image
        try:
            logging.info("Downloading course image")
            image_element = self.driver.find_element(By.XPATH, "//*[@id=\"__next\"]/div/div/div[2]/div/div[1]/img")
            logging.info("Found course image")
            image_link = image_element.get_attribute("src")
            # Save image
            image_path = os.path.join(course_path, "course-image.jpg")
            # send a GET request to the image link
            try:
                response = requests.get(image_link)
                # write the image data to a file
                with open(image_path, "wb") as f:
                    f.write(response.content)
                # print a message indicating that the image was downloaded
                logging.info("Image downloaded successfully.")
            except Exception as e:
                # print a message indicating that the image download failed
                logging.warning("Failed to download image:" + str(e))
        except Exception as e:
            logging.warning("Could not find course image: " + str(e))
            pass

        chapter_idx = 0
        video_list = []
        slim_sections = self.driver.find_elements(By.CSS_SELECTOR, ".slim-section")
        for slim_section in slim_sections:
            chapter_idx += 1
            bars = slim_section.find_elements(By.CSS_SELECTOR, ".bar")
            chapter_title = slim_section.find_element(By.CSS_SELECTOR, ".heading").text
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            try:
                not_available_element = WebDriverWait(slim_section, self.global_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".drip-tag")))
                logging.warning('Chapter "%s" not available, skipping', chapter_title)
                continue
            except TimeoutException:
                logging.info("Chapter is available")
                pass  # Element wasn't found so the chapter is available

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            idx = 1
            for bar in bars:
                video = bar.find_element(By.CSS_SELECTOR, ".text")
                link = video.get_attribute("href")
                # Remove new line characters from the title and replace spaces with -
                title = clean_string(video.text)
                logging.info("Found lecture: " + title)
                truncated_title = truncate_title_to_fit_file_name(title)
                video_entity = {"link": link, "title": truncated_title, "idx": idx, "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def download_videos_from_links(self, video_list):
        if not video_list:
            return

        course_path = os.path.dirname(video_list[0]["download_path"])
        completed_links = self._load_progress(course_path)
        download_tasks = []

        for video in video_list:
            if video["link"] in completed_links:
                logging.info(f"Skipping already processed lecture link: {video['title']}")
                continue

            if self.driver.current_url != video["link"]:
                logging.info("Navigating to lecture: " + video["title"])
                self.driver.get(video["link"])
                self.driver.implicitly_wait(self.global_timeout)
            logging.info("Processing lecture: " + video["title"])

            try:
                logging.info("Saving html")
                self.save_webpage_as_html(video["title"], video["idx"], video["download_path"])
            except Exception as e:
                logging.error("Could not save html: " + video["title"] + " cause: " + str(e), exc_info=self.verbose)

            try:
                logging.info("Downloading attachments")
                self.download_attachments(video["link"], video["title"], video["idx"], video["download_path"])
            except Exception as e:
                logging.warning("Could not download attachments: " + video["title"] + " cause: " + str(e))
            
            try:
                logging.debug("Trying to download video as an attachment")
                if self.download_video_file(video["title"], video["idx"], video["download_path"]):
                    completed_links.add(video["link"])
                    self._save_progress(course_path, completed_links)
                    continue

            except Exception as e:
                logging.debug("Could not download video as an attachment: " + video["title"] + " cause: " + str(e))

            video_iframes = self.driver.find_elements(By.XPATH, "//iframe[starts-with(@data-testid, 'embed-player')]")

            for i, iframe in enumerate(video_iframes):
                try:
                    logging.info("Switching to video frame")
                    self.driver.switch_to.frame(iframe)

                    script_text = self.driver.find_element(By.ID, "__NEXT_DATA__")
                    json_text = json.loads(script_text.get_attribute("innerHTML"))
                    link = json_text["props"]["pageProps"]["applicationData"]["mediaAssets"][0]["urlEncrypted"]

                    # Append -n to the video title if there are multiple iframes
                    video_title = video["title"] + ("-" + str(i + 1) if len(video_iframes) > 1 else "")

                    download_tasks.append({
                        "yt_link": link,
                        "video_title": video_title,
                        "video_idx": video["idx"],
                        "download_path": video["download_path"],
                        "original_lecture_link": video["link"]
                    })

                    self.driver.switch_to.default_content()

                except Exception as e:
                    self.driver.switch_to.default_content()
                    logging.warning("Could not find video: " + video["title"])
                    continue

            if self._complete_lecture:
                try:
                    logging.info("Completing lecture")
                    self.complete_lecture()
                except Exception as e:
                    logging.warning("Could not complete lecture: " + video["title"] + " cause: " + str(e))
            
            if not video_iframes:
                completed_links.add(video["link"])
                self._save_progress(course_path, completed_links)

        # Phase 2: Parallel downloads
        if download_tasks:
            logging.info(f"Starting Phase 2: Parallel download of {len(download_tasks)} videos...")
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = {
                    executor.submit(
                        self.download_video, 
                        task["yt_link"], 
                        task["video_title"], 
                        task["video_idx"], 
                        task["download_path"]
                    ): task 
                    for task in download_tasks
                }
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        future.result()
                        completed_links.add(task["original_lecture_link"])
                        self._save_progress(course_path, completed_links)
                    except Exception as e:
                        logging.error(f"Download failed for {task['video_title']}: {e}")

    def complete_lecture(self):
        # Complete lecture
        self.driver.switch_to.default_content()
        complete_buttons = self.driver.find_elements(By.ID, "lecture_complete_button")
        if complete_buttons:
            logging.info("Found complete button")
            complete_buttons[0].click()
            logging.info("Completed lecture")
            time.sleep(3)

    def download_video(self, link, title, video_index, output_path):
        out_file = os.path.join(output_path, "{:02d}-{}.mp4".format(video_index, title))
        if os.path.isfile(out_file):
            logging.info(f"Skipping already downloaded: {title}")
            return
            
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "concurrent_fragment_downloads": 5,
            "external_downloader": "ffmpeg",
            "external_downloader_args": {
                "ffmpeg_i": [
                    "-protocol_whitelist", "file,http,https,tcp,tls,crypto,m3u8",
                    "-thread_queue_size", "512",
                    "-reconnect", "1",
                    "-reconnect_streamed", "1",
                ]
            },
            "hls_prefer_native": False,
            "hls_use_mpegts": True,
            "writesubtitles": True,
            "subtitleslangs": ["en", "en-US", "en-GB"],
            "outtmpl": out_file,
            "verbose": self.verbose,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
        except Exception as e:
            logging.error("Could not download video: " + title + " cause: " + str(e))
                
    def download_video_file(self, title, video_index, output_path, timeout=300):
        video_title = "{:02d}-{}".format(video_index, title)

        # Grab the video attachments type video
        video_attachments = self.driver.find_elements(By.CLASS_NAME, "lecture-attachment-type-video")

        if not video_attachments:
            logging.debug(f"No video attachment found for lecture: {title}")
            return False

        video_attachment = video_attachments[0]
        video_links = video_attachment.find_elements(By.TAG_NAME, "a")

        if not video_links:
            logging.debug(f"No video link found for lecture: {title}")
            return False

        video_link = video_links[0]

        # Set the download directory for this file
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": output_path
        })
        # Get list of files before download
        files_before_download = set(os.listdir(output_path))

        # Click the link to trigger download
        video_link.click()

        # Wait for download to complete
        start_time = time.time()
        while True:
            files_after_download = set(os.listdir(output_path))

            # Find new files
            new_files = files_after_download - files_before_download

            if len(new_files) == 1 and not list(new_files)[0].endswith('.crdownload'):
                break
            
            if timeout > 0 and (time.time() - start_time) > timeout:
                logging.warning(f"Download timeout for lecture: {title}")
                return False
        
            time.sleep(1)

        latest_file = os.path.join(output_path, list(new_files)[0])
                
        # Determine the file extension
        _, extension = os.path.splitext(latest_file)
        
        # Create the new filename
        new_filename = f"{video_title}{extension}"
        new_filepath = os.path.join(output_path, new_filename)
        
        # Rename the file
        os.rename(latest_file, new_filepath)
        logging.info(f"Downloaded video file {new_filename}")
        return True
    
    def download_attachments(self, link, title, video_index, output_path):
        video_title = "{:02d}-{}".format(video_index, title)

        # Grab the video attachments type file
        video_attachments = self.driver.find_elements(By.CLASS_NAME, "lecture-attachment-type-file")
        # Get all links from the video attachments

        if video_attachments:
            video_links = video_attachments[0].find_elements(By.TAG_NAME, "a")

            output_path = os.path.join(output_path, video_title)
            os.makedirs(output_path, exist_ok=True)

            # Get href attribute from the first link
            if video_links:
                for video_link in video_links:
                    href = video_link.get_attribute("href")
                    file_name = video_link.text.strip() or os.path.basename(href)
                    file_path = os.path.join(output_path, file_name)
                    logging.info("Downloading attachment: " + file_name + " for video: " + title)
                    try:
                        response = requests.get(href, stream=True, timeout=30)
                        response.raise_for_status()
                        with open(file_path, "wb") as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        logging.info(f"Downloaded attachment: {file_name}")
                    except Exception as e:
                        logging.warning(f"Failed to download attachment {file_name}: {e}")
        else:
            logging.warning("No attachments found for video: " + title)

    def generate_html_dashboard(self):
        logging.info("Generating modern HTML dashboard viewer...")
        root_path = self.output_dir
        courses_dir = os.path.join(root_path, "courses")
        if not os.path.exists(courses_dir):
            logging.warning("No courses directory found, skipping HTML generation.")
            return

        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Teachable Offline Viewer</title>
    <style>
        :root {
            --bg-dark: #0f172a; --bg-card: #1e293b; --text-main: #f8fafc; --text-muted: #94a3b8;
            --accent: #3b82f6; --accent-hover: #2563eb; --border: #334155;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background: var(--bg-dark); color: var(--text-main); line-height: 1.6; padding: 2rem; }
        h1 { text-align: center; margin-bottom: 2rem; font-size: 2.5rem; color: #fff; text-shadow: 0 2px 4px rgba(0,0,0,0.5); }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.5rem; max-width: 1400px; margin: 0 auto; }
        .card { background: var(--bg-card); border-radius: 12px; border: 1px solid var(--border); overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; }
        .card:hover { transform: translateY(-4px); box-shadow: 0 12px 20px rgba(0,0,0,0.3); }
        .card-img { width: 100%; height: 180px; object-fit: cover; border-bottom: 1px solid var(--border); background: #000; }
        .card-content { padding: 1.5rem; }
        .card-title { font-size: 1.25rem; font-weight: 600; margin-bottom: 0.5rem; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .accordion { margin-top: 1rem; border-top: 1px solid var(--border); }
        .chapter-btn { background: transparent; color: var(--text-main); cursor: pointer; padding: 1rem 0; width: 100%; text-align: left; border: none; outline: none; transition: 0.3s; font-size: 1rem; font-weight: 500; display: flex; justify-content: space-between; align-items: center; }
        .chapter-btn:hover { color: var(--accent); }
        .chapter-btn::after { content: '+'; font-size: 1.5rem; transition: transform 0.3s; }
        .chapter-btn.active::after { transform: rotate(45deg); }
        .panel { padding: 0; background-color: rgba(0,0,0,0.2); max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; border-radius: 6px; }
        .panel-inner { padding: 0.5rem 1rem; }
        .lecture-link { display: block; color: var(--text-muted); text-decoration: none; padding: 0.5rem 0; font-size: 0.9rem; border-bottom: 1px solid rgba(255,255,255,0.05); transition: color 0.2s; word-break: break-all; }
        .lecture-link:last-child { border-bottom: none; }
        .lecture-link:hover { color: var(--text-main); padding-left: 0.25rem; }
        .icon { display: inline-block; width: 16px; margin-right: 8px; vertical-align: middle; }
    </style>
</head>
<body>
    <h1>My Courses</h1>
    <div class="grid">
"""

        for course_name in os.listdir(courses_dir):
            if course_name == "index.html": continue
            course_path = os.path.join(courses_dir, course_name)
            if not os.path.isdir(course_path): continue
            
            img_src = ""
            if os.path.exists(os.path.join(course_path, "course-image.jpg")):
                img_src = f"./{course_name}/course-image.jpg".replace("\\", "/")
            else:
                img_src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='100%25' height='100%25'%3E%3Crect width='100%25' height='100%25' fill='%23334155'/%3E%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' font-family='sans-serif' font-size='24px' fill='%2394a3b8'%3ENo Image%3C/text%3E%3C/svg%3E"

            html_content += f"""
        <div class="card">
            <img class="card-img" src="{img_src}" loading="lazy" alt="{course_name}">
            <div class="card-content">
                <h2 class="card-title">{course_name}</h2>
                <div class="accordion">"""

            chapters = [d for d in os.listdir(course_path) if os.path.isdir(os.path.join(course_path, d))]
            chapters.sort()
            for chapter in chapters:
                chapter_path = os.path.join(course_path, chapter)
                files = []
                for f in os.listdir(chapter_path):
                    if f.endswith(('.mp4', '.pdf', '.html', '.mkv', '.zip', '.txt')):
                        files.append(f)
                files.sort()
                
                if not files: continue
                
                html_content += f"""
                    <button class="chapter-btn">{chapter}</button>
                    <div class="panel">
                        <div class="panel-inner">"""
                for f in files:
                    file_href = f"./{course_name}/{chapter}/{f}".replace("\\", "/")
                    icon = "📄" if f.endswith('.pdf') else "🎥" if f.endswith(('.mp4', '.mkv')) else "🔗"
                    html_content += f"""
                            <a href="{file_href}" target="_blank" class="lecture-link"><span class="icon">{icon}</span>{f}</a>"""
                html_content += """
                        </div>
                    </div>"""

            html_content += """
                </div>
            </div>
        </div>"""

        html_content += """
    </div>
    <script>
        var acc = document.getElementsByClassName("chapter-btn");
        for (var i = 0; i < acc.length; i++) {
            acc[i].addEventListener("click", function() {
                this.classList.toggle("active");
                var panel = this.nextElementSibling;
                if (panel.style.maxHeight) {
                    panel.style.maxHeight = null;
                } else {
                    panel.style.maxHeight = panel.scrollHeight + "px";
                } 
            });
        }
    </script>
</body>
</html>"""
        
        index_file = os.path.join(courses_dir, "index.html")
        try:
            with open(index_file, "w", encoding="utf-8") as f:
                f.write(html_content)
            logging.info(f"Successfully generated HTML Dashboard viewer at {index_file}")
        except Exception as e:
            logging.error(f"Failed to generate HTML Dashboard: {e}")

    def save_webpage_as_html(self, title, video_index, output_path):
        output_file = os.path.join(output_path, "{:02d}-{}.html".format(video_index, title))
        with open(output_file, 'w+', encoding='utf-8') as f:
            f.write(self.driver.page_source)
        logging.info("Saved webpage as html: " + output_file)

    def save_webpage_as_pdf(self, title, video_index, output_path):
        output_file_pdf = os.path.join(output_path, "{:02d}-{}.pdf".format(video_index, title))
        self.driver.save_print_page(output_file_pdf)
        logging.info("Saved webpage as pdf: " + output_file_pdf)

    def clean_up(self):
        logging.info("Cleaning up")
        self.driver.quit()
        # Delete cookies.txt
        if os.path.exists("cookies.txt"):
            os.remove("cookies.txt")


def read_urls_from_file(file_path):
    urls = []
    try:
        with open(file_path, 'r') as file:
            urls = file.read().splitlines()
    except FileNotFoundError:
        logging.error(f"File not found: {file_path}")
    except IOError as e:
        logging.error(f"IOError reading file: {file_path}. Error: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error reading file: {file_path}. Error: {str(e)}")

    if urls:
        logging.info(f"Successfully read {len(urls)} URLs from file: {file_path}")
    else:
        logging.warning(f"No URLs found in file: {file_path}")

    return urls


def check_required_args(args):
    if args.email and args.password:
        return True
    elif args.man_login_url:
        return True
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Teachable-Dl', description='Download courses', )
    parser.add_argument("--url", required=False, help='URL of the course (or dashboard URL if using --all)')
    parser.add_argument("-e", "--email", required=False, help='Email of the account')
    parser.add_argument("-p", "--password", required=False, help='Password of the account')
    parser.add_argument("-o", "--output", required=False, help='Output directory for downloaded courses')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity level (repeat for more verbosity)')
    parser.add_argument('--complete-lecture', action='store_true', default=False,
                        help='Complete the lecture after downloading')
    parser.add_argument("--login_url", required=False, help='(Optional) URL to teachable SSO login page')
    parser.add_argument("--man_login_url", required=False,
                        help='Login manually and start downloading when this url is reached')
    parser.add_argument("-f", "--file", required=False, help='Path to a text file that contains URLs')
    parser.add_argument("--all", action='store_true', default=False, help='Download all enrolled courses from the dashboard')
    parser.add_argument("--user-agent", required=False, help='User agent to use when downloading videos',
                        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/116.0.0.0 Safari/537.36")
    parser.add_argument("-t", "--timeout", required=False, help='Timeout for selenium driver', default=10)
    args = parser.parse_args()
    verbose = False
    if args.verbose == 0:
        log_level = logging.WARNING
    elif args.verbose == 1:
        log_level = logging.INFO
    else:
        verbose = True
        log_level = logging.DEBUG

    logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')

    if not check_required_args(args):
        logging.error("Required arguments are missing. Choose email/password or manual login (man_login_url).")
        exit(1)

    downloader = TeachableDownloader(verbose_arg=verbose, complete_lecture_arg=args.complete_lecture,
                                     user_agent_arg=args.user_agent, timeout_arg=args.timeout, output_dir_arg=args.output)
    if args.all:
        if not args.url:
            logging.error("URL to the academy dashboard is required when using --all. (e.g. --url https://academy.tcm-sec.com/l/dashboard)")
            sys.exit(1)
        try:
            downloader.download_all_from_dashboard(dashboard_url=args.url, email=args.email, password=args.password, login_url=args.login_url, man_login_url=args.man_login_url)
            downloader.generate_html_dashboard()
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
    elif args.file:
        urls = read_urls_from_file(args.file)
        try:
            downloader.run_batch(urls, args.email, args.password, args.login_url, args.man_login_url)
            downloader.generate_html_dashboard()
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
    else:
        # Check if url argument is passed
        if not args.url:
            logging.error("URL is required")
            sys.exit(1)
        try:
            downloader.run(course_url=args.url, email=args.email, password=args.password, login_url=args.login_url,
                           man_login_url=args.man_login_url)
            downloader.generate_html_dashboard()
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
